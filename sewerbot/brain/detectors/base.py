"""Detector interface. Every backend (classical, RF-DETR, YOLO) returns the same Detection records."""
from __future__ import annotations

from simlab.perception.detection import Detection, Detector, iou, nms  # noqa: F401  (shared with every simlab task)
from simlab.perception.detection import score as _score

from ...core.defects import CLASSES


def score(preds: list[list[Detection]], gts: list[list[dict]], iou_thr: float = 0.3) -> dict:
    """Per-class precision/recall over a set of frames for the sewer classes (crack, leak). gts: {"cls", "box"}."""
    return _score(preds, gts, CLASSES, iou_thr)
