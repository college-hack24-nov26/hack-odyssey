"""Perception: exact labels from segmentation, dataset capture, appearance randomisation, scoring, the search task."""
import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("mujoco")

from simlab import TaskSpec, VehicleSpec, get_task_spec, get_vehicle  # noqa: E402
from simlab.control import make_policy  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.perception import Detection, iou, nms, score  # noqa: E402
from simlab.perception.capture import boxes_from_segmentation, capture, preview, split_of  # noqa: E402
from simlab.perception.train import evaluate_detector  # noqa: E402
from simlab.spec import SpecError  # noqa: E402

# the class colours the search task uses: crate red, barrel blue, ball yellow (RGB)
CLASS_RGB = {"crate": (217, 38, 38), "barrel": (38, 89, 230), "ball": (242, 217, 26)}


# ---------------------------------------------------------------- scoring
def test_nms_and_iou():
    a, b = Detection("x", 0.9, (0, 0, 10, 10)), Detection("x", 0.5, (1, 1, 10, 10))
    assert iou(a.box, b.box) == pytest.approx(81 / 119) and iou((0, 0, 5, 5), (10, 10, 5, 5)) == 0
    assert nms([b, a], 0.5) == [a]
    other = Detection("y", 0.4, (1, 1, 10, 10))
    assert len(nms([a, other], 0.5)) == 2                             # different classes do not suppress each other


def test_precision_recall_counts_each_ground_truth_once():
    preds = [[Detection("a", 0.9, (0, 0, 10, 10)), Detection("a", 0.8, (1, 1, 10, 10)), Detection("b", 0.7, (50, 50, 10, 10))]]
    gts = [[{"cls": "a", "box": [0, 0, 10, 10]}, {"cls": "a", "box": [100, 100, 10, 10]}]]
    r = score(preds, gts, ["a", "b"], 0.5)
    assert (r["a"]["tp"], r["a"]["fp"], r["a"]["fn"]) == (1, 1, 1) and r["a"]["precision"] == 0.5 and r["a"]["recall"] == 0.5
    assert (r["b"]["tp"], r["b"]["fp"], r["b"]["fn"]) == (0, 1, 0)


def test_the_sewer_detector_package_still_works_on_the_shared_code():
    from sewerbot.brain.detectors.base import Detection as SewerDetection, score as sewer_score
    assert SewerDetection is Detection
    r = sewer_score([[Detection("crack", 0.9, (0, 0, 10, 10))]], [[{"cls": "crack", "box": [0, 0, 10, 10]}]])
    assert r["crack"]["tp"] == 1 and set(r) == {"crack", "leak"}


# ---------------------------------------------------------------- labels from segmentation
def test_boxes_come_from_the_visible_pixels_of_labelled_objects_only():
    seg = np.full((40, 60, 2), -1, np.int32)
    seg[5:15, 10:30, 0], seg[5:15, 10:30, 1] = 7, 5                  # object 7 (geom): 20 x 10 px
    seg[20:22, 40:42, 0], seg[20:22, 40:42, 1] = 8, 5                # object 8: 4 px, far too small
    seg[30:38, 5:20, 0], seg[30:38, 5:20, 1] = 9, 5                  # object 9 is not labelled
    seg[0:6, 50:58, 0], seg[0:6, 50:58, 1] = 7, 2                    # same id but a different object type: ignored
    out = boxes_from_segmentation(seg, {7: "crate", 8: "ball"}, ["crate", "ball"], min_area=20)
    assert out == [{"cls": "crate", "box": [10, 5, 20, 10], "area": 200}]


def test_whole_scenes_go_to_one_split_in_the_expected_proportions():
    splits = [split_of(s) for s in range(1000)]
    assert splits.count("train") == 800 and splits.count("valid") == 100 and splits.count("test") == 100


# ---------------------------------------------------------------- capturing a dataset
@pytest.fixture(scope="module")
def rover_set(tmp_path_factory):
    d = tmp_path_factory.mktemp("rover_ds")
    meta = capture("mid_4wd", "search_targets", d, images=60, seed=2, per_scene=3, appearance=0.0)
    return d, meta


def test_capture_writes_a_valid_coco_dataset(rover_set):
    d, meta = rover_set
    assert meta["classes"] == ["crate", "barrel", "ball"]
    seen_scenes: dict[int, str] = {}
    total = 0
    for split in ("train", "valid", "test"):
        coco = json.loads((d / split / "_annotations.coco.json").read_text())
        assert [c["name"] for c in coco["categories"]] == meta["classes"] and [c["id"] for c in coco["categories"]] == [1, 2, 3]
        ids = {im["id"] for im in coco["images"]}
        for im in coco["images"]:
            assert (d / split / im["file_name"]).exists() and seen_scenes.setdefault(im["scene"], split) == split
        for a in coco["annotations"]:
            x, y, w, h = a["bbox"]
            assert a["image_id"] in ids and 1 <= a["category_id"] <= 3 and w >= 6 and h >= 6
            assert 0 <= x and 0 <= y and x + w <= 640 and y + h <= 480 and a["area"] == w * h
        total += len(coco["images"])
    assert total == 60 and meta["stats"]["train"]["images"] > 30
    assert sum(sum(s["boxes"].values()) for s in meta["stats"].values()) > 30        # most views contain something to label
    assert any(s["empty"] > 0 for s in meta["stats"].values())                       # and some are negatives


def test_labels_sit_on_objects_of_the_right_colour(rover_set):
    """Unrandomised images: the pixels inside each box must be dominated by that class's colour."""
    d, _ = rover_set
    coco = json.loads((d / "train" / "_annotations.coco.json").read_text())
    names = {c["id"]: c["name"] for c in coco["categories"]}
    by_img = {im["id"]: im for im in coco["images"]}
    checked = 0
    for a in coco["annotations"]:
        x, y, w, h = map(int, a["bbox"])
        if w * h < 400:
            continue
        rgb = cv2.imread(str(d / "train" / by_img[a["image_id"]]["file_name"]))[y:y + h, x:x + w, ::-1].reshape(-1, 3).astype(float)
        want = np.array(CLASS_RGB[names[a["category_id"]]], float)
        near = (np.linalg.norm(rgb - want, axis=1) < 110).mean()
        wrong = min((np.linalg.norm(rgb - np.array(c), axis=1) < 60).mean() for n, c in CLASS_RGB.items() if n != names[a["category_id"]])
        assert near > 0.15 and wrong < 0.5, (names[a["category_id"]], round(near, 3), round(wrong, 3), by_img[a["image_id"]]["file_name"],
                                             a["bbox"], rgb.mean(0).round().tolist())
        checked += 1
    assert checked >= 5


def test_labels_do_not_depend_on_which_libraries_were_imported_first(tmp_path):
    """Regression: with PyTorch loaded first the GL driver blended segmentation ids at edges and invented boxes."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    code = ("import sys, json, hashlib, pathlib\n{pre}"
            "from simlab.perception.capture import capture\n"
            "out = pathlib.Path(sys.argv[1])\n"
            "capture('mid_4wd', 'search_targets', out, images=30, seed=2, per_scene=3, appearance=0.0)\n"
            "ann = [json.loads((out / s / '_annotations.coco.json').read_text())['annotations'] for s in ('train', 'valid', 'test')]\n"
            "print(hashlib.md5(json.dumps(ann, sort_keys=True).encode()).hexdigest())\n")
    digests = []
    for name, pre in (("plain", ""), ("torch", "import torch\n")):
        r = subprocess.run([sys.executable, "-c", code.format(pre=pre), str(tmp_path / name)], cwd=root, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-800:]
        digests.append(r.stdout.strip().splitlines()[-1])
    assert digests[0] == digests[1] and len(digests[0]) == 32


def test_preview_sheet_is_written(rover_set, tmp_path):
    d, _ = rover_set
    preview(d, tmp_path / "p.jpg", "train", 8)
    assert cv2.imread(str(tmp_path / "p.jpg")).shape[1] == 1280


def test_a_drone_dataset_has_the_same_structure(tmp_path):
    meta = capture("quad_250", "search_targets", tmp_path, images=24, seed=1, per_scene=4)
    assert meta["vehicle"] == "quad_250" and sum(s["images"] for s in meta["stats"].values()) == 24
    img = cv2.imread(str(next((tmp_path / "train").glob("*.jpg"))))
    assert img.shape[:2] == (240, 320)                                # the quad's camera resolution


def test_capture_refuses_tasks_without_labels_or_vehicles_without_cameras(tmp_path):
    bare = TaskSpec.from_dict({"name": "nolabel", "task": "search", "params": {}})
    with pytest.raises(ValueError, match="no `labels`"):
        capture("mid_4wd", bare, tmp_path / "a", images=5)
    d = get_vehicle("quad_250").to_dict()
    d["sensors"] = [s for s in d["sensors"] if s["type"] != "camera"]
    with pytest.raises(SpecError, match="no camera"):
        capture(d, "search_targets", tmp_path / "b", images=5)


# ---------------------------------------------------------------- randomisation and teleporting
def test_appearance_randomisation_changes_images_reproducibly_and_never_recolours_targets():
    sim = Sim(get_vehicle("mid_4wd"), get_task_spec("search_targets"), seed=1)
    sim.reset(seed=4)
    be = sim.backend
    labelled = list(be.label_map())
    colour0 = be.model.geom_rgba[labelled, :3].copy()
    be.cameras["front"]["noise"] = 0.0                                              # sensor noise would mask exact comparisons
    be.teleport(0.0, 0.0, 0.0, 0.0)
    be.randomize_appearance(np.random.default_rng(1), 0.0)
    nominal = be.render("front").astype(float)
    be.randomize_appearance(np.random.default_rng(1), 1.0)
    a = be.render("front").astype(float)
    be.randomize_appearance(np.random.default_rng(1), 1.0)
    again = be.render("front").astype(float)
    be.randomize_appearance(np.random.default_rng(2), 1.0)
    other = be.render("front").astype(float)
    # same seed -> same look. (Not byte-exact: depending on the GL driver state a few pixels differ by one grey level.)
    assert np.abs(a - again).max() <= 2, f"same seed rendered differently: max {np.abs(a - again).max()}"
    assert np.abs(a - other).mean() > 3                                             # another seed, another look
    assert np.abs(a - nominal).mean() > 3
    assert np.array_equal(be.model.geom_rgba[labelled, :3], colour0)                # the labelled objects keep their class colours
    be.randomize_appearance(np.random.default_rng(1), 0.0)                          # strength 0 returns exactly to the nominal look
    assert np.abs(be.render("front").astype(float) - nominal).max() <= 2


def test_teleport_places_the_vehicle_at_rest():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("search_targets"), seed=1)
    sim.reset(seed=1)
    for _ in range(40):
        sim.step_physical([3.0, 0, 0, 1.0])
    o = sim.backend.teleport(-4.0, 3.0, 2.0, 1.2)
    assert np.allclose(o.state.pos, [-4.0, 3.0, 2.0]) and o.state.yaw == pytest.approx(1.2) and np.linalg.norm(o.state.vel) == 0
    for _ in range(60):
        o = sim.backend.step([0, 0, 0, 0])
    assert abs(o.state.pos[2] - 2.0) < 0.1 and np.hypot(o.state.pos[0] + 4.0, o.state.pos[1] - 3.0) < 0.2     # it hovers where it was put
    rover = Sim(get_vehicle("mid_4wd"), get_task_spec("search_targets"), seed=1)
    rover.reset(seed=1)
    o = rover.backend.teleport(2.0, -1.0, 0.0, -0.5)
    assert np.allclose(o.state.pos[:2], [2.0, -1.0]) and o.state.yaw == pytest.approx(-0.5)


# ---------------------------------------------------------------- the search task
def test_a_target_is_perceived_only_inside_the_field_of_view_and_range():
    sim = Sim(get_vehicle("mid_4wd"), get_task_spec("search_targets"), seed=1)
    sim.reset(seed=5)
    t, ep, be = sim.task, sim.episode, sim.backend
    ep.info["found"] = [False] + [True] * (len(ep.info["found"]) - 1)         # only target 0 can be perceived
    tx, ty = ep.info["targets"][0]
    seen = t.perceive(be.teleport(tx - 3.0, ty, 0.0, 0.0).state, ep)           # 3 m away, looking straight at it
    assert seen and abs(seen[0]) < 0.05 and seen[1] == pytest.approx(3.0, abs=0.01) and seen[2] == 0
    assert t.perceive(be.teleport(tx - 3.0, ty, 0.0, math.pi).state, ep) is None                   # facing away
    assert t.perceive(be.teleport(tx - 3.0, ty, 0.0, t.fov / 2 + 0.2).state, ep) is None           # just outside the field of view
    assert t.perceive(be.teleport(tx - 3.0, ty, 0.0, t.fov / 2 - 0.2).state, ep) is not None       # just inside it
    assert t.perceive(be.teleport(tx - 0.6 * t.sense - t.sense, ty, 0.0, 0.0).state, ep) is None   # too far


def test_search_baseline_finds_everything_with_ground_and_air_vehicles():
    for name in ("mid_4wd", "quad_250"):
        sim = Sim(get_vehicle(name), get_task_spec("search_targets"), seed=3)
        pol = make_policy("baseline", sim)
        wins = 0
        for ep in range(3):
            sim.reset(seed=300 + ep)
            pol.reset(sim)
            while True:
                vec, r, term, trunc, info = sim.step_physical(pol.act(sim))
                assert vec.shape == (sim.obs_dim,) and np.isfinite(vec).all()
                if term or trunc:
                    wins += info["success"]
                    break
        assert wins >= 2, f"{name}: {wins}/3"
        assert sim.obs_dim == (10 if name == "quad_250" else 19)


def test_search_scoring_and_rejections():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("search_targets"), seed=1)
    sim.reset(seed=6)
    _, r, term, trunc, info = sim.step_physical([0, 0, 0, 0])
    assert info["of"] == 3 and info["found"] <= 3 and r == pytest.approx(sum(info["terms"].values()))
    d = get_vehicle("mid_4wd").to_dict()
    d["sensors"] = []
    with pytest.raises(SpecError, match="no camera"):
        Sim(VehicleSpec.from_dict(d), TaskSpec.from_dict({"name": "s", "task": "search", "labels": ["crate"]}))


# ---------------------------------------------------------------- evaluating a detector on a captured set
class Lookup:
    """A stand-in detector that knows the dataset: returns the ground truth of the image it is shown (or nothing)."""

    def __init__(self, d: Path, split: str, perfect: bool):
        coco = json.loads((d / split / "_annotations.coco.json").read_text())
        names = {c["id"]: c["name"] for c in coco["categories"]}
        by: dict[int, list] = {}
        for a in coco["annotations"]:
            by.setdefault(a["image_id"], []).append(a)
        self.table = {}
        for im in coco["images"]:
            bgr = cv2.imread(str(d / split / im["file_name"]))
            self.table[int(bgr.sum())] = [Detection(names[a["category_id"]], 0.9, tuple(int(v) for v in a["bbox"])) for a in by.get(im["id"], [])]
        self.perfect = perfect

    def detect(self, bgr):
        return list(self.table.get(int(bgr.sum()), [])) if self.perfect else []


def test_detector_evaluation_scores_perfect_and_empty_detectors(rover_set, tmp_path):
    d, _ = rover_set
    perfect = evaluate_detector(Lookup(d, "valid", True), d, "valid", preview_to=tmp_path / "p.jpg")
    assert all(r["precision"] == 1.0 and r["recall"] == 1.0 for r in perfect["classes"].values() if r["tp"])
    assert sum(r["tp"] for r in perfect["classes"].values()) > 0 and all(r["fp"] == 0 and r["fn"] == 0 for r in perfect["classes"].values())
    assert (tmp_path / "p.jpg").exists() and perfect["images"] > 0
    blind = evaluate_detector(Lookup(d, "valid", False), d, "valid")
    assert blind["mean_f1"] == 0.0 and all(r["recall"] == 0.0 and r["tp"] == 0 for r in blind["classes"].values())
    assert sum(r["fn"] for r in blind["classes"].values()) == sum(r["tp"] for r in perfect["classes"].values())
