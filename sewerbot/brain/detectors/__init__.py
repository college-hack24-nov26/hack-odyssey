"""Detector registry: `make_detector("classical" | "rfdetr" | "yolo", weights=...)`."""
from __future__ import annotations

from .base import Detection, Detector, iou, nms, score  # noqa: F401


def make_detector(kind: str = "classical", weights: str | None = None, **kw):
    kind = kind.lower()
    if kind == "classical":
        from .classical import ClassicalDetector
        return ClassicalDetector()
    if kind == "rfdetr":
        from .rfdetr_backend import RFDETRDetector
        return RFDETRDetector(weights, **kw)
    if kind == "yolo":     # optional; Ultralytics is AGPL-3.0 - see docs/LICENSING.md
        from .yolo_backend import YOLODetector
        return YOLODetector(weights, **kw)
    raise ValueError(f"unknown detector {kind!r}")
