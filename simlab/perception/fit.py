"""Fine-tune RF-DETR on a COCO-layout dataset (what simlab.perception.capture writes).

    python -m simlab.perception.fit --data datasets/x --out runs/detectors/x/model --epochs 20

Run as its own process by the pipeline so its progress lines can be read, and so a failed CUDA allocation cannot take
the dashboard down. Tuned for an 8 GB laptop GPU: small batch plus gradient accumulation.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="nano", choices=["nano", "small", "medium"])
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--accum", type=int, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    a = ap.parse_args(argv)

    import rfdetr
    import torch

    print("torch", torch.__version__, "cuda available", torch.cuda.is_available(), flush=True)
    meta = json.loads((Path(a.data) / "dataset.json").read_text(encoding="utf-8"))
    cls = {"nano": rfdetr.RFDETRNano, "small": rfdetr.RFDETRSmall, "medium": rfdetr.RFDETRMedium}[a.size]
    model = cls(gradient_checkpointing=(a.size != "nano"))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    model.train(dataset_dir=a.data, epochs=a.epochs, batch_size=a.batch, grad_accum_steps=a.accum, lr=a.lr, output_dir=a.out)
    (Path(a.out) / "detector.json").write_text(json.dumps({"classes": meta["classes"], "size": a.size, "epochs": a.epochs,
                                                           "vehicle": meta["vehicle"], "task": meta["task"],
                                                           "train_minutes": round((time.time() - t0) / 60, 2)}), encoding="utf-8")
    print(f"training finished in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
