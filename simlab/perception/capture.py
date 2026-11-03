"""Capture a labelled detection dataset from any (vehicle, task): exact boxes from the simulator's segmentation render.

Each scene is a fresh arrangement of the task's objects; several viewpoints are sampled per scene (mostly looking at a
labelled object from a plausible distance and height, sometimes at nothing, so the detector also sees negatives), and
appearance (lighting, floor tint, colours of unlabelled objects, optics, exposure, blur) is randomised per image.
Scenes are assigned to train / valid / test whole, so no scene appears in two splits.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ..envs.core import Sim
from ..envs.gym_env import as_task, as_vehicle

MIN_AREA = 60          # px of visible object below which a box is not labelled (too small to see)
MIN_SIDE = 6           # px
SPLITS = ("train", "valid", "test")


def split_of(scene: int) -> str:
    """Whole scenes go to one split: 80 % train, 10 % valid, 10 % test."""
    return "test" if scene % 10 == 9 else "valid" if scene % 10 == 8 else "train"


def boxes_from_segmentation(seg: np.ndarray, label_of: dict[int, str], classes: list[str], min_area: int = MIN_AREA) -> list[dict]:
    """[{cls, box: [x, y, w, h], area}] for each labelled object visible in a segmentation render (ids, object types)."""
    ids, kinds = seg[..., 0], seg[..., 1]
    out = []
    geom = 5                                              # mujoco.mjtObj.mjOBJ_GEOM
    for gid, cls in label_of.items():
        if cls not in classes:
            continue
        mask = (ids == gid) & (kinds == geom)
        area = int(mask.sum())
        if area < min_area:
            continue
        ys, xs = np.nonzero(mask)
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        if x1 - x0 >= MIN_SIDE and y1 - y0 >= MIN_SIDE:
            out.append({"cls": cls, "box": [x0, y0, x1 - x0, y1 - y0], "area": area})
    return out


def _sample_pose(rng: np.random.Generator, sim: Sim, air: bool, spots: list[tuple[float, float, float]]):
    """A viewpoint (x, y, z, yaw): usually 1-8 m from a labelled object, facing it roughly; sometimes anywhere."""
    w, d = sim.backend.world.arena
    mx, my = w / 2 - 0.8, d / 2 - 0.8
    for _ in range(40):
        if spots and rng.random() < 0.85:
            tx, ty, _ = spots[rng.integers(len(spots))]
            dist = rng.uniform(2.0, 8.0) if air else rng.uniform(1.2, 6.0)
            ang = rng.uniform(0, 2 * math.pi)
            x, y = tx + dist * math.cos(ang), ty + dist * math.sin(ang)
            yaw = math.atan2(ty - y, tx - x) + rng.normal(0, 0.35)
        else:
            x, y, yaw = rng.uniform(-mx, mx), rng.uniform(-my, my), rng.uniform(-math.pi, math.pi)
        if abs(x) > mx or abs(y) > my:
            continue
        if all(math.hypot(x - ox, y - oy) > r + 0.5 for ox, oy, r in sim_items(sim)):
            return x, y, (rng.uniform(1.2, 4.0) if air else 0.0), yaw
    return 0.0, 0.0, (2.5 if air else 0.0), 0.0


def sim_items(sim: Sim) -> list[tuple[float, float, float]]:
    """(x, y, footprint radius) of every placed, collidable movable item in the current episode."""
    out = []
    for it in sim.backend.world.items:
        p = sim.episode.placements.get(it.name)
        if p is not None and len(p) == 3 and it.movable and it.collide:
            out.append((p[0], p[1], max(it.size[:2]) * (1.42 if it.shape == "box" else 1.0)))
    return out


def _photometric(img: np.ndarray, rng: np.random.Generator, strength: float) -> np.ndarray:
    """Camera-ish nuisance variation on top of the 3D appearance randomisation: exposure, gamma, a little blur."""
    x = img.astype(np.float32) / 255.0
    x = np.clip(x, 0, 1) ** rng.uniform(1 - 0.25 * strength, 1 + 0.35 * strength)
    x = np.clip(x * rng.uniform(1 - 0.3 * strength, 1 + 0.3 * strength), 0, 1)
    out = (x * 255).astype(np.uint8)
    if rng.random() < 0.2 * strength:
        k = int(rng.choice([3, 5]))
        out = cv2.GaussianBlur(out, (k, k), 0)
    return out


def capture(vehicle, task, out_dir: str | Path, images: int = 600, seed: int = 0, per_scene: int = 6, appearance: float = 1.0,
            camera: str | None = None, progress: Callable[[int, int], None] | None = None) -> dict:
    """Write out_dir/{train,valid,test}/*.jpg + _annotations.coco.json; returns the dataset statistics."""
    v, t = as_vehicle(vehicle), as_task(task)
    classes = list(t.labels)
    if not classes:
        raise ValueError(f"task {t.name!r} lists no `labels`: nothing to detect")
    sim = Sim(v, t, seed=seed)
    be = sim.backend
    cam = camera or next(iter(be.cameras), None)
    if cam is None:
        raise ValueError(f"{v.name} has no camera")
    label_of = be.label_map()
    if not label_of:
        raise ValueError(f"task {t.name!r} has no labelled objects in its world")
    air = v.kind == "multirotor"
    out = Path(out_dir)
    coco = {s: {"images": [], "annotations": [], "categories": [{"id": i + 1, "name": c} for i, c in enumerate(classes)]} for s in SPLITS}
    for s in SPLITS:
        (out / s).mkdir(parents=True, exist_ok=True)
    stats = {s: {"images": 0, "boxes": dict.fromkeys(classes, 0), "empty": 0} for s in SPLITS}
    rng, ann_id, n, scene = np.random.default_rng(seed), 1, 0, 0
    groups = sim.task.capture_groups()                       # a static world: each defect (or stretch of pipe) is one scene
    try:
        while n < images:
            sim.reset(seed=seed * 7919 + scene)
            if groups:
                gi = scene % len(groups)
                split, poses = split_of(gi), sim.task.viewpoints(groups[gi], rng, per_scene)
                spots = []
            else:
                split, poses = split_of(scene), None
                spots = [(p[0], p[1], 0.0) for name, p in sim.episode.placements.items()
                         if len(p) == 3 and any(i.name == name and i.label for i in be.world.items)]
            for k in range(per_scene):
                if n >= images:
                    break
                x, y, z, yaw = poses[k] if poses else _sample_pose(rng, sim, air, spots)
                be.teleport(x, y, z, yaw)
                be.randomize_appearance(rng, appearance)
                img = _photometric(be.render(cam), rng, appearance)
                boxes = boxes_from_segmentation(be.render(cam, segmentation=True), label_of, classes)
                fname = f"{t.name}_{v.name}_{n:06d}.jpg"
                bgr = np.ascontiguousarray(img[:, :, ::-1])
                cv2.imwrite(str(out / split / fname), bgr, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(82, 96))])
                h, w = img.shape[:2]
                img_id = len(coco[split]["images"]) + 1
                coco[split]["images"].append({"id": img_id, "file_name": fname, "width": w, "height": h, "scene": scene,
                                              **({"group": groups[gi]} if groups else {})})
                for b in boxes:
                    bx, by, bw, bh = b["box"]
                    coco[split]["annotations"].append({"id": ann_id, "image_id": img_id, "category_id": classes.index(b["cls"]) + 1,
                                                       "bbox": [bx, by, bw, bh], "area": bw * bh, "iscrowd": 0})
                    ann_id += 1
                    stats[split]["boxes"][b["cls"]] += 1
                stats[split]["images"] += 1
                stats[split]["empty"] += not boxes
                n += 1
                if progress and n % 20 == 0:
                    progress(n, images)
            scene += 1
    finally:
        sim.close()
    for s in SPLITS:
        (out / s / "_annotations.coco.json").write_text(json.dumps(coco[s]), encoding="utf-8")
    meta = {"classes": classes, "vehicle": v.name, "task": t.name, "camera": cam, "seed": seed, "scenes": scene, "stats": stats}
    (out / "dataset.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if progress:
        progress(n, images)
    return meta


def preview(dataset_dir: str | Path, out_file: str | Path, split: str = "valid", n: int = 12) -> None:
    """A contact sheet of annotated images, to eyeball the labels."""
    d = Path(dataset_dir)
    coco = json.loads((d / split / "_annotations.coco.json").read_text())
    names = {c["id"]: c["name"] for c in coco["categories"]}
    by_img: dict[int, list] = {}
    for a in coco["annotations"]:
        by_img.setdefault(a["image_id"], []).append(a)
    tiles = []
    for im in coco["images"][:n]:
        bgr = cv2.imread(str(d / split / im["file_name"]))
        for a in by_img.get(im["id"], []):
            x, y, w, h = map(int, a["bbox"])
            cv2.rectangle(bgr, (x, y), (x + w, y + h), (60, 255, 120), 2)
            cv2.putText(bgr, names[a["category_id"]], (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 255, 120), 1, cv2.LINE_AA)
        tiles.append(cv2.resize(bgr, (320, 240)))
    cols = 4
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(out_file), np.vstack(rows))
