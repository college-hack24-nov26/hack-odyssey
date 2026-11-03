"""Fine-tune RF-DETR (Apache-2.0) on a dataset built by tools.make_dataset (COCO layout).

    python -m tools.train_rfdetr --data datasets/sewer_v1 --size nano --epochs 30 --out models/rfdetr_nano_v1

Tuned for an 8 GB laptop GPU (RTX 5050): small batch + gradient accumulation. Close Webots while training.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/sewer_v1")
    ap.add_argument("--size", default="nano", choices=["nano", "small", "medium"])
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--accum", type=int, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--out", default="models/rfdetr_nano_v1")
    ap.add_argument("--resume")
    a = ap.parse_args()

    import torch
    import rfdetr

    print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available(), flush=True)
    if torch.cuda.is_available():
        print("gpu:", torch.cuda.get_device_name(0), f"{torch.cuda.get_device_properties(0).total_memory / 2**30:.1f} GB", flush=True)
    cls = {"nano": rfdetr.RFDETRNano, "small": rfdetr.RFDETRSmall, "medium": rfdetr.RFDETRMedium}[a.size]
    model = cls(gradient_checkpointing=(a.size != "nano"))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    kw = dict(dataset_dir=a.data, epochs=a.epochs, batch_size=a.batch, grad_accum_steps=a.accum, lr=a.lr,
              output_dir=a.out)
    if a.resume:
        kw["resume"] = a.resume
    model.train(**kw)
    print(f"training finished in {(time.time() - t0) / 60:.1f} min -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
