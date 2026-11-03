"""The MuJoCo pipe environment: geometry that matches the Webots world, a camera that sees planted defects, a baseline that drives."""
import math
import re
from collections import Counter

import numpy as np
import pytest

pytest.importorskip("mujoco")

from sewerbot.core.mapspec import load_map  # noqa: E402
from simlab import get_task_spec, get_vehicle  # noqa: E402
from simlab.control import make_policy  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.spec import SpecError, TaskSpec  # noqa: E402
from simlab.world.pipe_geometry import MAX_EDGE, build_pipe_world  # noqa: E402
from tools.gen_world import ROOT, generate  # noqa: E402

MAPS = ("straight_t", "star_five_goals", "large_loop")


def in_network(m, x: float, y: float, tol: float = 0.1) -> bool:
    """Is the point inside a chamber (a polygon) or a pipe (a strip)? SewerMap.locate treats chambers as discs, which misses their corners."""
    p = np.array([x, y])
    for nid, node in m.nodes.items():
        poly = m.chambers[nid].corners + np.array(node.pos)
        inside = False
        for a, b in zip(poly, np.roll(poly, -1, axis=0)):
            if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
                inside = not inside
        near = min(np.hypot(*(p - q)) for q in poly)
        if inside or near < tol:
            return True
    for eid, e in m.edges.items():
        o, f, rt = m.edge_frame(eid)
        s, lat = float((p - o) @ f), float((p - o) @ rt)
        if e.s_min - tol <= s <= e.s_max + tol and abs(lat) <= e.section.width / 2 + tol:
            return True
    return False


def sim_for(task="pipe_straight", vehicle="mid_4wd", **kw):
    return Sim(get_vehicle(vehicle), get_task_spec(task), **kw)


# ---------------------------------------------------------------- geometry
@pytest.mark.parametrize("name", MAPS)
def test_collision_boxes_equal_the_ones_the_webots_world_uses(name, tmp_path):
    """tools/gen_world.py writes one thin box per wall segment into the Webots world; the MuJoCo world must stand where they stand."""
    wbt = generate(ROOT / "maps" / f"{name}.yaml", "mid_4wd", out=tmp_path / "w.wbt").read_text()
    webots = []
    for t, size in re.findall(r"Pose \{ translation ([-\d. e]+?) (?:rotation [-\d. e]+? )?children \[ Box \{ size ([-\d. e]+?) \} \] \}", wbt):
        x, y, z = (float(v) for v in t.split())
        if abs(z + 0.05) < 1e-6:                                            # the floor slabs: the planar vehicle model needs none
            continue
        webots.append(((x, y, z), tuple(sorted(float(v) for v in size.split()))))
    mine = [(c, tuple(sorted(s))) for c, s, _ in build_pipe_world(load_map(ROOT / "maps" / f"{name}.yaml"), pad=0.0).slabs]
    assert len(mine) == len(webots) > 50
    free = list(webots)
    for c, s in mine:                                                       # every box of mine has a partner of the same size at the same place
        hit = next((i for i, (wc, ws) in enumerate(free) if np.allclose(s, ws, atol=0.006) and np.allclose(c, wc, atol=0.006)), None)
        assert hit is not None, f"no Webots box matches {np.round(c, 3)} {np.round(s, 3)}"
        free.pop(hit)
    assert not free


def test_the_slabs_sit_behind_the_walls_by_the_padding():
    m = load_map(ROOT / "maps" / "straight_t.yaml")
    flush, padded = build_pipe_world(m, pad=0.0), build_pipe_world(m)
    moved = [np.linalg.norm(np.subtract(a[0], b[0])) for a, b in zip(flush.slabs, padded.slabs)]
    assert all(abs(d - 0.003) < 1e-6 for d in moved)


@pytest.mark.parametrize("name", MAPS)
def test_every_surface_is_textured_labelled_where_it_should_be_and_faces_the_inside(name):
    m = load_map(ROOT / "maps" / f"{name}.yaml")
    w = build_pipe_world(m)
    for mesh in w.meshes:
        assert mesh.texture is None or mesh.texture in w.assets, mesh.name
        assert len(mesh.uv) == len(mesh.verts) == 3 * len(mesh.faces)
    defect_meshes = [x for x in w.meshes if x.name.startswith("defect_")]
    assert len(defect_meshes) == len(m.defects) == len(w.defects) and {x.label for x in defect_meshes} <= {"crack", "leak"}
    assert Counter(x.label for x in defect_meshes) == Counter(d.type for d in m.defects)
    assert all(x.label is None for x in w.meshes if not x.name.startswith("defect_"))
    first = next(x for x in w.meshes if x.name == f"pipe_{next(iter(m.edges))}_wall")
    e = m.edges[next(iter(m.edges))]
    axis_z = e.section.fo
    tri = first.verts[first.faces[0]]
    n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    centre = tri.mean(0)
    axis_pt = np.array([centre[0], centre[1], axis_z])
    along = np.array([*e.direction, 0.0])
    to_axis = axis_pt - centre
    to_axis -= along * (to_axis @ along)
    assert n @ to_axis > 0                                                   # the visible side faces into the pipe, so the lamp lights it
    cells = [np.linalg.norm(first.verts[f][(k + 1) % 3] - first.verts[f][k]) for f in first.faces for k in range(3)]
    assert max(cells) < 1.6 * MAX_EDGE + 1e-6                               # small triangles: MuJoCo lights vertices and interpolates


def test_the_world_starts_where_the_map_says_and_faces_the_first_pipe():
    for name in MAPS:
        m = load_map(ROOT / "maps" / f"{name}.yaml")
        w = build_pipe_world(m)
        assert (w.start[0], w.start[1]) == tuple(m.nodes[m.start_node].pos)
        target = next(iter(m.goals.values())).node
        nxt = m.nodes[m.route(m.start_node, target)[1]].pos
        assert abs(math.atan2(math.sin(w.start[2] - math.atan2(nxt[1] - w.start[1], nxt[0] - w.start[0])), math.cos(w.start[2] - math.atan2(nxt[1] - w.start[1], nxt[0] - w.start[0])))) < 1e-6
        assert set(w.goals) == set(m.goals)


# ---------------------------------------------------------------- the camera
def test_the_lamp_is_the_only_light_and_the_camera_sees_the_pipe():
    lit = sim_for()
    lit.reset(seed=1)
    lit.backend.teleport(2.0, 0.0, 0.0, 0.0)
    lit.backend.cameras["front"]["auto_exposure"] = False
    raw_lit = lit.backend.render("front")
    dark = Sim(get_vehicle("mid_4wd").override(light_intensity=0.0), get_task_spec("pipe_straight"))
    dark.reset(seed=1)
    dark.backend.teleport(2.0, 0.0, 0.0, 0.0)
    dark.backend.cameras["front"]["auto_exposure"] = False
    raw_dark = dark.backend.render("front")
    assert raw_lit.mean() > 1.8 * raw_dark.mean() > 0                         # the lamp adds a lot over the faint fill light
    far = lit.backend
    far.teleport(12.0, 0.0, 0.0, 0.0)
    assert abs(float(np.percentile(far.render("front"), 80)) - float(np.percentile(raw_lit, 80))) < 12      # lit evenly along the pipe, not just near the start


def test_auto_exposure_brightens_a_dark_frame_like_a_real_sensor():
    s = sim_for()
    s.reset(seed=1)
    s.backend.teleport(2.0, 0.0, 0.0, 0.0)
    on = s.backend.render("front").mean()
    s.backend.cameras["front"]["auto_exposure"] = False
    off = s.backend.render("front").mean()
    assert on > 1.4 * off
    seg = s.backend.render("front", segmentation=True)
    assert seg.shape[2] == 2                                                   # segmentation frames are never auto-exposed (they are ids, not light)


def test_a_planted_crack_shows_up_in_the_segmentation_with_its_label():
    s = sim_for()
    s.reset(seed=1)
    be = s.backend
    d = next(x for x in s.task.defects if x["id"] == "D01")                    # a crack on the first pipe, 6 m in, on the left wall
    be.teleport(d["s"] - 1.2, 0.0, 0.0, 0.0)
    gid = next(g for g, label in be.label_map().items() if label == "crack" and be.model.geom(g).name == "defect_D01")
    seg = be.render("front", segmentation=True)
    mask = seg[:, :, 0] == gid
    assert mask.sum() > 150
    ys, xs = np.where(mask)
    assert xs.max() - xs.min() > 20 and ys.max() - ys.min() > 10
    assert {be.model.geom(g).name for g in be.label_map()} >= {"defect_D01", "defect_D02"}
    assert set(be.label_map().values()) == {"crack", "leak"}


def test_viewer_scene_shows_the_pipes_see_through_without_the_hidden_floor_plane():
    s = sim_for()
    s.reset(seed=1)
    sc = s.backend.scene()
    kinds = Counter(g["type"] for g in sc["geoms"])
    assert kinds["plane"] == 0 and kinds["mesh"] > 20
    walls = [g for g in sc["geoms"] if g["name"].startswith("pipe_e1_wall")]
    assert walls and walls[0]["rgba"][3] <= 0.3
    assert all(g["rgba"][3] < 0.5 for g in sc["geoms"] if g["name"].startswith("slab"))


# ---------------------------------------------------------------- physics
def test_the_vehicle_cannot_leave_the_network():
    s = sim_for("pipe_star")
    m = s.task.map
    s.reset(seed=1)
    rng = np.random.default_rng(0)
    vmax, wmax = s.vehicle.params["max_speed"], s.vehicle.params["max_yaw_rate"]
    outside, bumps, w = 0, 0, 0.0
    for i in range(1500):                                                       # full speed with random steering, bouncing off whatever it meets
        if i % 15 == 0:
            w = float(rng.uniform(-wmax, wmax))
        s.step_physical([vmax, w])
        bumps += bool(s.obs.state.collided)
        if not in_network(m, float(s.obs.state.pos[0]), float(s.obs.state.pos[1])):
            outside += 1
    assert bumps > 20 and outside == 0                                          # it did hit walls, and never got through one


def test_driving_straight_into_a_chamber_wall_stops_there():
    s = sim_for()
    s.reset(seed=1)
    be = s.backend
    be.teleport(0.0, 0.0, 0.0, math.pi)                                         # at the entry chamber, facing its blank west wall
    hit = False
    for _ in range(150):
        s.step_physical([s.vehicle.params["max_speed"], 0.0])
        hit = hit or s.obs.state.collided
    ap = s.task.map.chambers["A"].apothem
    assert hit and s.obs.state.pos[0] > -ap - 0.05 and abs(s.obs.state.vel_body[0]) < 0.1


def test_range_rays_see_the_walls_but_not_the_floor():
    s = sim_for()
    s.reset(seed=1)
    d = s.backend.teleport(4.0, 0.0, 0.0, math.pi / 2).sensors["range"]         # across the pipe: the side wall is about half a pipe diameter away
    assert 0.2 < d[len(d) // 2] < 0.9
    d = s.backend.teleport(4.0, 0.0, 0.0, 0.0).sensors["range"]
    assert d[len(d) // 2] > 3.0                                                  # looking down the pipe: far (and no phantom floor hit)


# ---------------------------------------------------------------- the task
@pytest.mark.parametrize("task,limit", [("pipe_straight", 1.2), ("pipe_star", 1.2)])
def test_the_baseline_drives_to_the_goal_without_touching_the_walls(task, limit):
    s = sim_for(task)
    for seed in range(3):
        s.reset(seed=seed)
        pol = make_policy("baseline", s)
        pol.reset(s)
        out = None
        for _ in range(int(s.task.max_time * 10) + 5):
            out = s.step_physical(pol.act(s))
            if out[2] or out[3]:
                break
        assert out[4]["success"] and out[4]["collisions"] == 0
        assert s.obs.state.t < s.task.max_time / 1.5 * limit


def test_a_smaller_rover_also_makes_it():
    s = sim_for("pipe_straight", "micro_2wd")
    s.reset(seed=1)
    pol = make_policy("baseline", s)
    pol.reset(s)
    out = None
    for _ in range(int(s.task.max_time * 10) + 5):
        out = s.step_physical(pol.act(s))
        if out[2] or out[3]:
            break
    assert out[4]["success"]


def test_same_seed_same_world_same_trajectory():
    def run():
        s = sim_for("pipe_star")
        s.reset(seed=4)
        pol = make_policy("baseline", s)
        pol.reset(s)
        return np.array([s.step_physical(pol.act(s))[0] for _ in range(120)])
    assert np.array_equal(run(), run())


def test_observations_rewards_and_the_gym_interface():
    from simlab.envs.gym_env import SimEnv
    env = SimEnv("mid_4wd", "pipe_straight", seed=0)
    obs, _ = env.reset(seed=0)
    assert obs.shape == env.observation_space.shape == (13,) and np.all(np.isfinite(obs))
    total = 0.0
    for _ in range(40):
        obs, r, term, trunc, info = env.step(np.array([1.0, 0.0], np.float32))
        total += r
        assert np.all(np.isfinite(obs)) and env.observation_space.contains(obs)
    assert total > 0 and info["terms"]["progress"] > 0                          # driving down the route earns progress
    spin = SimEnv("mid_4wd", "pipe_straight", seed=0)
    spin.reset(seed=0)
    assert sum(spin.step(np.array([0.0, 1.0], np.float32))[1] for _ in range(40)) < total      # spinning on the spot earns nothing


def test_a_task_that_cannot_be_built_says_why():
    def make(**params):
        return Sim(get_vehicle("mid_4wd"), TaskSpec.from_dict({"name": "t", "task": "pipe_inspect", "params": params}))
    with pytest.raises(SpecError, match="no map file"):
        make(map="nowhere")
    with pytest.raises(SpecError, match="no goal"):
        make(goal="GOAL_NOPE")
    with pytest.raises(SpecError, match="ground"):
        Sim(get_vehicle("quad_250"), get_task_spec("pipe_straight"))
    goals = list(load_map(ROOT / "maps" / "star_five_goals.yaml").goals)
    assert make(map="star_five_goals", goal=goals[-1]).task.goal_id == goals[-1]


def test_requirements_include_the_pipes_the_vehicle_must_fit():
    from simlab.registry import TASKS
    ts = get_task_spec("pipe_star")
    reqs = TASKS.get("pipe_inspect")(ts, get_vehicle("mid_4wd")).requirements()
    assert {r.kind for r in reqs} >= {"sensor", "light", "endurance", "fit"}
    fit = next(r for r in reqs if r.kind == "fit")
    assert fit.params["start_ok"] and all(f["fits"] for f in fit.params["fits"].values())
    big = TASKS.get("pipe_inspect")(ts, get_vehicle("large_6wd")).requirements()
    assert isinstance(next(r for r in big if r.kind == "fit").params["start_ok"], bool)


# ---------------------------------------------------------------- datasets and the inspection mission
def test_captured_pipe_datasets_have_exact_boxes_and_never_share_a_defect_between_splits(tmp_path):
    import json
    from simlab.perception.capture import capture
    meta = capture(get_vehicle("mid_4wd"), get_task_spec("pipe_star"), tmp_path, images=48, seed=1, per_scene=3)
    seen, boxes = {}, Counter()
    for split in ("train", "valid", "test"):
        coco = json.loads((tmp_path / split / "_annotations.coco.json").read_text())
        assert [c["name"] for c in coco["categories"]] == ["crack", "leak"]
        for im in coco["images"]:
            seen.setdefault(im["group"], set()).add(split)
        for a in coco["annotations"]:
            x, y, w, h = a["bbox"]
            assert w >= 6 and h >= 6 and 0 <= x and 0 <= y
            boxes[a["category_id"]] += 1
    assert all(len(v) == 1 for v in seen.values())                              # a group (one defect, or a stretch of clear pipe) lives in one split only
    assert boxes[1] > 5 and boxes[2] > 5 and meta["stats"]["train"]["images"] > meta["stats"]["test"]["images"] > 0


def test_pipe_appearance_randomisation_keeps_walls_and_patches_matching():
    s = sim_for()
    s.reset(seed=1)
    be = s.backend
    rng = np.random.default_rng(3)
    be.randomize_appearance(rng, 1.0)
    m = be.model
    walls = [g for g in range(m.ngeom) if m.geom(g).name.startswith(("pipe_e1_wall", "defect_D01"))]
    assert len({tuple(np.round(m.mat_rgba[int(m.geom_matid[g]), :3], 4)) for g in walls if m.geom_matid[g] >= 0 and "concrete" in m.material(int(m.geom_matid[g])).name}) == 1
    assert all(int(m.geom_matid[g]) >= 0 and np.allclose(m.geom_rgba[g, :3], 0.5) for g in walls)      # textured surfaces keep their texture colours
    assert (m.mat_rgba[:, :3] > 0.5).all()                                      # a mild tint, never a colour cast


def test_inspect_everything_finds_the_planted_defects_with_a_perfect_detector():
    from simlab.tasks.pipe_eval import inspect_all
    r = inspect_all("mid_4wd", "pipe_short", "oracle", frame_every=3)
    ev = r["evaluation"]
    assert r["finished"] == "arrived" and r["route"][0] == r["route"][-1] == "A"           # out to the end and back
    assert r["planted"] == 4 and ev["true_positive"] == 4 and ev["false_positive"] == 0 and ev["recall"] == 1.0
    assert ev["per_class"]["crack"]["found"] == 2 and ev["per_class"]["leak"]["found"] == 2
