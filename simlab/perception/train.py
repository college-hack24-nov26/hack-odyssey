"""The perception pipeline as one job: capture a dataset, fine-tune a detector, measure it on held-out scenes.

A run directory holds:  dataset/ (images + COCO labels + dataset.json)   model/ (checkpoints + detector.json)
                        progress.json (stage, epoch, best metric)   eval.json   eval_preview.jpg   train.log
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .capture import capture, preview
from .detection import score

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs" / "detectors"
EPOCH = re.compile(r"Epoch (\d+)/(\d+)")
BEST = re.compile(r"Best EMA metric improved to ([0-9.]+)")


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, default=float), encoding="utf-8")
    os.replace(tmp, path)


def evaluate_detector(detector, dataset_dir: str | Path, split: str = "test", iou_thr: float = 0.5, preview_to: Path | None = None) -> dict:
    """Per-class precision / recall / F1 of a detector on a split of a captured dataset (boxes matched at IoU >= 0.5)."""
    d = Path(dataset_dir)
    coco = json.loads((d / split / "_annotations.coco.json").read_text())
    classes = [c["name"] for c in sorted(coco["categories"], key=lambda c: c["id"])]
    names = {c["id"]: c["name"] for c in coco["categories"]}
    gts: dict[int, list] = {}
    for a in coco["annotations"]:
        gts.setdefault(a["image_id"], []).append({"cls": names[a["category_id"]], "box": a["bbox"]})
    preds, truth, ms, shots = [], [], [], []
    for im in coco["images"]:
        bgr = cv2.imread(str(d / split / im["file_name"]))
        t0 = time.perf_counter()
        dets = detector.detect(bgr)
        ms.append((time.perf_counter() - t0) * 1000)
        preds.append(dets)
        truth.append(gts.get(im["id"], []))
        if preview_to is not None and len(shots) < 12 and (gts.get(im["id"]) or dets):
            shots.append(_draw(bgr, dets, gts.get(im["id"], [])))
    res = score(preds, truth, classes, iou_thr)
    for r in res.values():
        p, rc = r["precision"], r["recall"]
        r["f1"] = round(2 * p * rc / (p + rc), 3) if p + rc else 0.0
    if preview_to is not None and shots:
        cols = 4
        shots = [cv2.resize(s, (320, 240)) for s in shots]
        while len(shots) % cols:
            shots.append(np.zeros_like(shots[0]))
        cv2.imwrite(str(preview_to), np.vstack([np.hstack(shots[i:i + cols]) for i in range(0, len(shots), cols)]))
    warm = ms[1:] or ms
    return {"split": split, "images": len(ms), "classes": res, "ms_per_image": round(float(np.mean(warm)), 1) if warm else None,
            "mean_f1": round(float(np.mean([r["f1"] for r in res.values()])), 3)}


def _draw(bgr, dets, gts):
    img = bgr.copy()
    for g in gts:
        x, y, w, h = map(int, g["box"])
        cv2.rectangle(img, (x, y), (x + w, y + h), (255, 255, 255), 1)
    for d in dets:
        x, y, w, h = d.box
        cv2.rectangle(img, (x, y), (x + w, y + h), (60, 255, 120), 2)
        cv2.putText(img, f"{d.cls} {d.conf:.2f}", (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 255, 120), 1, cv2.LINE_AA)
    return img


def prune_checkpoints(model_dir: Path) -> int:
    """Keep only the best weights (what load_detector uses); RF-DETR leaves one ~100 MB checkpoint per saved epoch.
    Returns the number of bytes freed."""
    from .rfdetr import WEIGHT_NAMES
    keep = next((model_dir / n for n in WEIGHT_NAMES if (model_dir / n).exists()), None)
    freed = 0
    for p in (*model_dir.glob("*.pth"), *model_dir.glob("*.ckpt")):          # .ckpt = Lightning resume files with optimiser state
        if p != keep:
            freed += p.stat().st_size
            p.unlink()
    return freed


def run_pipeline(cfg: dict, out_dir: str | Path) -> Path:
    from ..envs.gym_env import as_task, as_vehicle
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    vehicle, task = as_vehicle(cfg["vehicle"]), as_task(cfg["task"])
    cfg = {"images": 800, "epochs": 20, "size": "nano", "seed": 0, "batch": 8, "accum": 2, "appearance": 1.0, **cfg,
           "vehicle": vehicle.to_dict(), "task": task.to_dict()}
    _write(out / "config.json", cfg)
    state = {"status": "starting", "stage": "capture", "steps": 0, "total_steps": cfg["images"], "vehicle": vehicle.name, "task": task.name,
             "kind": vehicle.kind, "algo": f"rfdetr-{cfg['size']}", "classes": list(task.labels), "started": time.time()}
    _write(out / "progress.json", state)
    (out / "stop.flag").unlink(missing_ok=True)

    def stopped() -> bool:
        return (out / "stop.flag").exists()

    try:
        state.update(status="running")
        data = out / "dataset"

        def cap(n, total):
            state.update(stage="capture", steps=n, total_steps=total)
            _write(out / "progress.json", state)

        meta = capture(vehicle, task, data, cfg["images"], cfg["seed"], appearance=cfg["appearance"], progress=cap)
        preview(data, out / "dataset_preview.jpg", "valid")
        state["dataset"] = meta["stats"]
        if stopped():
            state.update(status="stopped")
            return out
        state.update(stage="train", steps=0, total_steps=cfg["epochs"])
        _write(out / "progress.json", state)
        cmd = [sys.executable, "-m", "simlab.perception.fit", "--data", str(data), "--out", str(out / "model"), "--size", cfg["size"],
               "--epochs", str(cfg["epochs"]), "--batch", str(cfg["batch"]), "--accum", str(cfg["accum"])]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with open(out / "train.log", "a", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", creationflags=flags, env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
            for line in proc.stdout:
                log.write(line)
                log.flush()
                changed = False
                if m := EPOCH.search(line):
                    state.update(steps=int(m.group(1)), total_steps=int(m.group(2)))
                    changed = True
                if m := BEST.search(line):
                    state.update(best_eval=float(m.group(1)))
                    changed = True
                if changed:
                    _write(out / "progress.json", state)
                if stopped():
                    proc.terminate()
            rc = proc.wait()
        if stopped():
            state.update(status="stopped")
            return out
        if rc != 0 or not (out / "model" / "detector.json").exists():
            raise RuntimeError(f"detector training failed (exit {rc}); see train.log")
        state.update(stage="eval", status="evaluating", steps=cfg["epochs"], freed_mb=round(prune_checkpoints(out / "model") / 2 ** 20))
        _write(out / "progress.json", state)
        from .rfdetr import load_detector
        det = load_detector(out / "model", conf=0.3)
        _write(out / "eval.json", evaluate_detector(det, data, "test", preview_to=out / "eval_preview.jpg"))
        state.update(status="done", stage="done", finished=time.time())
    except Exception as e:  # noqa: BLE001 - leave a readable record for the dashboard, then re-raise for the log
        state.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        _write(out / "progress.json", state)
    return out
