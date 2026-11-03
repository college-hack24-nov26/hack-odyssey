"""A sewer map (sewerbot.core.mapspec) as a MuJoCo world: textured wall, floor and ceiling surfaces, defect patches that carry a
perception label, and thin collision boxes behind the walls.

This is the same geometry tools/gen_world.py writes for Webots (one thin slab per wall segment of every pipe and chamber, a
polygonal chamber with holes where the pipes join), expressed as plain data so any backend can use it. The Webots path is not
touched; tests/test_pipe_world.py checks that the collision boxes here match the ones in a generated Webots world.

Frame: x east, y north, z up; every pipe's drivable floor is at z = 0 (see sewerbot/core/geometry.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from sewerbot.core.geometry import ring_panel
from sewerbot.core.mapspec import GOAL_COLORS, Edge, SewerMap
from tools.defect_textures import make_defect_asset
from tools.materials import TILE_M, ensure_textures

from ..tasks.base import Item, MeshItem

ROOT = Path(__file__).resolve().parents[2]
TEXTURE_DIR = ROOT / "sim" / "webots" / "textures"            # the base textures are generated once and cached here (git-ignored)
PATCH_LIFT = 0.004          # a defect patch sits this far inside the wall so it never z-fights with it
JOINT_SPACING = 2.5         # a visible pipe joint every 2.5 m (a hard negative for the detector)
MAX_EDGE = 0.45            # longest triangle edge of a surface (see Surface.subdivide)
THICK = 0.1                 # collision slab thickness
PAD = 0.003                 # slabs sit this far behind the wall surface so they never show through it
SURFACE_RGBA = {"concrete": (0.52, 0.5, 0.47, 1.0), "brick": (0.55, 0.36, 0.3, 1.0), "pvc": (0.55, 0.55, 0.5, 1.0), "floor": (0.34, 0.3, 0.26, 1.0)}
_TEXTURES: dict[str, np.ndarray] = {}


def base_textures() -> dict[str, np.ndarray]:
    if not _TEXTURES:
        _TEXTURES.update(ensure_textures(TEXTURE_DIR))
    return _TEXTURES


def png_bytes(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError("could not encode a texture")
    return buf.tobytes()


def rz_rx(yaw: float, roll: float) -> tuple[float, float, float, float]:
    """Quaternion (w, x, y, z) of Rz(yaw) @ Rx(roll)."""
    wz, zz = math.cos(yaw / 2), math.sin(yaw / 2)
    wx, xx = math.cos(roll / 2), math.sin(roll / 2)
    return (wz * wx, wz * xx, zz * xx, zz * wx)


class Surface:
    """Triangles with per-vertex texture coordinates; each triangle faces the `inside` point."""

    def __init__(self, max_edge: float = MAX_EDGE):
        self.pts: list = []
        self.uv: list = []
        self.max_edge = max_edge

    def tri(self, p, uv, inside=None):
        p = [np.asarray(x, float) for x in p]
        uv = list(uv)
        if inside is not None and float(np.dot(np.cross(p[1] - p[0], p[2] - p[0]), np.asarray(inside, float) - p[0])) < 0:
            p[1], p[2] = p[2], p[1]
            uv[1], uv[2] = uv[2], uv[1]
        self.pts += [tuple(map(float, x)) for x in p]
        self.uv += [tuple(map(float, u)) for u in uv]

    def quad(self, p00, p01, p11, p10, uv00, uv01, uv11, uv10, inside):
        """A quad as a grid of cells no longer than `max_edge` (MuJoCo lights each vertex and interpolates, so a long wall made
        of two triangles is lit only by how far its far corners are from the lamp). Cells make two clean triangles each."""
        p00, p01, p11, p10 = (np.asarray(p, float) for p in (p00, p01, p11, p10))
        uv = [np.asarray(x, float) for x in (uv00, uv01, uv11, uv10)]
        nu = max(1, math.ceil(max(np.linalg.norm(p01 - p00), np.linalg.norm(p11 - p10)) / self.max_edge))
        nv = max(1, math.ceil(max(np.linalg.norm(p10 - p00), np.linalg.norm(p11 - p01)) / self.max_edge))

        def at(a: float, b: float):           # a along p00 -> p01, b along p00 -> p10
            w = ((1 - a) * (1 - b), a * (1 - b), a * b, (1 - a) * b)
            return w[0] * p00 + w[1] * p01 + w[2] * p11 + w[3] * p10, w[0] * uv[0] + w[1] * uv[1] + w[2] * uv[2] + w[3] * uv[3]

        for i in range(nu):
            for j in range(nv):
                c = [at((i + da) / nu, (j + db) / nv) for da, db in ((0, 0), (1, 0), (1, 1), (0, 1))]
                self.tri([c[0][0], c[1][0], c[2][0]], [c[0][1], c[1][1], c[2][1]], inside)
                self.tri([c[0][0], c[2][0], c[3][0]], [c[0][1], c[2][1], c[3][1]], inside)

    def empty(self) -> bool:
        return not self.pts

    def subdivide(self, max_edge: float) -> None:
        """Split triangles until no edge is longer than `max_edge`. MuJoCo lights each vertex and interpolates, so a wall made of
        a few huge triangles would be lit only by how far its corners are from the lamp: it needs vertices near the vehicle."""
        out_p, out_uv = [], []
        stack = [(self.pts[i:i + 3], self.uv[i:i + 3]) for i in range(len(self.pts) - 3, -1, -3)]
        while stack:
            p, uv = stack.pop()
            e = [float(np.linalg.norm(np.subtract(p[(k + 1) % 3], p[k]))) for k in range(3)]
            k = int(np.argmax(e))
            if e[k] <= max_edge:
                out_p += p
                out_uv += uv
                continue
            a, b, c = k, (k + 1) % 3, (k + 2) % 3                          # split the longest edge a-b at its midpoint, keeping the winding
            mp = tuple((np.add(p[a], p[b]) / 2.0).tolist())
            muv = tuple((np.add(uv[a], uv[b]) / 2.0).tolist())
            stack.append(([mp, p[b], p[c]], [muv, uv[b], uv[c]]))
            stack.append(([p[a], mp, p[c]], [uv[a], muv, uv[c]]))
        self.pts, self.uv = out_p, out_uv

    def mesh(self, name: str, texture: str | None, rgba, label: str | None = None) -> MeshItem:
        self.subdivide(1.5 * self.max_edge)                                   # fans and panels with holes are plain triangles: cut the big ones
        n = len(self.pts)
        return MeshItem(name, np.array(self.pts, float), np.arange(n, dtype=int).reshape(-1, 3), np.array(self.uv, float), texture, tuple(rgba), label)


@dataclass
class PipeWorld:
    name: str
    meshes: list[MeshItem] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)            # collision boxes and goal posts
    assets: dict[str, bytes] = field(default_factory=dict)
    defects: list[dict] = field(default_factory=list)          # ground truth: id, type, edge, centre, size ...
    goals: dict[str, tuple[float, float]] = field(default_factory=dict)
    start: tuple[float, float, float] = (0.0, 0.0, 0.0)        # x, y, yaw of the entry
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    slabs: list[tuple] = field(default_factory=list)           # (centre xyz, size xyz, quat) of every collision box


class PipeBuilder:
    def __init__(self, m: SewerMap, defect_seed: int | None = None, pad: float = PAD):
        self.m, self.pad = m, pad
        self.base = base_textures()
        self.out = PipeWorld(m.name)
        for key, arr in self.base.items():
            self.out.assets[f"{key}.png"] = png_bytes(arr)
        self.n_slab = 0

    # ---------------------------------------------------------------- collision boxes
    def _slab(self, centre, size, quat) -> None:
        self.out.slabs.append((tuple(float(c) for c in centre), tuple(float(s) for s in size), tuple(float(q) for q in quat)))
        half = tuple(round(s / 2, 5) for s in size)
        self.out.items.append(Item(f"slab{self.n_slab}", "box", half, tuple(round(float(c), 5) for c in centre), quat=tuple(round(float(q), 6) for q in quat),
                                   rgba=(0.16, 0.2, 0.3, 0.15), hidden=True))
        self.n_slab += 1

    # ---------------------------------------------------------------- pipes
    def pipe(self, e: Edge) -> None:
        m, sec = self.m, e.section
        poly, n = sec.poly, len(sec.poly)
        A = np.array(m.nodes[e.a].pos)
        d = np.array(e.direction)
        right = np.array([d[1], -d[0]])
        fo = sec.fo

        def P(s, r, z, lift=0.0, nrm=(0.0, 0.0)):
            r, z = r + lift * nrm[0], z + lift * nrm[1]
            return np.array([A[0] + d[0] * s + right[0] * r, A[1] + d[1] * s + right[1] * r, fo + z])

        seg = np.hypot(*(np.roll(poly, -1, axis=0) - poly).T)
        t_cum = np.concatenate([[0.0], np.cumsum(seg)])
        wall, floor, joints = Surface(), Surface(), Surface()
        s0, s1 = e.s_min, e.s_max
        axis_mid = P((s0 + s1) / 2, 0, 0)
        for i in range(n):
            j = (i + 1) % n
            a, b = poly[i], poly[j]
            if seg[i] < 1e-9:
                continue
            if abs(a[1] + fo) < 1e-9 and abs(b[1] + fo) < 1e-9:
                floor.quad(P(s0, *a), P(s1, *a), P(s1, *b), P(s0, *b), (a[0] / TILE_M, s0 / TILE_M), (a[0] / TILE_M, s1 / TILE_M),
                           (b[0] / TILE_M, s1 / TILE_M), (b[0] / TILE_M, s0 / TILE_M), axis_mid)
                continue
            u0, u1 = t_cum[i] / TILE_M, t_cum[i + 1] / TILE_M
            wall.quad(P(s0, *a), P(s1, *a), P(s1, *b), P(s0, *b), (u0, s0 / TILE_M), (u0, s1 / TILE_M), (u1, s1 / TILE_M), (u1, s0 / TILE_M), axis_mid)
            tang = (b - a) / seg[i]
            nrm = (-tang[1], tang[0])                                         # inward (counter-clockwise polygon)
            sj = s0 + JOINT_SPACING
            while sj < s1 - 0.3:
                lo, hi = sj - 0.02, sj + 0.02
                joints.quad(P(lo, *a, 0.002, nrm), P(hi, *a, 0.002, nrm), P(hi, *b, 0.002, nrm), P(lo, *b, 0.002, nrm), (0, 0), (0, 1), (1, 1), (1, 0), axis_mid)
                sj += JOINT_SPACING
        for tag, surf, tex, kind in (("wall", wall, f"{e.material}.png", e.material), ("floor", floor, "floor.png", "floor")):
            if not surf.empty():
                self.out.meshes.append(surf.mesh(f"pipe_{e.id}_{tag}", tex, SURFACE_RGBA[kind]))
        if not joints.empty():
            self.out.meshes.append(joints.mesh(f"pipe_{e.id}_joints", None, (0.10, 0.095, 0.09, 1.0)))
        # collision: one thin box behind every wall segment
        mid = A + d * (s0 + s1) / 2
        yaw = math.atan2(d[1], d[0])
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            if seg[i] < 1e-9 or (abs(a[1] + fo) < 1e-9 and abs(b[1] + fo) < 1e-9):
                continue
            tr, tz = (b - a) / seg[i]
            out_r, out_z = tz, -tr                                             # outward normal in (r, z)
            off = THICK / 2 + self.pad
            cr = (a[0] + b[0]) / 2 + out_r * off
            cz = (a[1] + b[1]) / 2 + out_z * off
            theta = math.atan2(tz, -tr)
            yl = -cr                                                           # lateral offset in the pipe frame (y = left)
            self._slab((mid[0] - math.sin(yaw) * yl, mid[1] + math.cos(yaw) * yl, fo + cz), (s1 - s0 + 0.04, seg[i] + 0.02, THICK), rz_rx(yaw, theta))

    # ---------------------------------------------------------------- chambers
    def chamber(self, nid: str) -> None:
        m = self.m
        ch = m.chambers[nid]
        c = np.array(m.nodes[nid].pos)
        corners = ch.corners
        walls, floor, ceil = Surface(), Surface(), Surface()
        cen = np.array([c[0], c[1], 0.0])
        for i in range(len(corners)):
            a, b = corners[i], corners[(i + 1) % len(corners)]
            pa, pb = np.array([c[0] + a[0], c[1] + a[1], 0.0]), np.array([c[0] + b[0], c[1] + b[1], 0.0])
            uvs = [(0, 0), (a[0] / TILE_M, a[1] / TILE_M), (b[0] / TILE_M, b[1] / TILE_M)]
            floor.tri([cen, pa, pb], uvs, inside=cen + [0, 0, 1])
            ceil.tri([cen + [0, 0, ch.height], pa + [0, 0, ch.height], pb + [0, 0, ch.height]], uvs, inside=cen + [0, 0, ch.height - 1])
        mid_in = np.array([c[0], c[1], ch.height / 2])
        for k, face in enumerate(ch.faces):
            P0, P1 = corners[k - 1], corners[k]
            Lp = float(np.hypot(*(P1 - P0)))
            ud = (P1 - P0) / Lp
            hole, hz = None, 0.0
            if face.pipe:
                hole = m.edges[face.pipe].section
                hz = hole.fo
            verts, tris = ring_panel(0.0, Lp, -0.2, ch.height, hole, face.half_before, hz)
            W = [np.array([c[0] + P0[0] + ud[0] * u, c[1] + P0[1] + ud[1] * u, z]) for u, z in verts]
            for ia, ib, ic in tris:
                walls.tri([W[ia], W[ib], W[ic]], [(verts[i][0] / TILE_M, verts[i][1] / TILE_M) for i in (ia, ib, ic)], inside=mid_in)
        for tag, surf, tex, kind in (("walls", walls, "brick.png", "brick"), ("floor", floor, "floor.png", "floor"), ("ceiling", ceil, "concrete.png", "concrete")):
            if not surf.empty():
                self.out.meshes.append(surf.mesh(f"chamber_{nid}_{tag}", tex, SURFACE_RGBA[kind]))

        def slab(P0, ud, u0, u1, z0, z1):
            if u1 - u0 < 1e-3 or z1 - z0 < 1e-3:
                return
            uc, zc = (u0 + u1) / 2, (z0 + z1) / 2
            ox, oy = ud[1], -ud[0]                                              # outward normal
            off = THICK / 2 + self.pad
            self._slab((c[0] + P0[0] + ud[0] * uc + ox * off, c[1] + P0[1] + ud[1] * uc + oy * off, zc), (u1 - u0, THICK, z1 - z0), rz_rx(math.atan2(ud[1], ud[0]), 0.0))

        for k, face in enumerate(ch.faces):
            P0, P1 = corners[k - 1], corners[k]
            Lp = float(np.hypot(*(P1 - P0)))
            ud = (P1 - P0) / Lp
            if face.pipe:
                sec = m.edges[face.pipe].section
                hw, hu = sec.width / 2, face.half_before
                slab(P0, ud, 0, hu - hw, -0.2, ch.height)
                slab(P0, ud, hu + hw, Lp, -0.2, ch.height)
                slab(P0, ud, hu - hw, hu + hw, sec.top, ch.height)
            else:
                slab(P0, ud, 0, Lp, -0.2, ch.height)

    # ---------------------------------------------------------------- defects and goals
    def defect(self, d) -> None:
        m = self.m
        e = m.edges[d.edge]
        sec = e.section
        a = d.angle % 360
        t0 = sec.boundary_param(a)
        down = -1 if a < 90 else +1
        asset = make_defect_asset(d, self.base[e.material], e.material, t0, d.s, down)
        w, h = asset.size
        tc, sc = t0 + asset.center_off[0], d.s + asset.center_off[1]
        lo, hi = e.s_min + 0.02, e.s_max - 0.02
        sc = float(np.clip(sc, lo + h / 2, hi - h / 2))
        ts = list(np.linspace(tc - w / 2, tc + w / 2, 9))
        acc, perim = 0.0, sec.perimeter_len
        for i in range(len(sec.poly)):
            for off in (-perim, 0.0, perim):
                if tc - w / 2 < acc + off < tc + w / 2:
                    ts.append(acc + off)
            acc += float(np.hypot(*(sec.poly[(i + 1) % len(sec.poly)] - sec.poly[i])))
        ts = sorted(set(round(t, 6) for t in ts))
        A = np.array(m.nodes[e.a].pos)
        dv = np.array(e.direction)
        right = np.array([dv[1], -dv[0]])
        rows = (sc - h / 2, sc + h / 2)
        pts = []
        for t in ts:
            pt, nrm = sec.point_at(t)
            r, z = pt + nrm * PATCH_LIFT
            col = [np.array([*(A + dv * s + right * r), sec.fo + z]) for s in rows]
            pts.append((t, col))
        axis = np.array([A[0] + dv[0] * sc, A[1] + dv[1] * sc, sec.fo])
        surf = Surface()
        for (ta, ca), (tb, cb) in zip(pts[:-1], pts[1:]):
            ua, ub = (ta - (tc - w / 2)) / w, (tb - (tc - w / 2)) / w
            surf.quad(ca[0], ca[1], cb[1], cb[0], (ua, 0.0), (ua, 1.0), (ub, 1.0), (ub, 0.0), axis)
        tex = f"{d.id}.png"
        self.out.assets[tex] = png_bytes(asset.albedo)
        # MuJoCo reads image row 0 as the top of the texture while the patch's v axis grows along the pipe: flip so streaks run downward
        surf.uv = [(u, 1.0 - v) for u, v in surf.uv]
        self.out.meshes.append(surf.mesh(f"defect_{d.id}", tex, (0.2, 0.2, 0.2, 1.0), label=d.type))
        c_world = np.mean([p for _, c in pts for p in c], axis=0)
        self.out.defects.append({"id": d.id, "type": d.type, "subtype": d.subtype, "edge": d.edge, "severity": d.severity,
                                 "center": [round(float(x), 4) for x in c_world], "s": round(sc, 4), "size_t": round(w, 4), "size_s": round(h, 4),
                                 "angle": d.angle, "coverage": round(asset.coverage, 3)})

    def goal(self, g) -> None:
        ch = self.m.chambers[g.node]
        c = self.m.nodes[g.node].pos
        free = [f for f in ch.faces if f.pipe is None]
        if free:
            ang, rad = free[0].angle, 0.55 * ch.apothem
        else:
            cx, cy = ch.corners[0]
            ang, rad = math.atan2(cy, cx), 0.5 * math.hypot(cx, cy)
        x, y = c[0] + rad * math.cos(ang), c[1] + rad * math.sin(ang)
        col = GOAL_COLORS[g.color]
        self.out.goals[g.id] = (x, y)
        self.out.items.append(Item(f"goal_{g.id}", "cylinder", (0.035, 0.6), (round(x, 3), round(y, 3), 0.6), rgba=(col[0], col[1], col[2], 1.0), collide=False))
        self.out.items.append(Item(f"goal_{g.id}_top", "sphere", (0.09,), (round(x, 3), round(y, 3), 1.28), rgba=(col[0], col[1], col[2], 1.0), collide=False))


def build_pipe_world(m: SewerMap, pad: float = PAD) -> PipeWorld:
    """Everything a MuJoCo world needs for the sewer map `m`."""
    b = PipeBuilder(m, pad=pad)
    for nid in m.nodes:
        b.chamber(nid)
    for e in m.edges.values():
        b.pipe(e)
    for d in m.defects:
        b.defect(d)
    for g in m.goals.values():
        b.goal(g)
    start = m.nodes[m.start_node].pos
    yaw = 0.0
    target = next(iter(m.goals.values())).node if m.goals else None
    if target and target != m.start_node:
        route = m.route(m.start_node, target)
        yaw = math.atan2(m.nodes[route[1]].pos[1] - start[1], m.nodes[route[1]].pos[0] - start[0])
    b.out.start = (float(start[0]), float(start[1]), float(yaw))
    b.out.bounds = m.bounding_box(4.0)
    return b.out
