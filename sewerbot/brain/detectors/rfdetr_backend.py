"""RF-DETR backend (Apache-2.0). Fine-tuned on simulator data with `tools/train_rfdetr.py`."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ...core.defects import CLASSES
from .base import Detection, nms

SIZES = {"nano": "RFDETRNano", "small": "RFDETRSmall", "medium": "RFDETRMedium"}


def _find_weights(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if p.is_dir():
        for name in ("checkpoint_best_total.pth", "checkpoint_best_ema.pth", "checkpoint_best_regular.pth", "checkpoint.pth"):
            if (p / name).exists():
                return str(p / name)
        raise FileNotFoundError(f"no checkpoint in {p}")
    return str(p)


class RFDETRDetector:
    name = "rfdetr"

    def __init__(self, weights: str | None = None, size: str = "nano", conf: float = 0.25, device: str | None = None):
        import torch
        import rfdetr
        self.conf = conf
        cls = getattr(rfdetr, SIZES[size])
        w = _find_weights(weights)
        kw = {"num_classes": len(CLASSES)} if w else {}
        if w:
            kw["pretrain_weights"] = w
        if device:
            kw["device"] = device
        self.model = cls(**kw)
        try:
            self.model.optimize_for_inference()
        except Exception:      # optional speed-up; keep working if the build does not support it
            pass
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def detect(self, bgr: np.ndarray) -> list[Detection]:
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        res = self.model.predict(rgb, threshold=self.conf)
        out: list[Detection] = []
        names = res.data.get("class_name") if hasattr(res, "data") else None
        for i in range(len(res.xyxy)):
            x0, y0, x1, y1 = res.xyxy[i]
            if names is not None:
                cls = str(names[i])
            else:
                cid = int(res.class_id[i])
                cls = CLASSES[cid - 1] if 1 <= cid <= len(CLASSES) else CLASSES[min(cid, len(CLASSES) - 1)]
            if cls not in CLASSES:
                continue
            out.append(Detection(cls, float(res.confidence[i]), (int(x0), int(y0), int(x1 - x0), int(y1 - y0))))
        return nms(out, 0.5)
