"""Every mission step in the simulator, driven only by the 96x96 camera and the gyro through bounded drive commands:
go_to, follow (keeps the gap), orbit (laps from heading), bag -> bag -> box, a lost target stops the rover, limits, faults, a new target."""
import math

import numpy as np
import pytest

pytest.importorskip("mujoco")

from simlab import get_task_spec  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.missions import Mission, TargetRegistry  # noqa: E402
from simlab.missions.runner import DriveModel  # noqa: E402
from simlab.missions.sim import MissionSim  # noqa: E402
from simlab.missions.targets import STANDINS, StandIn, _draw_standin, to_frame  # noqa: E402
from simlab.spec import SpecError  # noqa: E402

OPEN = {"environment": "open_floor"}


def mission(*steps) -> Mission:
    return Mission.from_dict({"name": "test", "steps": list(steps)})


def go_to(target, **kw):
    return {"op": "go_to", "target": target, "stop_at_cm": 30, **kw}


def run(m, world=None, **kw):
    with MissionSim(m, world=world, **kw) as ms:
        r = ms.run()
        return r, ms


def true_laps(r, ms, target):
    """Laps around the target from the simulator's true positions (the runner never sees these)."""
    ang = [math.atan2(row["y"] - row["objects"][target][1], row["x"] - row["objects"][target][0]) for row in r.trace]
    return sum((b - a + math.pi) % (2 * math.pi) - math.pi for a, b in zip(ang, ang[1:])) / (2 * math.pi)


def camera_radius(r, ms, target):
    """Camera-to-centre distance of every trace row from the moment the runner reported being on the circle."""
    t0 = next(e["t"] for e in r.events if e["text"].startswith("on the circle"))
    return [math.dist(ms.camera_xy(row), row["objects"][target]) for row in r.trace if row["t"] >= t0]


def assert_bounded(r, model=DriveModel()):
    """Every command the runner issued is one the firmware's bounded D command can carry."""
    assert r.trace
    for row in r.trace:
        left, right, ms = row["drive"]
        assert -255 <= left <= 255 and -255 <= right <= 255 and 0 <= ms <= model.max_ms
        assert all(d == 0 or abs(d) >= model.min_duty for d in (left, right))


# ---------------------------------------------------------------- the camera sees what the geometry says
def test_the_camera_gives_distance_and_bearing_from_the_real_size():
    m = mission(go_to("bag_a"))
    with MissionSim(m, world={**OPEN, "objects": [{"target": "bag_a", "at": [3.0, 0.0]}]}) as ms:
        ms.sim.reset(seed=0)
        bag, fwd = ms.registry.get("bag_a"), ms.sim.vehicle.sensor("camera").options["forward"]
        for gap in (0.3, 0.6, 1.0, 1.5, 2.0, 2.5):
            for yaw in (0.0, 0.3, -0.3):
                x = 3.0 - bag.footprint_m - gap - fwd                       # the camera, not the body centre, is `gap` from the bag
                ms.sim.backend.teleport(x, 0.0, 0.0, yaw)
                s = bag.locate(ms.sim.backend.render(ms.camera_name), ms.camera)
                assert s is not None, (gap, yaw)
                cx, cy = x + fwd * math.cos(yaw), fwd * math.sin(yaw)
                true_bearing = math.atan2(0.0 - cy, 3.0 - cx) - yaw                    # from the camera to the bag's centre, relative to where it points
                one_pixel = gap * gap / (ms.camera.focal_px * bag.height_cm / 100.0)       # how much farther a blob one pixel shorter would be
                assert abs(s.distance - math.hypot(3.0 - cx, cy) + bag.footprint_m) <= 0.08 * gap + 0.02 + one_pixel, (gap, yaw, s.distance)
                assert s.bearing == pytest.approx(true_bearing, abs=0.04)


def test_an_empty_scene_shows_no_target_and_the_rover_does_not_see_itself():
    with MissionSim(mission(go_to("bag_a")), world={"objects": []}) as ms:
        ms.sim.reset(seed=0)
        frame = ms.sim.backend.render(ms.camera_name)
        assert frame.shape == (96, 96, 3)
        assert all(ms.registry.get(t).locate(frame, ms.camera) is None for t in STANDINS)


# ---------------------------------------------------------------- go_to and stop_at
def test_go_to_stops_at_the_requested_distance_without_touching():
    r, ms = run(mission(go_to("bag_a", stop_at_cm=30)))
    assert r.success and r.status == "done" and r.collisions == 0 and r.task["success"] and r.sim_time < 40
    assert abs(ms.true_gap(r.trace[-1], "bag_a") - 0.30) <= 0.05
    assert_bounded(r)
    last = r.trace[-2:]
    assert last[-1]["drive"] == (0, 0, 0) and math.dist((last[0]["x"], last[0]["y"]), (last[-1]["x"], last[-1]["y"])) < 0.05       # standing still


def test_go_to_turns_to_find_a_target_that_is_behind_it():
    r, ms = run(mission(go_to("bag_a")), world={"objects": [{"target": "bag_a", "at": [-1.2, 0.3]}]})
    assert r.success and abs(ms.true_gap(r.trace[-1], "bag_a") - 0.30) <= 0.05 and r.collisions == 0


def test_stop_at_confirms_the_goal_and_ends_the_mission():
    r, ms = run(mission({"op": "stop_at", "target": "box", "within_cm": 35}), world={"environment": "small_room"})
    assert r.success and r.collisions == 0
    assert ms.true_gap(r.trace[-1], "box") <= 0.40
    assert any("stop_at box done" in e["text"] for e in r.events) and r.events[-1]["text"] == "mission complete"


# ---------------------------------------------------------------- follow
def test_follow_keeps_the_gap_behind_a_moving_bag():
    carried = {"target": "bag_a", "at": [1.0, 0.0], "path": [[2.2, 0.0], [2.2, 1.2]], "speed": 0.05}
    r, ms = run(mission({"op": "follow", "target": "bag_a", "gap_cm": 40, "duration_s": 40}), world={**OPEN, "objects": [carried]})
    assert r.success and r.collisions == 0 and 40 <= r.sim_time <= 45
    start, end = r.trace[0]["objects"]["bag_a"], r.trace[-1]["objects"]["bag_a"]
    assert math.dist(start, end) > 1.3                                           # the bag really moved
    gaps = [ms.true_gap(row, "bag_a") for row in r.trace if row["t"] >= 12]     # after the first approach
    assert max(gaps) <= 0.40 + 0.10 and min(gaps) >= 0.40 - 0.10, (min(gaps), max(gaps))
    assert abs(float(np.median(gaps)) - 0.40) <= 0.06
    assert_bounded(r)


def test_follow_does_not_reverse_when_the_bag_comes_closer_than_the_gap():
    carried = {"target": "bag_a", "at": [1.0, 0.0], "path": [[0.45, 0.0]], "speed": 0.08}      # walks toward the rover
    r, ms = run(mission({"op": "follow", "target": "bag_a", "gap_cm": 40, "duration_s": 12}), world={**OPEN, "objects": [carried]})
    assert all(row["drive"][0] >= 0 and row["drive"][1] >= 0 for row in r.trace)               # never backs up blind


# ---------------------------------------------------------------- orbit
@pytest.mark.parametrize("direction", ["ccw", "cw"])
def test_orbit_counts_a_lap_from_the_gyro_heading_and_keeps_the_radius(direction):
    r, ms = run(mission({"op": "orbit", "target": "bag_a", "radius_cm": 40, "laps": 1, "direction": direction}), world=OPEN)
    sign = 1 if direction == "ccw" else -1
    assert r.success and r.collisions == 0
    assert 0.9 <= r.laps <= 1.15                                                  # counted from heading: 360 degrees = 1 lap
    assert sign * true_laps(r, ms, "bag_a") == pytest.approx(r.laps, abs=0.12)    # and it agrees with the real geometry
    rad = camera_radius(r, ms, "bag_a")                                      # once on the circle
    assert abs(float(np.mean(rad)) - 0.40) <= 0.05 and max(rad) <= 0.40 + 0.12 and min(rad) >= 0.40 - 0.12, (min(rad), max(rad))
    assert_bounded(r)


def test_orbit_runs_several_laps_even_with_a_gyro_bias_and_a_weak_motor():
    r, ms = run(mission({"op": "orbit", "target": "bag_a", "radius_cm": 40, "laps": 2}), world=OPEN, gyro_bias=0.003,
                hazards={"side_gain": (0.85, 1.0)})
    assert r.success and r.collisions == 0
    assert r.laps == pytest.approx(2.0, abs=0.15) and true_laps(r, ms, "bag_a") == pytest.approx(2.0, abs=0.25)
    rad = camera_radius(r, ms, "bag_a")
    assert 0.40 - 0.12 <= min(rad) and max(rad) <= 0.40 + 0.15


def test_orbit_can_run_a_wider_circle():
    r, ms = run(mission({"op": "orbit", "target": "bag_a", "radius_cm": 60, "laps": 1}), world=OPEN)
    assert r.success and abs(float(np.mean(camera_radius(r, ms, "bag_a"))) - 0.60) <= 0.07


# ---------------------------------------------------------------- missions made of several steps
BAG_TO_BOX = [go_to("bag_a"), go_to("bag_b"), {"op": "stop_at", "target": "box", "within_cm": 35}]


def test_bag_to_bag_to_box_completes_in_order():
    r, ms = run(mission(*BAG_TO_BOX))
    assert r.success and r.collisions == 0 and r.task["success"]
    assert [e["step"] for e in r.events if e["text"].startswith("start")] == [1, 2, 3]
    for step_no, target in ((2, "bag_a"), (3, "bag_b")):                         # when a step ended, the rover stood at that step's target
        first = next(row for row in r.trace if row["step"] == step_no)
        assert ms.true_gap(first, target) <= 0.40, (step_no, target)
    assert ms.true_gap(r.trace[-1], "box") <= 0.40
    assert_bounded(r)


def test_laps_then_the_box():
    r, ms = run(mission({"op": "orbit", "target": "bag_a", "radius_cm": 40, "laps": 1}, {"op": "stop_at", "target": "box", "within_cm": 35}),
                world={"environment": "tile_floor"})
    assert r.success and r.collisions == 0 and r.laps >= 0.9 and ms.true_gap(r.trace[-1], "box") <= 0.40


@pytest.mark.parametrize("faults", [
    {"hazards": {"side_gain": (0.85, 1.0)}},                 # a weaker left motor
    {"hazards": {"side_gain": (1.0, 0.8)}},                  # a weaker right motor
    {"hazards": {"cmd_delay_s": 0.2}},                       # a slow link
    {"latency_s": 0.3},                                      # a camera frame that is 0.3 s old when the runner sees it
    {"gyro_bias": 0.004},
])
def test_the_chain_survives_the_faults_a_real_rover_has(faults):
    r, ms = run(mission(*BAG_TO_BOX), **faults)
    assert r.success and r.collisions == 0, (r.status, r.message)
    assert ms.true_gap(r.trace[-1], "box") <= 0.45


# ---------------------------------------------------------------- safety: lost target, not found, limits
@pytest.mark.parametrize("step_, world", [
    (go_to("bag_a"), None),
    ({"op": "follow", "target": "bag_a", "gap_cm": 40, "duration_s": 60}, OPEN),
    ({"op": "orbit", "target": "bag_a", "radius_cm": 40, "laps": 2}, OPEN),
])
def test_a_lost_target_stops_the_rover(step_, world):
    def pick_up_the_bag(ms, tick, runner):
        if tick == 6:
            ms.hide("bag_a")
    with MissionSim(mission(step_), world=world) as ms:
        r = ms.run(on_tick=pick_up_the_bag)
    assert r.status == "failed" and r.reason == "lost_target" and "bag_a" in r.message
    assert r.trace[-1]["drive"] == (0, 0, 0)
    held = r.trace[8:]                                                            # from the moment it noticed: no command moves the wheels
    assert held and all(row["drive"] == (0, 0, 0) for row in held)
    assert math.dist((held[0]["x"], held[0]["y"]), (held[-1]["x"], held[-1]["y"])) < 0.03
    assert r.sim_time < 12                                                        # it gave up within a few looks, not at the time limit


def test_a_target_that_is_not_in_the_room_is_reported_after_one_turn():
    r, _ = run(mission(go_to("bag_b")), world={"objects": [{"target": "bag_a", "at": [1.0, 0.0]}]})
    assert r.status == "failed" and r.reason == "target_not_found" and "bag_b" in r.message
    assert r.trace[-1]["drive"] == (0, 0, 0) and r.sim_time < 40


def test_every_step_has_a_distance_limit_and_a_time_limit():
    r, ms = run(mission(go_to("bag_a", max_distance_cm=40)))
    assert r.status == "failed" and r.reason == "distance_limit" and r.path_true_m < 0.40 * 2        # it stopped soon after the limit
    r, _ = run(mission(go_to("bag_a", max_time_s=4)))
    assert r.status == "failed" and r.reason == "timeout" and r.sim_time < 7
    r, _ = run(mission(go_to("bag_a")), world={"objects": [{"target": "bag_a", "at": [1.0, 0.0]}]})
    assert r.success                                                                            # limits left at their defaults do not get in the way


def test_the_dead_reckoned_path_is_close_to_the_true_path():
    r, _ = run(mission(*BAG_TO_BOX))
    assert 0.75 * r.path_true_m <= r.path_dead_reckoned_m <= 1.15 * r.path_true_m


# ---------------------------------------------------------------- the task, and objects that need no code
def test_the_task_is_a_registered_preset_with_a_named_environment():
    from simlab.dashboard.catalog import build_catalog
    entry = build_catalog()["tasks"]["bag_mission"]
    assert entry["task"] == "bag_mission" and entry["spec"]["world"]["environment"] == "tile_floor" and entry["baseline"] is False
    from simlab import get_vehicle
    v = get_vehicle("micro_2wd")
    for env in ("tile_floor", "open_floor", "small_room"):
        spec = get_task_spec("bag_mission").to_dict() | {"world": {"environment": env}}
        from simlab.spec import TaskSpec
        sim = Sim(v, TaskSpec.from_dict(spec), seed=1)
        vec = sim.reset(seed=1)
        assert vec.shape == (sim.obs_dim,) and np.isfinite(vec).all() and sim.task.markers(sim.episode)
        sim.close()


def test_the_task_refuses_what_it_cannot_run():
    from simlab import TaskSpec, get_vehicle
    base = get_task_spec("bag_mission").to_dict()
    with pytest.raises(SpecError, match="unknown target"):
        Sim(get_vehicle("micro_2wd"), TaskSpec.from_dict(base | {"world": {"objects": [{"target": "dragon", "at": [1, 0]}]}}))
    with pytest.raises(SpecError, match="inside the"):
        Sim(get_vehicle("micro_2wd"), TaskSpec.from_dict(base | {"world": {"objects": [{"target": "bag_a", "at": [99, 0]}]}}))
    with pytest.raises(SpecError, match="drives ground vehicles"):
        Sim(get_vehicle("quad_250"), TaskSpec.from_dict(base))
    blind = get_vehicle("micro_2wd").to_dict()
    blind["sensors"] = []
    from simlab import VehicleSpec
    with pytest.raises(SpecError, match="no camera"):
        Sim(VehicleSpec.from_dict(blind), TaskSpec.from_dict(base))


def test_a_new_object_from_photos_works_in_the_simulator_without_code_changes(tmp_path, monkeypatch):
    """A yellow cone learned from photos is placed in the scene (its size and colour come from the target) and driven to."""
    monkeypatch.setenv("SIMLAB_TARGETS", str(tmp_path))
    cone = StandIn("Yellow cone", "waypoint", "cylinder", (240, 215, 30), 20.0, 30.0)
    rng = np.random.default_rng(3)
    TargetRegistry(tmp_path).add("cone", "Yellow cone", "waypoint", 20, 30, [_draw_standin(cone, rng) for _ in range(5)])
    r, ms = run(mission(go_to("cone", stop_at_cm=35)), world={"objects": [{"target": "cone", "at": [1.5, 0.4]}, {"target": "bag_a", "at": [1.0, -1.0]}]})
    assert r.success and r.collisions == 0, (r.status, r.message)
    assert abs(ms.true_gap(r.trace[-1], "cone") - 0.35) <= 0.06


def test_two_bags_of_different_colours_are_not_confused():
    both = {"objects": [{"target": "bag_a", "at": [1.2, -0.5]}, {"target": "bag_b", "at": [1.2, 0.5]}]}
    for want, other in (("bag_a", "bag_b"), ("bag_b", "bag_a")):
        r, ms = run(mission(go_to(want)), world=both)
        assert r.success and ms.true_gap(r.trace[-1], want) <= ms.true_gap(r.trace[-1], other)
        assert abs(ms.true_gap(r.trace[-1], want) - 0.30) <= 0.06
