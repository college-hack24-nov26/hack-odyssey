"""Sewer geometry: pipe cross-sections, drivable floor and polygonal manhole chambers.

Conventions (match Webots ENU world): X east, Y north, Z up, metres.
A pipe runs from node A to node B. Cross-section coordinates are (r, z):
r = metres to the RIGHT of the direction of travel, z = metres above the pipe axis.
The drivable floor of every pipe sits at world z = 0; the axis is `Section.fo` above it.
Defect/ray angles: 0 deg = right wall, 90 = crown, 180 = left wall, 270 = floor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

SHAPES = ("circular", "oval", "box")
FLOOR_FRAC = 0.6  # floor sits this fraction of the half-height below the axis (round shapes)


@dataclass
class Section:
    shape: str
    width: float
    height: float
    fo: float = 0.0                       # floor offset below the axis (m)
    poly: np.ndarray = field(default=None, repr=False)  # (N,2) ccw in (r,z), axis-centred

    @staticmethod
    def make(shape: str, width: float, height: float | None = None, n_arc: int = 28) -> "Section":
        if shape not in SHAPES:
            raise ValueError(f"unknown shape {shape!r}, expected one of {SHAPES}")
        height = width if (shape == "circular" or height is None) else height
        a, b = width / 2.0, height / 2.0
        if shape == "box":
            fo = b
            poly = np.array([[a, -b], [a, b], [-a, b], [-a, -b]], float)
        else:
            fo = FLOOR_FRAC * b
            t0 = math.asin(fo / b)
            ts = np.linspace(-t0, math.pi + t0, n_arc)
            poly = np.stack([a * np.cos(ts), b * np.sin(ts)], axis=1)
            poly[0, 1] = -fo   # snap chord ends exactly onto the floor line
            poly[-1, 1] = -fo
        return Section(shape, width, height, fo, poly)

    # ---- derived dimensions -------------------------------------------------
    @property
    def floor_width(self) -> float:
        z = -self.fo
        row = self.poly[np.isclose(self.poly[:, 1], z, atol=1e-9)]
        return float(row[:, 0].max() - row[:, 0].min())

    @property
    def clear_height(self) -> float:
        return float(self.poly[:, 1].max() + self.fo)

    @property
    def top(self) -> float:
        """Height of the crown above the floor (world z)."""
        return self.clear_height

    @property
    def perimeter_len(self) -> float:
        d = np.roll(self.poly, -1, axis=0) - self.poly
        return float(np.hypot(d[:, 0], d[:, 1]).sum())

    # ---- ray casting (polygon is convex, so star-shaped about the axis) -----
    def radius(self, angle: float) -> float:
        """Distance from the axis to the boundary along `angle` (radians)."""
        c, s = math.cos(angle), math.sin(angle)
        best = math.inf
        p = self.poly
        q = np.roll(p, -1, axis=0)
        for (x1, y1), (x2, y2) in zip(p, q):
            ex, ey = x2 - x1, y2 - y1
            # solve ray (c,s)*t = (x1,y1) + (ex,ey)*u for t (along ray) and u (along edge)
            den = ex * s - c * ey
            if abs(den) < 1e-12:
                continue
            t = (ex * y1 - ey * x1) / den
            u = (c * y1 - s * x1) / den
            if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                best = min(best, t)
        if not math.isfinite(best):
            raise RuntimeError("ray missed the cross-section")
        return best

    def boundary_point(self, angle_deg: float) -> np.ndarray:
        a = math.radians(angle_deg)
        r = self.radius(a)
        return np.array([r * math.cos(a), r * math.sin(a)])

    def boundary_param(self, angle_deg: float) -> float:
        """Perimeter arc-length of the boundary point hit by the ray at `angle_deg`."""
        pt = self.boundary_point(angle_deg)
        p = self.poly
        acc = 0.0
        for i in range(len(p)):
            a, b = p[i], p[(i + 1) % len(p)]
            ab = b - a
            L = float(np.hypot(*ab))
            if L < 1e-12:
                continue
            t = float(np.dot(pt - a, ab) / (L * L))
            d = float(np.hypot(*(a + ab * np.clip(t, 0, 1) - pt)))
            if d < 1e-6 and -1e-9 <= t <= 1 + 1e-9:
                return acc + t * L
            acc += L
        return 0.0

    def point_at(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        """Boundary point and INWARD unit normal at perimeter arc-length t (wraps)."""
        p = self.poly
        P = self.perimeter_len
        t = t % P
        acc = 0.0
        for i in range(len(p)):
            a, b = p[i], p[(i + 1) % len(p)]
            ab = b - a
            L = float(np.hypot(*ab))
            if acc + L >= t - 1e-12 and L > 1e-12:
                f = (t - acc) / L
                pt = a + ab * f
                tang = ab / L
                n = np.array([-tang[1], tang[0]])    # left of a ccw edge = inward
                return pt, n
            acc += L
        return p[0].copy(), np.array([0.0, 1.0])

    def contains(self, r: float, z: float, margin: float = 0.0) -> bool:
        """Point inside polygon with `margin` clearance (convex half-plane test)."""
        p = self.poly
        for i in range(len(p)):
            a, b = p[i], p[(i + 1) % len(p)]
            ab = b - a
            L = float(np.hypot(*ab))
            if L < 1e-12:
                continue
            cross = (ab[0] * (z - a[1]) - ab[1] * (r - a[0])) / L   # >0 = inside for ccw
            on_floor = abs(a[1] + self.fo) < 1e-9 and abs(b[1] + self.fo) < 1e-9
            if cross < (-1e-9 if on_floor else margin):   # wheels rest on the floor: no margin there
                return False
        return True


def fit_check(width: float, length: float, height: float, sec: Section, margin: float = 0.02) -> dict:
    """Can a rover footprint (w x l, height h standing on the floor) drive / turn in `sec`?"""
    z0 = -sec.fo
    corners = [(width / 2, z0 + 0.0), (-width / 2, z0 + 0.0),
               (width / 2, z0 + height), (-width / 2, z0 + height)]
    fits = all(sec.contains(r, z, margin) for r, z in corners)
    # turning in place sweeps a circle of radius = half diagonal of the footprint
    sweep = math.hypot(length, width)
    can_turn = fits and sweep + 2 * margin <= sec.floor_width
    reason = "ok"
    if not fits:
        reason = (f"rover {width*100:.0f}x{height*100:.0f} cm does not fit the "
                  f"{sec.shape} section (floor {sec.floor_width*100:.0f} cm, "
                  f"clear height {sec.clear_height*100:.0f} cm)")
    elif not can_turn:
        reason = "fits, but cannot turn in place here (needs a chamber to turn)"
    return {"fits": fits, "can_turn": can_turn, "reason": reason}


# ----------------------------------------------------------------------------
# Chambers: a convex polygon circumscribed about a circle of radius `apothem`,
# one wall panel per pipe bearing (plus fillers so no gap exceeds 90 deg).
# ----------------------------------------------------------------------------
@dataclass
class Face:
    angle: float                 # outward bearing (rad)
    pipe: str | None             # edge id attached to this face (or None = plain wall)
    half_before: float = 0.0     # panel extent on the clockwise side of the tangent point
    half_after: float = 0.0      # ... and on the counter-clockwise side


@dataclass
class Chamber:
    faces: list[Face]
    apothem: float
    height: float
    corners: np.ndarray          # (n,2) polygon vertices relative to node centre, ccw

    def face_for(self, pipe_id: str) -> Face:
        return next(f for f in self.faces if f.pipe == pipe_id)


def layout_chamber(attachments: list[tuple[str, float, float]], min_apothem: float = 0.9,
                   margin: float = 0.2, headroom: float = 0.45, section_tops=()) -> Chamber:
    """attachments: (edge_id, bearing_rad, hole_half_width). Returns the chamber layout."""
    items = sorted(((a % (2 * math.pi), pid, hw) for pid, a, hw in attachments))
    faces: list[tuple[float, str | None, float]] = []
    if not items:
        items = [(0.0, None, 0.0)]
    for i, (ang, pid, hw) in enumerate(items):
        faces.append((ang, pid, hw))
        nxt = items[(i + 1) % len(items)][0]
        gap = (nxt - ang) % (2 * math.pi) or 2 * math.pi
        k = max(0, math.ceil(gap / (math.pi / 2) - 1e-9) - 1)
        for j in range(1, k + 1):
            faces.append(((ang + gap * j / (k + 1)) % (2 * math.pi), None, 0.0))
    faces.sort(key=lambda f: f[0])
    n = len(faces)
    gaps = [((faces[(i + 1) % n][0] - faces[i][0]) % (2 * math.pi)) or 2 * math.pi for i in range(n)]
    for g in gaps:
        if g >= math.pi - 1e-6:
            raise ValueError("degenerate chamber layout")
    a = min_apothem
    for i, (ang, pid, hw) in enumerate(faces):
        if pid is None:
            continue
        for g in (gaps[i], gaps[i - 1]):
            a = max(a, (hw + margin) / math.tan(g / 2))
    out_faces = []
    for i, (ang, pid, hw) in enumerate(faces):
        out_faces.append(Face(ang, pid, a * math.tan(gaps[i - 1] / 2), a * math.tan(gaps[i] / 2)))
    corners = []
    for i in range(n):
        mid = faces[i][0] + gaps[i] / 2
        rr = a / math.cos(gaps[i] / 2)
        corners.append((rr * math.cos(mid), rr * math.sin(mid)))
    height = max([headroom + t for t in section_tops] + [2.0])
    return Chamber(out_faces, a, height, np.array(corners))


# ----------------------------------------------------------------------------
# Wall panel with a hole: ring triangulation by ray casting from the hole centre
# ----------------------------------------------------------------------------
def rect_ray(c: float, s: float, cx: float, cz: float, u0: float, u1: float, z0: float, z1: float) -> float:
    ts = []
    if abs(c) > 1e-12:
        for ux in (u0, u1):
            t = (ux - cx) / c
            z = cz + s * t
            if t > 0 and z0 - 1e-9 <= z <= z1 + 1e-9:
                ts.append(t)
    if abs(s) > 1e-12:
        for zz in (z0, z1):
            t = (zz - cz) / s
            u = cx + c * t
            if t > 0 and u0 - 1e-9 <= u <= u1 + 1e-9:
                ts.append(t)
    return min(ts)


def ring_panel(u0: float, u1: float, z0: float, z1: float, hole: Section | None,
               hole_u: float, hole_z: float, n_rays: int = 48):
    """Quads (as two triangles each) of a rectangle with a hole. Returns (verts2d, tris)."""
    if hole is None:
        v = [(u0, z0), (u1, z0), (u1, z1), (u0, z1)]
        return v, [(0, 1, 2), (0, 2, 3)]
    angs = set(np.linspace(0, 2 * math.pi, n_rays, endpoint=False).tolist())
    for ux in (u0, u1):
        for zz in (z0, z1):
            angs.add(math.atan2(zz - hole_z, ux - hole_u) % (2 * math.pi))
    # polygon corner directions of the hole so hole edges are reproduced exactly
    for (r, z) in hole.poly:
        angs.add(math.atan2(z, r) % (2 * math.pi))
    angs = sorted(angs)
    inner, outer = [], []
    for a in angs:
        c, s = math.cos(a), math.sin(a)
        ri = hole.radius(a)
        ro = rect_ray(c, s, hole_u, hole_z, u0, u1, z0, z1)
        inner.append((hole_u + ri * c, hole_z + ri * s))
        outer.append((hole_u + ro * c, hole_z + ro * s))
    verts = inner + outer
    n = len(angs)
    tris = []
    for i in range(n):
        j = (i + 1) % n
        tris.append((i, n + i, n + j))   # inner_i, outer_i, outer_j
        tris.append((i, n + j, j))       # inner_i, outer_j, inner_j
    return verts, tris
