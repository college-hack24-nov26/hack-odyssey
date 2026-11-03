"""Optional Ultralytics YOLO backend.

LICENCE WARNING: Ultralytics YOLO is AGPL-3.0. Using it means open-sourcing your whole project under AGPL-3.0 or buying
an Ultralytics Enterprise licence. The default detector in this project (RF-DETR, Apache-2.0) has no such condition.
`ultralytics` is NOT installed by default; `pip install ultralytics` only if you accept those terms.
"""
from __future__ import annotations

import numpy as np

from ...core.defects import CLASSES
from .base import Detection, nms


class YOLODetector:
    name = "yolo"

    def __init__(self, weights: str | None = None, conf: float = 0.25, device: str | None = None):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError("ultralytics is not installed (AGPL-3.0). See docs/LICENSING.md before installing it.") from e
        self.model = YOLO(weights or "yolo11n.pt")
        self.conf, self.device = conf, device

    def detect(self, bgr: np.ndarray) -> list[Detection]:
        r = self.model.predict(bgr, conf=self.conf, device=self.device, verbose=False)[0]
        out = []
        for b in r.boxes:
            cid = int(b.cls[0])
            if cid >= len(CLASSES):
                continue
            x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
            out.append(Detection(CLASSES[cid], float(b.conf[0]), (int(x0), int(y0), int(x1 - x0), int(y1 - y0))))
        return nms(out, 0.5)
