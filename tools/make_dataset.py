"""Generate a labelled sewer-defect dataset by driving/teleporting rovers through generated worlds in Webots.

    python -m tools.make_dataset capture --maps straight_t,star_five_goals,large_loop --rovers micro_2wd,mid_4wd --per-run 150 --seeds 3
    python -m tools.make_dataset build   --name sewer_v1          # raw captures -> COCO/YOLO train/valid/test
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from pathlib import Path

from sewerbot.core.defects import CLASSES
from .gen_world import ROOT, generate
from .webots_runner import run_test

RAW = ROOT / "datasets" / "raw"
MIN_BOX = 10   # px: smaller boxes are dropped from the labels


def capture(maps, rovers, per_run, seeds, ambients=(0.0, 0.35, 0.8), base_seed=100, mode="fast"):
    RAW.mkdir(parents=True, exist_ok=True)
    runs = [(m, r, s) for s in range(seeds) for m in maps for r in rovers]
    for k, (m, r, s) in enumerate(runs):
        tag = f"{m}__{r}__s{s}"
        out = RAW / tag
        if (out / "DONE").exists():
            print(f"[{k + 1}/{len(runs)}] {tag}: already captured", flush=True)
            continue
        ambient = ambients[s % len(ambients)]
        wbt = generate(ROOT / "maps" / f"{m}.yaml", r, out=ROOT / "sim" / "webots" / "worlds" / f"ds_{tag}.wbt",
                       ambient=ambient, defect_seed=base_seed + s * 7 + hash(m) % 5, defect_count=14 + 2 * s)
        t0 = time.time()
        try:
            run_test(wbt, [{"v": 0, "w": 0, "t": 1}], out, timeout=90 + 3 * per_run, mode=mode, minimize=True,
                     dataset={"n": per_run, "seed": base_seed + k, "prefix": "s", "p_defect": 0.7})
        except TimeoutError as e:
            print(f"[{k + 1}/{len(runs)}] {tag}: {e}", flush=True)
            continue
        (out / "DONE").write_text("ok")
        n = len(list(out.glob("s*.jpg")))
        print(f"[{k + 1}/{len(runs)}] {tag}: {n} images in {time.time() - t0:.0f}s", flush=True)
        for f in (wbt, wbt.with_suffix(".json")):
            f.unlink(missing_ok=True)


def build(name, val=0.12, test=0.08, seed=7):
    items = []
    for run in sorted(p for p in RAW.glob("*") if p.is_dir()):
        for jp in sorted(run.glob("s*.json")):
            items.append((run.name, jp))
    if not items:
        raise SystemExit("no raw captures; run `capture` first")
    # split by run+sequence blocks of 25 so near-identical neighbours stay together
    rng = random.Random(seed)
    blocks = {}
    for run, jp in items:
        blocks.setdefault((run, int(jp.stem[1:]) // 25), []).append((run, jp))
    keys = list(blocks)
    rng.shuffle(keys)
    n = len(keys)
    split = {}
    for i, k in enumerate(keys):
        split[k] = "test" if i < n * test else "valid" if i < n * (test + val) else "train"
    dest = ROOT / "datasets" / name
    if dest.exists():
        shutil.rmtree(dest)
    cats = [{"id": i + 1, "name": c} for i, c in enumerate(CLASSES)]
    coco = {s: {"images": [], "annotations": [], "categories": cats} for s in ("train", "valid", "test")}
    stats = {s: {"images": 0, "boxes": {c: 0 for c in CLASSES}, "empty": 0} for s in coco}
    ann_id = 1
    for (run, blk), members in blocks.items():
        s = split[(run, blk)]
        (dest / s).mkdir(parents=True, exist_ok=True)
        (dest / "yolo" / s / "images").mkdir(parents=True, exist_ok=True)
        (dest / "yolo" / s / "labels").mkdir(parents=True, exist_ok=True)
        for _, jp in members:
            d = json.loads(jp.read_text())
            fname = f"{run}__{jp.stem}.jpg"
            shutil.copy2(jp.with_suffix(".jpg"), dest / s / fname)
            shutil.copy2(jp.with_suffix(".jpg"), dest / "yolo" / s / "images" / fname)
            W, H = d["w"], d["h"]
            img_id = len(coco[s]["images"]) + 1
            coco[s]["images"].append({"id": img_id, "file_name": fname, "width": W, "height": H})
            lines, kept = [], 0
            for g in d["gt"]:
                x, y, w, h = g["box"]
                if w < MIN_BOX or h < MIN_BOX:
                    continue
                cid = CLASSES.index(g["cls"])
                coco[s]["annotations"].append({"id": ann_id, "image_id": img_id, "category_id": cid + 1,
                                               "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0})
                ann_id += 1
                lines.append(f"{cid} {(x + w / 2) / W:.6f} {(y + h / 2) / H:.6f} {w / W:.6f} {h / H:.6f}")
                stats[s]["boxes"][g["cls"]] += 1
                kept += 1
            (dest / "yolo" / s / "labels" / (Path(fname).stem + ".txt")).write_text("\n".join(lines))
            stats[s]["images"] += 1
            stats[s]["empty"] += kept == 0
    for s, c in coco.items():
        (dest / s / "_annotations.coco.json").write_text(json.dumps(c))
    (dest / "yolo" / "data.yaml").write_text(
        f"path: {dest.as_posix()}/yolo\ntrain: train/images\nval: valid/images\ntest: test/images\n"
        f"names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES)))
    (dest / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
    return dest


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--maps", default="straight_t,star_five_goals,large_loop")
    c.add_argument("--rovers", default="micro_2wd,mid_4wd")
    c.add_argument("--per-run", type=int, default=150)
    c.add_argument("--seeds", type=int, default=2)
    c.add_argument("--mode", default="fast")
    b = sub.add_parser("build")
    b.add_argument("--name", default="sewer_v1")
    a = ap.parse_args()
    if a.cmd == "capture":
        capture(a.maps.split(","), a.rovers.split(","), a.per_run, a.seeds, mode=a.mode)
    else:
        build(a.name)


if __name__ == "__main__":
    main()
