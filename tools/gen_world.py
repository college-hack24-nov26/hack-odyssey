"""Map (YAML) + rover profile -> Webots world (.wbt), baked defect textures and ground-truth JSON.

    python -m tools.gen_world maps/straight_t.yaml --rover mid_4wd
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np

from sewerbot.core.geometry import ring_panel
from sewerbot.core.mapspec import GOAL_COLORS, Edge, SewerMap, load_map
from sewerbot.core.profiles import PRESETS, RoverProfile, get_profile
from .defect_textures import make_defect_asset
from .materials import MATERIALS, TILE_M, ensure_textures

ROOT = Path(__file__).resolve().parents[1]
WEBOTS = ROOT / "sim" / "webots"
CLASS_COLOR = {"crack": (1, 0, 0), "leak": (0, 0, 1), "goal": (0, 1, 0)}   # recognitionColors
PATCH_LIFT = 0.004     # defect patch sits 4 mm inside the wall (avoids z-fighting)
JOINT_SPACING = 2.5    # visible pipe joints every 2.5 m (hard negatives for the detector)


def _compose_rot(yaw: float, roll: float) -> tuple[tuple[float, float, float], float]:
    """Rz(yaw) * Rx(roll) as a single Webots axis-angle (via quaternions)."""
    wz, zz = math.cos(yaw / 2), math.sin(yaw / 2)
    wx, xx = math.cos(roll / 2), math.sin(roll / 2)
    # q = qz (x) qx with qz = (wz; 0,0,zz), qx = (wx; xx,0,0)
    w = wz * wx
    x = wz * xx
    y = zz * xx          # from cross((0,0,zz), (xx,0,0)) = (0, zz*xx, 0)
    z = wx * zz
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-12:
        return (0.0, 0.0, 1.0), 0.0
    return (x / n, y / n, z / n), 2 * math.atan2(n, w)


def _pot(n: int, lo: int = 16) -> int:
    """Nearest power of two (>= lo)."""
    n = max(lo, int(n))
    up = 1 << (n - 1).bit_length()
    down = up >> 1
    return down if (n - down) < (up - n) and down >= lo else up


class Mesh:
    def __init__(self):
        self.pts: list[tuple[float, float, float]] = []
        self.uv: list[tuple[float, float]] = []
        self.tris: list[tuple[int, int, int]] = []

    def tri(self, p, uv, inside=None):
        p = [np.asarray(x, float) for x in p]
        uv = list(uv)
        if inside is not None:
            n = np.cross(p[1] - p[0], p[2] - p[0])
            if float(np.dot(n, np.asarray(inside, float) - p[0])) < 0:
                p[1], p[2] = p[2], p[1]
                uv[1], uv[2] = uv[2], uv[1]
        i = len(self.pts)
        self.pts += [tuple(map(float, x)) for x in p]
        self.uv += [tuple(map(float, u)) for u in uv]
        self.tris.append((i, i + 1, i + 2))

    def quad(self, p00, p01, p11, p10, uv00, uv01, uv11, uv10, inside):
        self.tri([p00, p01, p11], [uv00, uv01, uv11], inside)
        self.tri([p00, p11, p10], [uv00, uv11, uv10], inside)

    def empty(self) -> bool:
        return not self.tris

    def ifs(self, ind: str, crease: float = 0.0) -> str:
        pts = ", ".join(f"{x:.4f} {y:.4f} {z:.4f}" for x, y, z in self.pts)
        uvs = ", ".join(f"{u:.5f} {v:.5f}" for u, v in self.uv)
        idx = " ".join(f"{a} {b} {c} -1" for a, b, c in self.tris)
        return (f"IndexedFaceSet {{\n{ind}  coord Coordinate {{ point [ {pts} ] }}\n"
                f"{ind}  texCoord TextureCoordinate {{ point [ {uvs} ] }}\n"
                f"{ind}  coordIndex [ {idx} ]\n{ind}  texCoordIndex [ {idx} ]\n{ind}  creaseAngle {crease}\n{ind}}}")


def pbr(tex: str, rough: float, rough_map: str | None = None, ibl: float = 0.25) -> str:
    rm = f' roughnessMap ImageTexture {{ url [ "{rough_map}" ] }}' if rough_map else ""
    return (f'PBRAppearance {{ baseColorMap ImageTexture {{ url [ "{tex}" ] }} roughness {rough} metalness 0 '
            f'IBLStrength {ibl}{rm} }}')


def shape(app: str, geom: str, ind: str = "    ") -> str:
    return f"{ind}Shape {{\n{ind}  appearance {app}\n{ind}  geometry {geom}\n{ind}}}\n"


class WorldBuilder:
    def __init__(self, m: SewerMap, profile: RoverProfile, out: Path, ambient: float = 0.35, seed: int = 0):
        self.m, self.profile, self.out = m, profile, out
        self.ambient, self.seed = ambient, seed
        self.tex_dir = WEBOTS / "textures"
        self.base = ensure_textures(self.tex_dir)
        self.defect_dir = self.tex_dir / "defects" / m.name
        self.defect_dir.mkdir(parents=True, exist_ok=True)
        self.gt: list[dict] = []
        self.instances: dict[int, dict] = {}     # segmentation key -> {"id", "cls"}
        self.geom_id = 0

    def _instance(self, ident: str, cls: str) -> tuple[int, str]:
        """Allocate a unique segmentation colour. key = R + 256*G with R,G in 0..255 (blue stays 0)."""
        k = len(self.instances) + 1
        if k >= 65536:
            raise ValueError("too many recognisable objects")
        self.instances[k] = {"id": ident, "cls": cls}
        return k, f"{(k & 255) / 255:.6f} {((k >> 8) & 255) / 255:.6f} 0"

    # ------------------------------------------------------------- pipes
    def _pipe_solid(self, e: Edge) -> str:
        m, sec = self.m, e.section
        poly = sec.poly
        n = len(poly)
        A = np.array(m.nodes[e.a].pos)
        d = np.array(e.direction)
        right = np.array([d[1], -d[0]])
        fo = sec.fo

        def P(s, r, z, lift=0.0, nrm=(0.0, 0.0)):
            r, z = r + lift * nrm[0], z + lift * nrm[1]
            return np.array([A[0] + d[0] * s + right[0] * r, A[1] + d[1] * s + right[1] * r, fo + z])

        seg = np.hypot(*(np.roll(poly, -1, axis=0) - poly).T)
        t_cum = np.concatenate([[0.0], np.cumsum(seg)])
        wall, floor, joints = Mesh(), Mesh(), Mesh()
        s0, s1 = e.s_min, e.s_max
        axis_mid = P((s0 + s1) / 2, 0, 0)
        for i in range(n):
            j = (i + 1) % n
            a, b = poly[i], poly[j]
            if seg[i] < 1e-9:
                continue
            is_floor = abs(a[1] + fo) < 1e-9 and abs(b[1] + fo) < 1e-9
            if is_floor:
                floor.quad(P(s0, *a), P(s1, *a), P(s1, *b), P(s0, *b),
                           (a[0] / TILE_M, s0 / TILE_M), (a[0] / TILE_M, s1 / TILE_M),
                           (b[0] / TILE_M, s1 / TILE_M), (b[0] / TILE_M, s0 / TILE_M), axis_mid)
                continue
            u0, u1 = t_cum[i] / TILE_M, t_cum[i + 1] / TILE_M
            wall.quad(P(s0, *a), P(s1, *a), P(s1, *b), P(s0, *b),
                      (u0, s0 / TILE_M), (u0, s1 / TILE_M), (u1, s1 / TILE_M), (u1, s0 / TILE_M), axis_mid)
            tang = (b - a) / seg[i]
            nrm = (-tang[1], tang[0])                  # inward (ccw polygon)
            sj = s0 + JOINT_SPACING
            while sj < s1 - 0.3:
                lo, hi = sj - 0.02, sj + 0.02
                joints.quad(P(lo, *a, 0.002, nrm), P(hi, *a, 0.002, nrm), P(hi, *b, 0.002, nrm), P(lo, *b, 0.002, nrm),
                            (0, 0), (0, 1), (1, 1), (1, 0), axis_mid)
                sj += JOINT_SPACING

        tex = f"../textures/{e.material}.png"
        rough = MATERIALS[e.material]
        parts, bounds = [], []
        for name, mesh, app in (("wall", wall, pbr(tex, rough)),
                                ("floor", floor, pbr("../textures/floor.png", MATERIALS["floor"]))):
            if mesh.empty():
                continue
            parts.append(shape(app, mesh.ifs("    ")))
        # Collision uses boxes only (ODE's triangle-mesh contacts proved unstable): one thin slab per wall edge.
        mid = A + d * (s0 + s1) / 2
        yaw = math.atan2(d[1], d[0])
        slabs = []
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            if seg[i] < 1e-9 or (abs(a[1] + fo) < 1e-9 and abs(b[1] + fo) < 1e-9):
                continue
            tr, tz = (b - a) / seg[i]
            out_r, out_z = tz, -tr                       # outward normal in (r, z)
            thick = 0.1
            cr = (a[0] + b[0]) / 2 + out_r * thick / 2
            cz = (a[1] + b[1]) / 2 + out_z * thick / 2
            theta = math.atan2(tz, -tr)                  # slab y-axis direction in (y=-r, z)
            yl = -cr                                     # lateral offset in the pipe frame (y = left)
            wx_ = mid[0] - math.sin(yaw) * yl
            wy_ = mid[1] + math.cos(yaw) * yl
            ax, ang = _compose_rot(yaw, theta)           # Webots forbids nested transforms in boundingObject
            slabs.append(f"      Pose {{ translation {wx_:.4f} {wy_:.4f} {fo + cz:.4f} rotation {ax[0]:.6f} {ax[1]:.6f} {ax[2]:.6f} {ang:.6f} "
                         f"children [ Box {{ size {s1 - s0 + 0.04:.4f} {seg[i] + 0.02:.4f} {thick} }} ] }}")
        bounds.extend(slabs)
        bounds.append(f"      Pose {{ translation {mid[0]:.4f} {mid[1]:.4f} -0.05 rotation 0 0 1 {yaw:.5f} "
                      f"children [ Box {{ size {s1 - s0 + 0.04:.4f} {sec.floor_width:.4f} 0.1 }} ] }}")
        if not joints.empty():
            parts.append(shape('PBRAppearance { baseColor 0.10 0.095 0.09 roughness 0.95 metalness 0 IBLStrength 0.1 }',
                               joints.ifs("    ")))
        return (f'Solid {{\n  name "pipe_{e.id}"\n  children [\n{"".join(parts)}  ]\n'
                f'  boundingObject Group {{ children [\n{chr(10).join(bounds)}\n    ] }}\n}}\n')

    # ------------------------------------------------------------- chambers
    def _chamber_solid(self, nid: str) -> str:
        m = self.m
        ch = m.chambers[nid]
        c = np.array(m.nodes[nid].pos)
        corners = ch.corners
        walls, floor, ceil = Mesh(), Mesh(), Mesh()
        cen = np.array([c[0], c[1], 0.0])
        for i in range(len(corners)):
            a, b = corners[i], corners[(i + 1) % len(corners)]
            pa, pb = np.array([c[0] + a[0], c[1] + a[1], 0.0]), np.array([c[0] + b[0], c[1] + b[1], 0.0])
            floor.tri([cen, pa, pb], [(0, 0), (a[0] / TILE_M, a[1] / TILE_M), (b[0] / TILE_M, b[1] / TILE_M)],
                      inside=cen + [0, 0, 1])
            qa, qb, qc = cen + [0, 0, ch.height], pa + [0, 0, ch.height], pb + [0, 0, ch.height]
            ceil.tri([qa, qb, qc], [(0, 0), (a[0] / TILE_M, a[1] / TILE_M), (b[0] / TILE_M, b[1] / TILE_M)],
                     inside=cen + [0, 0, ch.height - 1])
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
            for (ia, ib, ic) in tris:
                walls.tri([W[ia], W[ib], W[ic]],
                          [(verts[ia][0] / TILE_M, verts[ia][1] / TILE_M), (verts[ib][0] / TILE_M, verts[ib][1] / TILE_M),
                           (verts[ic][0] / TILE_M, verts[ic][1] / TILE_M)], inside=mid_in)
        parts, bounds = [], []
        for kind, mesh, app in (("walls", walls, pbr("../textures/brick.png", MATERIALS["brick"])),
                                ("floor", floor, pbr("../textures/floor.png", MATERIALS["floor"])),
                                ("ceil", ceil, pbr("../textures/concrete.png", MATERIALS["concrete"]))):
            parts.append(shape(app, mesh.ifs("    ")))
        # collision: box slabs per wall panel, leaving the pipe opening free
        def slab(P0, ud, u0, u1, z0, z1):
            if u1 - u0 < 1e-3 or z1 - z0 < 1e-3:
                return
            uc, zc, th = (u0 + u1) / 2, (z0 + z1) / 2, 0.1
            ox, oy = ud[1], -ud[0]                                # outward normal
            cx = c[0] + P0[0] + ud[0] * uc + ox * th / 2
            cy = c[1] + P0[1] + ud[1] * uc + oy * th / 2
            bounds.append(f"      Pose {{ translation {cx:.4f} {cy:.4f} {zc:.4f} rotation 0 0 1 {math.atan2(ud[1], ud[0]):.5f} "
                          f"children [ Box {{ size {u1 - u0:.4f} {th} {z1 - z0:.4f} }} ] }}")
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
        R = float(np.max(np.hypot(corners[:, 0], corners[:, 1])))
        bounds.append(f"      Pose {{ translation {c[0]:.4f} {c[1]:.4f} -0.05 children [ Box {{ size {2 * R:.3f} {2 * R:.3f} 0.1 }} ] }}")
        return (f'Solid {{\n  name "chamber_{nid}"\n  children [\n{"".join(parts)}  ]\n'
                f'  boundingObject Group {{ children [\n{chr(10).join(bounds)}\n    ] }}\n}}\n')

    def _chamber_light(self, nid: str) -> str:
        n = self.m.nodes[nid]
        ch = self.m.chambers[nid]
        x, y = n.pos
        out = ""
        if n.kind == "entry":     # daylight shaft from the manhole
            out += (f"SpotLight {{ attenuation 0 0 1 beamWidth 0.9 cutOffAngle 1.2 direction 0 0 -1 intensity 6 "
                    f"location {x} {y} {ch.height - 0.05} radius 25 color 1 0.97 0.88 castShadows FALSE }}\n")
        if self.ambient > 0:
            out += (f"PointLight {{ attenuation 0 0 1 intensity {self.ambient} location {x} {y} {ch.height - 0.3} "
                    f"radius 10 color 0.85 0.9 1 castShadows FALSE }}\n")
        return out

    # ------------------------------------------------------------- defects
    def _defect_solid(self, d) -> str:
        m = self.m
        e = m.edges[d.edge]
        sec = e.section
        base = self.base[e.material]
        a = d.angle % 360
        t0 = sec.boundary_param(a)
        down = -1 if a < 90 else +1
        asset = make_defect_asset(d, base, e.material, t0, d.s, down)
        w, h = asset.size
        tc, sc = t0 + asset.center_off[0], d.s + asset.center_off[1]
        lo, hi = e.s_min + 0.02, e.s_max - 0.02
        sc = float(np.clip(sc, lo + h / 2, hi - h / 2))
        # sample the boundary: uniform steps + polygon vertices inside the span
        ts = list(np.linspace(tc - w / 2, tc + w / 2, 9))
        P_len = sec.perimeter_len
        acc = 0.0
        for i in range(len(sec.poly)):
            for off in (-P_len, 0.0, P_len):
                if tc - w / 2 < acc + off < tc + w / 2:
                    ts.append(acc + off)
            acc += float(np.hypot(*(sec.poly[(i + 1) % len(sec.poly)] - sec.poly[i])))
        ts = sorted(set(round(t, 6) for t in ts))
        A = np.array(m.nodes[e.a].pos)
        dv = np.array(e.direction)
        right = np.array([dv[1], -dv[0]])
        mesh = Mesh()
        rows = (sc - h / 2, sc + h / 2)
        pts = []
        for t in ts:
            pt, nrm = sec.point_at(t)
            r, z = pt + nrm * PATCH_LIFT
            col = []
            for s in rows:
                xy = A + dv * s + right * r
                col.append(np.array([xy[0], xy[1], sec.fo + z]))
            pts.append((t, col, nrm))
        axis = np.array([A[0] + dv[0] * sc, A[1] + dv[1] * sc, sec.fo])
        for (ta, ca, _), (tb, cb, _) in zip(pts[:-1], pts[1:]):
            ua, ub = (ta - (tc - w / 2)) / w, (tb - (tc - w / 2)) / w
            mesh.quad(ca[0], ca[1], cb[1], cb[0], (ua, 0.0), (ua, 1.0), (ub, 1.0), (ub, 0.0), axis)
        # persist textures (power-of-two sizes: Webots would otherwise rescale them with its own filter)
        stem = f"{d.id}"
        ph, pw = _pot(asset.albedo.shape[0]), _pot(asset.albedo.shape[1])
        alb = cv2.resize(asset.albedo, (pw, ph), interpolation=cv2.INTER_CUBIC)
        rgh = cv2.resize(asset.rough, (pw, ph), interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(str(self.defect_dir / f"{stem}.png"), cv2.cvtColor(alb, cv2.COLOR_RGB2BGR))
        cv2.imwrite(str(self.defect_dir / f"{stem}_r.png"), rgh)
        rel = f"../textures/defects/{m.name}/{stem}"
        app = pbr(f"{rel}.png", 0.8, f"{rel}_r.png")
        key, rcol = self._instance(d.id, d.type)
        c_world = np.mean([p for _, c, _ in pts for p in c], axis=0)
        self.gt.append({"id": d.id, "key": key, "type": d.type, "subtype": d.subtype, "edge": d.edge, "severity": d.severity,
                        "center": [round(float(x), 4) for x in c_world], "s": round(sc, 4), "size_t": round(w, 4), "size_s": round(h, 4),
                        "angle": d.angle, "coverage": round(asset.coverage, 3)})
        return (f'Solid {{\n  name "{d.id}"\n  model "{d.type}"\n  recognitionColors [ {rcol} ]\n'
                f'  children [\n{shape(app, mesh.ifs("    "))}  ]\n}}\n')

    # ------------------------------------------------------------- goals
    def goal_position(self, nid: str) -> tuple[float, float]:
        ch = self.m.chambers[nid]
        c = self.m.nodes[nid].pos
        free = [f for f in ch.faces if f.pipe is None]
        if free:
            ang, rad = free[0].angle, 0.55 * ch.apothem
        else:
            cx, cy = ch.corners[0]
            ang, rad = math.atan2(cy, cx), 0.5 * math.hypot(cx, cy)
        return c[0] + rad * math.cos(ang), c[1] + rad * math.sin(ang)

    def _goal_solid(self, g) -> str:
        x, y = self.goal_position(g.node)
        col = GOAL_COLORS[g.color]
        c = f"{col[0]} {col[1]} {col[2]}"
        _key, rcol = self._instance(g.id, "goal")
        return (f'Solid {{\n  translation {x:.3f} {y:.3f} 0\n  name "goal_{g.id}"\n  model "goal"\n'
                f'  recognitionColors [ {rcol} ]\n  children [\n'
                f'    Pose {{ translation 0 0 0.6 children [ Shape {{ appearance PBRAppearance {{ baseColor {c} '
                f'emissiveColor {c} emissiveIntensity 0.6 roughness 0.6 metalness 0 }} '
                f'geometry Cylinder {{ height 1.2 radius 0.035 }} }} ] }}\n'
                f'    Pose {{ translation 0 0 1.28 children [ Shape {{ appearance PBRAppearance {{ baseColor {c} '
                f'emissiveColor {c} emissiveIntensity 1.5 roughness 0.4 metalness 0 }} geometry Sphere {{ radius 0.09 }} }} ] }}\n'
                f'    PointLight {{ attenuation 0 0 1 color {c} intensity 0.5 location 0 0 1.3 radius 5 castShadows FALSE }}\n'
                f'  ]\n}}\n')

    # ------------------------------------------------------------- rover + world
    def _rover(self, mission_goal: str | None) -> str:
        m, p = self.m, self.profile
        start = m.nodes[m.start_node]
        target = None
        if mission_goal and mission_goal in m.goals:
            target = m.goals[mission_goal].node
        elif m.goals:
            target = next(iter(m.goals.values())).node
        yaw = 0.0
        if target and target != m.start_node:
            route = m.route(m.start_node, target)
            nx, ny = m.nodes[route[1]].pos
            yaw = math.atan2(ny - start.pos[1], nx - start.pos[0])
        cam = p.camera
        proj = "spherical" if cam.fisheye else "planar"
        custom = json.dumps({"profile": p.to_dict(), "map": m.name, "seed": self.seed,
                             "start": list(start.pos), "yaw": yaw,
                             "meta": str(self.out.with_suffix(".json")).replace("\\", "/")}).replace('"', '\\"')
        return (f'SewerRover {{\n  translation {start.pos[0]} {start.pos[1]} 0.02\n  rotation 0 0 1 {yaw:.5f}\n'
                f'  name "rover"\n  controller "sewerbot_rover"\n  customData "{custom}"\n'
                f'  driveType "{p.drive}"\n  bodyLength {p.length}\n  bodyWidth {p.width}\n  bodyHeight {p.height}\n'
                f'  wheelRadius {p.wheel_radius}\n  mass {p.mass}\n  maxSpeed {p.max_speed}\n'
                f'  cameraHeight {cam.height}\n  cameraTilt {math.radians(cam.tilt_deg):.4f}\n'
                f'  cameraFov {math.radians(cam.fov_deg):.4f}\n  cameraPixelsW {cam.width_px}\n  cameraPixelsH {cam.height_px}\n'
                f'  cameraNoise {cam.noise}\n  cameraMotionBlur {cam.motion_blur_ms}\n  cameraProjection "{proj}"\n'
                f'  lightIntensity {p.light_intensity}\n}}\n')

    def build(self, mission_goal: str | None = None) -> Path:
        m = self.m
        x0, y0, x1, y1 = m.bounding_box(4.0)
        cx, cy, span = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0)
        parts = [
            "#VRML_SIM R2025a utf8\n",
            'EXTERNPROTO "../protos/SewerRover.proto"\n\n',
            f'WorldInfo {{\n  title "sewerbot: {m.name}"\n  coordinateSystem "ENU"\n  basicTimeStep 16\n'
            f'  contactProperties [\n'
            f'    ContactProperties {{ material1 "wheel" material2 "default" coulombFriction [ 0.6 ] bounce 0 }}\n'
            f'    ContactProperties {{ material1 "caster" material2 "default" coulombFriction [ 0.03 ] bounce 0 }}\n'
            f'    ContactProperties {{ material1 "track" material2 "default" coulombFriction [ 0.6 ] bounce 0 }}\n'
            f'  ]\n}}\n',
            f"Viewpoint {{\n  orientation 0 1 0 1.5708\n  position {cx:.2f} {cy:.2f} {span * 0.95 + 6:.2f}\n}}\n",
            "Background { skyColor [ 0.01 0.01 0.015 ] luminosity 0.25 }\n",
        ]
        for nid in m.nodes:
            parts.append(self._chamber_solid(nid))
            parts.append(self._chamber_light(nid))
        for e in m.edges.values():
            parts.append(self._pipe_solid(e))
        for d in m.defects:
            parts.append(self._defect_solid(d))
        for g in m.goals.values():
            parts.append(self._goal_solid(g))
        parts.append(self._rover(mission_goal))
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text("".join(parts), encoding="utf-8")
        meta = {"map": m.summary(), "defects": self.gt, "instances": {str(k): v for k, v in self.instances.items()},
                "goals": {g.id: {"node": g.node, "pos": list(self.goal_position(g.node)), "color": g.color}
                          for g in m.goals.values()},
                "fit": {e.id: self.profile.fits(e.section) for e in m.edges.values()},
                "profile": self.profile.to_dict()}
        self.out.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return self.out


def generate(map_path: str | Path, rover: str | RoverProfile = "mid_4wd", out: str | Path | None = None,
             ambient: float = 0.35, seed: int = 0, mission_goal: str | None = None,
             defect_seed: int | None = None, defect_count: int | None = None, **overrides) -> Path:
    m = load_map(map_path, defect_seed, defect_count)
    profile = get_profile(rover, **overrides) if isinstance(rover, str) else rover
    name = f"{m.name}__{profile.name}.wbt"
    out = Path(out) if out else WEBOTS / "worlds" / name
    return WorldBuilder(m, profile, out, ambient, seed).build(mission_goal)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("map")
    ap.add_argument("--rover", default="mid_4wd", choices=sorted(PRESETS))
    ap.add_argument("--out")
    ap.add_argument("--ambient", type=float, default=0.35)
    ap.add_argument("--goal")
    a = ap.parse_args()
    p = generate(a.map, a.rover, a.out, a.ambient, mission_goal=a.goal)
    print("wrote", p)


if __name__ == "__main__":
    main()
