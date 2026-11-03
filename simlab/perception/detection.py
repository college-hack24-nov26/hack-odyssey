"""Detections, overlap and non-max suppression, and precision/recall scoring for any set of classes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, Sequence

import numpy as np


@dataclass
class Detection:
    cls: str
    conf: float                       # 0..1
    box: tuple[int, int, int, int]    # x, y, w, h in pixels
    subtype: str | None = None        # optional finer label

    @property
    def area(self) -> int:
        return self.box[2] * self.box[3]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["box"] = list(self.box)
        return d


class Detector(Protocol):
    name: str

    def detect(self, bgr: np.ndarray) -> list[Detection]: ...


def iou(a: Sequence[int], b: Sequence[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def nms(dets: list[Detection], thr: float = 0.5) -> list[Detection]:
    out: list[Detection] = []
    for d in sorted(dets, key=lambda d: -d.conf):
        if all(not (o.cls == d.cls and iou(o.box, d.box) > thr) for o in out):
            out.append(d)
    return out


def score(preds: list[list[Detection]], gts: list[list[dict]], classes: Sequence[str], iou_thr: float = 0.5) -> dict:
    """Per-class precision / recall over a set of frames. Each gt is {"cls", "box"}; a prediction matches at most one gt."""
    res = {c: {"tp": 0, "fp": 0, "fn": 0} for c in classes}
    for pr, gt in zip(preds, gts):
        used = set()
        for p in sorted(pr, key=lambda d: -d.conf):
            if p.cls not in res:
                continue
            best, bi = 0.0, -1
            for j, g in enumerate(gt):
                if j in used or g["cls"] != p.cls:
                    continue
                v = iou(p.box, tuple(g["box"]))
                if v > best:
                    best, bi = v, j
            if best >= iou_thr:
                used.add(bi)
                res[p.cls]["tp"] += 1
            else:
                res[p.cls]["fp"] += 1
        for j, g in enumerate(gt):
            if j not in used and g["cls"] in res:
                res[g["cls"]]["fn"] += 1
    for r in res.values():
        r["precision"] = round(r["tp"] / max(1, r["tp"] + r["fp"]), 3)
        r["recall"] = round(r["tp"] / max(1, r["tp"] + r["fn"]), 3)
    return res
