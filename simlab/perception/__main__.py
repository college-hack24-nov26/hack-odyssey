"""python -m simlab.perception capture  --vehicle quad_250 --task search_targets --images 600 --out datasets/search
   python -m simlab.perception pipeline --config cfg.json --out runs/detectors/<id>     capture + train + evaluate
   python -m simlab.perception eval     --model runs/detectors/<id>/model --data runs/detectors/<id>/dataset
"""
import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="simlab.perception")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--vehicle", required=True)
    c.add_argument("--task", required=True)
    c.add_argument("--images", type=int, default=600)
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--out", required=True)
    p = sub.add_parser("pipeline")
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    e = sub.add_parser("eval")
    e.add_argument("--model", required=True)
    e.add_argument("--data", required=True)
    e.add_argument("--split", default="test")
    a = ap.parse_args(argv)
    if a.cmd == "capture":
        from .capture import capture, preview
        meta = capture(a.vehicle, a.task, a.out, a.images, a.seed)
        preview(a.out, f"{a.out}/preview.jpg")
        print(json.dumps(meta["stats"], indent=1))
    elif a.cmd == "pipeline":
        from .train import run_pipeline
        run_pipeline(json.load(open(a.config, encoding="utf-8-sig")), a.out)
    else:
        from .rfdetr import load_detector
        from .train import evaluate_detector
        print(json.dumps(evaluate_detector(load_detector(a.model), a.data, a.split), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
