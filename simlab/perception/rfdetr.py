"""RF-DETR (Apache-2.0) detector for any class list; weights come from simlab.perception.train."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .detection import Detection, nms

SIZES = {"nano": "RFDETRNano", "small": "RFDETRSmall", "medium": "RFDETRMedium"}
WEIGHT_NAMES = ("checkpoint_best_total.pth", "checkpoint_best_ema.pth", "checkpoint_best_regular.pth", "checkpoint.pth")


def find_weights(path: str | Path) -> str:
    p = Path(path)
    if p.is_dir():
        for name in WEIGHT_NAMES:
            if (p / name).exists():
                return str(p / name)
        raise FileNotFoundError(f"no checkpoint in {p}")
    return str(p)


class RFDETRDetector:
    name = "rfdetr"

    def __init__(self, weights: str | Path, classes: list[str], size: str = "nano", conf: float = 0.3, device: str | None = None):
        import rfdetr
        import torch
        self.classes, self.conf = list(classes), conf
        kw = {"num_classes": len(self.classes), "pretrain_weights": find_weights(weights)}
        if device:
            kw["device"] = device
        self.model = getattr(rfdetr, SIZES[size])(**kw)
        try:
            self.model.optimize_for_inference()
        except Exception:                      # optional speed-up; keep working if this build does not support it
            pass
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def detect(self, bgr: np.ndarray) -> list[Detection]:
        res = self.model.predict(np.ascontiguousarray(bgr[:, :, ::-1]), threshold=self.conf)
        names = res.data.get("class_name") if hasattr(res, "data") else None
        out: list[Detection] = []
        for i in range(len(res.xyxy)):
            x0, y0, x1, y1 = res.xyxy[i]
            if names is not None:
                cls = str(names[i])
            else:
                cid = int(res.class_id[i])
                cls = self.classes[cid - 1] if 1 <= cid <= len(self.classes) else self.classes[min(cid, len(self.classes) - 1)]
            if cls in self.classes:
                out.append(Detection(cls, float(res.confidence[i]), (int(x0), int(y0), int(x1 - x0), int(y1 - y0))))
        return nms(out, 0.5)


def load_detector(model_dir: str | Path, conf: float = 0.3, device: str | None = None) -> RFDETRDetector:
    """A detector from a directory written by simlab.perception.train (checkpoint + detector.json)."""
    d = Path(model_dir)
    meta = json.loads((d / "detector.json").read_text(encoding="utf-8-sig"))
    return RFDETRDetector(d, meta["classes"], meta.get("size", "nano"), conf, device)
