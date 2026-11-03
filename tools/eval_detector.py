"""Score a detector against simulator ground truth and write an overlay sheet.

    python -m tools.eval_detector --detector classical --split datasets/sewer_v1/test
    python -m tools.eval_detector --detector classical --raw datasets/raw --limit 400
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from sewerbot.brain.detectors import make_detector
from sewerbot.brain.detectors.base import score
from sewerbot.core.defects import CLASSES

MIN_BOX = 10


def load_raw(root: Path, limit: int, seed: int = 3):
    items = []
    for run in sorted(p for p in root.glob("*") if p.is_dir()):
        for jp in sorted(run.glob("s*.json")):
            items.append(jp)
    random.Random(seed).shuffle(items)
    out = []
    for jp in items[:limit]:
        d = json.loads(jp.read_text())
        gt = [{"cls": g["cls"], "box": g["box"]} for g in d["gt"] if g["box"][2] >= MIN_BOX and g["box"][3] >= MIN_BOX]
        out.append((jp.with_suffix(".jpg"), gt))
    return out


def load_coco(split: Path):
    c = json.loads((split / "_annotations.coco.json").read_text())
    by = {}
    for a in c["annotations"]:
        by.setdefault(a["image_id"], []).append({"cls": CLASSES[a["category_id"] - 1], "box": a["bbox"]})
    return [(split / im["file_name"], by.get(im["id"], [])) for im in c["images"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", default="classical")
    ap.add_argument("--weights")
    ap.add_argument("--split")
    ap.add_argument("--raw")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--conf", type=float, default=0.0)
    ap.add_argument("--sheet")
    a = ap.parse_args()
    items = load_coco(Path(a.split)) if a.split else load_raw(Path(a.raw or "datasets/raw"), a.limit)
    if a.split:
        items = items[: a.limit]
    det = make_detector(a.detector, weights=a.weights)
    preds, gts, times = [], [], []
    tiles = []
    for img_path, gt in items:
        bgr = cv2.imread(str(img_path))
        t0 = time.perf_counter()
        d = [x for x in det.detect(bgr) if x.conf >= a.conf]
        times.append(time.perf_counter() - t0)
        preds.append(d)
        gts.append(gt)
        if a.sheet and len(tiles) < 12 and (gt or d):
            im = bgr.copy()
            for g in gt:
                x, y, w, h = g["box"]
                cv2.rectangle(im, (x, y), (x + w, y + h), (0, 255, 0), 2)
            for p in d:
                x, y, w, h = p.box
                col = (0, 0, 255) if p.cls == "crack" else (255, 120, 0)
                cv2.rectangle(im, (x, y), (x + w, y + h), col, 1)
                cv2.putText(im, f"{p.cls} {p.conf:.2f}", (x, max(12, y - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
            tiles.append(cv2.resize(im, (480, 360)))
    res = score(preds, gts)
    res["_images"] = len(items)
    res["_ms_per_frame"] = round(1000 * float(np.mean(times)), 1)
    res["_gt_boxes"] = {c: sum(1 for g in gts for b in g if b["cls"] == c) for c in CLASSES}
    print(json.dumps(res, indent=1))
    if a.sheet and tiles:
        while len(tiles) % 4:
            tiles.append(np.zeros_like(tiles[0]))
        cv2.imwrite(a.sheet, np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]))


if __name__ == "__main__":
    main()
