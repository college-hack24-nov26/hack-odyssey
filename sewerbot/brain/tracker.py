"""Turn per-frame image detections into a registry of physical defects with positions along the pipe.

A detection's pixel is cast as a ray from the camera onto the (convex) pipe cross-section, giving the distance
along the pipe `s`, the angle around the wall and the range. Repeat sightings of the same defect (same pipe, similar
s and angle) are merged, and a defect is *confirmed* after enough consistent sightings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.geometry import Section
from ..core.profiles import RoverProfile
from .detectors.base import Detection


@dataclass
class CameraModel:
    width: int
    height: int
    fov_deg: float            # horizontal
    height_m: float           # lens height above the floor
    tilt_deg: float           # + = pitched down
    x_off: float              # lens position forward of the rover origin

    @staticmethod
    def from_profile(p: RoverProfile) -> "CameraModel":
        c = p.camera
        return CameraModel(c.width_px, c.height_px, c.fov_deg, c.height, c.tilt_deg, p.length / 2 - 0.008)

    @property
    def focal_px(self) -> float:
        return (self.width / 2) / math.tan(math.radians(self.fov_deg) / 2)

    def ray_rover(self, px: float, py: float) -> np.ndarray:
        """Unit ray in the rover frame (x fwd, y left, z up) for pixel (px, py)."""
        f = self.focal_px
        d = np.array([1.0, -(px - self.width / 2) / f, -(py - self.height / 2) / f])
        t = math.radians(self.tilt_deg)               # pitch down about the y axis
        c, s = math.cos(t), math.sin(t)
        d = np.array([c * d[0] + s * d[2], d[1], -s * d[0] + c * d[2]])
        return d / np.linalg.norm(d)


@dataclass
class PipeFrame:
    """A straight pipe: origin A, forward unit vector d (map xy), floor-plane axis height fo, cross-section."""
    edge: str
    origin: np.ndarray
    fwd: np.ndarray
    section: Section
    s_min: float = 0.0
    s_max: float = 1e9

    @property
    def right(self) -> np.ndarray:
        return np.array([self.fwd[1], -self.fwd[0]])


def locate(pipe: PipeFrame, cam: CameraModel, x: float, y: float, yaw: float, box: tuple,
           pitch: float = 0.0) -> dict | None:
    """Cast the pixel at the box centre onto the pipe wall. Returns {s, angle, range, point} or None."""
    px, py = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
    dr = cam.ray_rover(px, py)
    cy_, sy_ = math.cos(yaw), math.sin(yaw)
    dw = np.array([cy_ * dr[0] - sy_ * dr[1], sy_ * dr[0] + cy_ * dr[1], dr[2]])          # world direction
    o = np.array([x + cy_ * cam.x_off, y + sy_ * cam.x_off, cam.height_m])
    rel = o[:2] - pipe.origin
    s0, r0, z0 = float(rel @ pipe.fwd), float(rel @ pipe.right), o[2] - pipe.section.fo
    ds, dr_, dz = float(dw[:2] @ pipe.fwd), float(dw[:2] @ pipe.right), dw[2]
    poly = pipe.section.poly
    t_hit = math.inf
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        e = b - a
        n = np.array([-e[1], e[0]]) / max(np.hypot(*e), 1e-12)              # inward normal (ccw polygon)
        den = n[0] * dr_ + n[1] * dz
        if den >= -1e-9:
            continue
        t = -(n[0] * (r0 - a[0]) + n[1] * (z0 - a[1])) / den
        if t > 1e-6:
            t_hit = min(t_hit, t)
    if not math.isfinite(t_hit):
        return None
    r_h, z_h, s_h = r0 + t_hit * dr_, z0 + t_hit * dz, s0 + t_hit * ds
    if not (pipe.s_min - 0.3 <= s_h <= pipe.s_max + 0.3):
        return None
    return {"s": s_h, "angle": math.degrees(math.atan2(z_h, r_h)) % 360.0, "range": t_hit,
            "point": (r_h, z_h, s_h)}


@dataclass
class TrackedDefect:
    id: int
    cls: str
    edge: str
    s: float
    angle: float
    conf: float = 0.0
    hits: int = 1
    first_t: float = 0.0
    last_t: float = 0.0
    size_m: tuple[float, float] = (0.0, 0.0)
    best_score: float = 0.0
    snapshot: bytes | None = None          # JPEG of the best view (annotated by the caller)
    confirmed: bool = False
    subtype: str | None = None
    votes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "cls": self.cls, "edge": self.edge, "s": round(self.s, 2), "angle": round(self.angle, 1),
                "conf": round(self.conf, 3), "hits": self.hits, "confirmed": self.confirmed,
                "size_cm": [round(self.size_m[0] * 100), round(self.size_m[1] * 100)], "t": round(self.first_t, 1),
                "subtype": self.subtype}


def _angdiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class DefectTracker:
    def __init__(self, cam: CameraModel, confirm_hits: int = 4, gate_s: float = 0.8, gate_angle: float = 35.0,
                 min_conf: float = 0.40, max_range: float = 4.0, merge_s: float = 1.2, merge_angle: float = 50.0):
        self.merge_s, self.merge_angle = merge_s, merge_angle
        self.cam, self.confirm_hits, self.gate_s, self.gate_angle = cam, confirm_hits, gate_s, gate_angle
        self.min_conf, self.max_range = min_conf, max_range
        self.defects: list[TrackedDefect] = []
        self._next = 1

    def reset(self):
        self.defects.clear()
        self._next = 1

    def update(self, t: float, pipe: PipeFrame | None, pose: tuple, dets: list[Detection],
               jpeg_for_best=None) -> list[tuple[Detection, TrackedDefect]]:
        """Associate detections with tracks. `pose` = (x, y, yaw). Returns (detection, track) pairs."""
        out = []
        if pipe is None:
            return out
        x, y, yaw = pose
        for d in dets:
            if d.conf < self.min_conf:
                continue
            loc = locate(pipe, self.cam, x, y, yaw, d.box)
            if loc is None or loc["range"] > self.max_range:
                continue
            best, bd = None, 1e9
            for tr in self.defects:
                if tr.edge != pipe.edge or tr.cls != d.cls:
                    continue
                ds, da = abs(tr.s - loc["s"]), _angdiff(tr.angle, loc["angle"])
                if ds < self.gate_s and da < self.gate_angle and ds + da / 60.0 < bd:
                    best, bd = tr, ds + da / 60.0
            f = self.cam.focal_px
            size = (d.box[2] * loc["range"] / f, d.box[3] * loc["range"] / f)
            score = d.conf * d.area / max(loc["range"], 0.2)
            if best is None:
                best = TrackedDefect(self._next, d.cls, pipe.edge, loc["s"], loc["angle"], d.conf, 1, t, t, size)
                self._next += 1
                self.defects.append(best)
            else:
                k = best.hits
                best.s = (best.s * k + loc["s"]) / (k + 1)
                best.angle = (best.angle + ((loc["angle"] - best.angle + 180) % 360 - 180) / (k + 1)) % 360
                best.hits += 1
                best.conf = max(best.conf * 0.9, d.conf)
                best.last_t = t
            if d.subtype:
                best.votes[d.subtype] = best.votes.get(d.subtype, 0) + d.conf
                best.subtype = max(best.votes, key=best.votes.get)
            if score > best.best_score:
                best.best_score, best.size_m = score, size
                if jpeg_for_best is not None:
                    best.snapshot = jpeg_for_best(d)
            best.confirmed = best.hits >= self.confirm_hits or (best.conf > 0.9 and best.hits >= 2)
            out.append((d, best))
        self._merge()
        return out

    def _merge(self):
        """Fold duplicate tracks of one physical defect (seen from far and near, so the position estimate moved)."""
        i = 0
        while i < len(self.defects):
            a = self.defects[i]
            j = i + 1
            while j < len(self.defects):
                b = self.defects[j]
                if (a.cls == b.cls and a.edge == b.edge and abs(a.s - b.s) < self.merge_s
                        and _angdiff(a.angle, b.angle) < self.merge_angle):
                    keep, drop = (a, b) if a.hits >= b.hits else (b, a)
                    n = keep.hits + drop.hits
                    keep.s = (keep.s * keep.hits + drop.s * drop.hits) / n
                    keep.hits, keep.conf = n, max(keep.conf, drop.conf)
                    keep.confirmed = keep.confirmed or drop.confirmed or n >= self.confirm_hits
                    keep.first_t = min(keep.first_t, drop.first_t)
                    if drop.best_score > keep.best_score:
                        keep.best_score, keep.size_m, keep.snapshot = drop.best_score, drop.size_m, drop.snapshot
                    self.defects.pop(j)                  # b leaves the list either way ...
                    if keep is b:                        # ... and if b is the survivor it takes a's slot
                        self.defects[i] = b
                        a = b
                    j = i + 1
                else:
                    j += 1
            i += 1

    def summary(self) -> list[dict]:
        return [d.to_dict() for d in self.defects if d.confirmed]


def evaluate_against_truth(found: list[TrackedDefect], truth: list[dict], gate_s: float = 0.8,
                           gate_angle: float = 40.0) -> dict:
    """Match confirmed tracks to ground-truth defects (same edge/class, close s and angle)."""
    used, tp = set(), 0
    per_cls = {}
    for g in truth:
        hit = None
        for tr in found:
            if tr.id in used or tr.edge != g["edge"] or tr.cls != g["type"]:
                continue
            if abs(tr.s - g["s"]) < gate_s and _angdiff(tr.angle, g["angle"]) < gate_angle:
                hit = tr
                break
        c = per_cls.setdefault(g["type"], {"found": 0, "missed": 0})
        if hit:
            used.add(hit.id)
            tp += 1
            c["found"] += 1
        else:
            c["missed"] += 1
    fp = len([t for t in found if t.id not in used])
    return {"true_positive": tp, "missed": len(truth) - tp, "false_positive": fp,
            "recall": round(tp / max(1, len(truth)), 3), "precision": round(tp / max(1, tp + fp), 3), "per_class": per_cls}
