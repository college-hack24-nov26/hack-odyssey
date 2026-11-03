"""MuJoCo backend: ground dynamics against the vehicle spec, determinism, sensors, scene export, baseline controller."""
import json
import math
import time

import numpy as np
import pytest

pytest.importorskip("mujoco")

from simlab import TaskSpec, VehicleSpec, get_task_spec, get_vehicle  # noqa: E402
from simlab.control import Manual, make_policy  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.spec import SpecError  # noqa: E402

GROUND = ("micro_2wd", "mid_4wd", "tracked_crawler", "large_6wd")


def make(vehicle="mid_4wd", task="goto_empty", **kw):
    return Sim(get_vehicle(vehicle) if isinstance(vehicle, str) else vehicle, get_task_spec(task), **kw)


# ---------------------------------------------------------------- dynamics follow the spec
@pytest.mark.parametrize("name", GROUND)
def test_speed_acceleration_and_spin_respect_the_vehicle_limits(name):
    v = get_vehicle(name)
    sim = make(name)
    sim.reset(seed=1)
    vmax, amax, wmax = v.params["max_speed"], v.params["max_accel"], v.params["max_yaw_rate"]
    prev = peak = 0.0
    for _ in range(int(2.5 * vmax / amax / sim.backend.policy_dt) + 40):
        sim.step_physical([vmax * 5, 0.0])                         # asks for far too much: must be clipped
        vb = sim.obs.state.vel_body
        peak = max(peak, (vb[0] - prev) / sim.backend.policy_dt)
        prev = vb[0]
        assert abs(vb[1]) < 0.02 * vmax + 1e-3                     # no sideways drift
    assert prev == pytest.approx(vmax, rel=0.02)
    assert peak <= amax * 1.05
    sim.reset(seed=1)
    for _ in range(80):
        sim.step_physical([0.0, wmax * 5])
    assert sim.obs.state.omega_body[2] == pytest.approx(wmax, rel=0.03)
    assert abs(sim.obs.state.vel_body[0]) < 1e-2 * vmax + 1e-4      # a true spin in place


def test_wheels_space_applies_the_measured_yaw_gain():
    """Raw left/right commands (no mixer compensation) turn at yaw_gain x the ideal differential rate."""
    v = get_vehicle("mid_4wd")
    sim = make("mid_4wd", space="wheels")
    sim.reset(seed=2)
    s = 0.2
    for _ in range(80):
        sim.step_physical([-s, s])
    track = sim.backend.plant.track
    assert sim.obs.state.omega_body[2] == pytest.approx(v.params["yaw_gain"] * 2 * s / track, rel=0.03)


def test_unicycle_mixer_compensates_skid_steer_slip():
    sim = make("large_6wd")                                          # yaw_gain 0.655: it would turn at 65% without the mixer
    sim.reset(seed=2)
    w = 0.6
    for _ in range(80):
        sim.step_physical([0.0, w])
    assert sim.obs.state.omega_body[2] == pytest.approx(w, rel=0.03)


# ---------------------------------------------------------------- determinism and reset
def run_trace(seed, steps=60):
    sim = make("mid_4wd", "goto_clutter", seed=seed)
    sim.reset(seed=seed)
    pol = make_policy("baseline", sim)
    out = []
    for _ in range(steps):
        sim.step_physical(pol.act(sim))
        out.append(np.concatenate([sim.obs.state.pos, sim.obs.sensors["range"], [sim.obs.state.yaw]]))
    return np.array(out)


def test_same_seed_same_trajectory_different_seed_differs():
    a, b, c = run_trace(11), run_trace(11), run_trace(12)
    assert np.array_equal(a, b)
    assert not np.allclose(a, c)


def test_reset_honours_the_placements_and_keeps_the_spawn_clear():
    sim = make("mid_4wd", "goto_clutter")
    for seed in range(6):
        sim.reset(seed=seed)
        ep, task, be = sim.episode, sim.task, sim.backend
        m, d = be.model, be.data
        for name, p in ep.placements.items():
            mid = int(m.body_mocapid[m.body(name).id])
            assert tuple(d.mocap_pos[mid][:2]) == pytest.approx(p[:2])
            if len(p) == 3:                                           # placed inside the arena, so it must stand on the floor
                assert 0 <= d.mocap_pos[mid][2] < 1.0, f"{name} is not standing on the floor (z={d.mocap_pos[mid][2]})"
        sx, sy = ep.spawn[:2]
        for name, p in ep.placements.items():
            if name.startswith("obstacle") and len(p) == 3:
                assert math.hypot(p[0] - sx, p[1] - sy) > task.radius + 0.4
        gx, gy = ep.info["goal"]
        assert math.hypot(gx - sx, gy - sy) <= task.p["goal_max_dist"] + 1e-6
        assert abs(gx) < task.wp["width"] / 2 and abs(gy) < task.wp["depth"] / 2


def test_randomisation_changes_mass_within_bounds():
    sim = make("mid_4wd", "goto_clutter")                             # randomize.mass = 0.2
    masses = []
    for seed in range(8):
        sim.reset(seed=seed)
        masses.append(sim.backend.plant.m)
    base = get_vehicle("mid_4wd").body["mass"]
    assert len(set(np.round(masses, 6))) > 4 and all(0.8 * base - 1e-9 <= m <= 1.2 * base + 1e-9 for m in masses)


# ---------------------------------------------------------------- contact and sensors
def test_driving_into_the_wall_is_flagged_and_contained():
    sim = make("mid_4wd")
    be = sim.backend
    sim.reset(seed=1)
    be.reset((5.0, 0.0, 0.0, 0.0), {}, {}, rng=sim.rng)               # facing the east wall (inner face at x = 7)
    hit = False
    for _ in range(200):
        obs = be.step([0.5, 0.0])
        hit = hit or obs.state.collided
    assert hit and obs.state.pos[0] < 7.0 - 0.1 and abs(obs.state.vel_body[0]) < 0.1


def test_a_placed_obstacle_is_solid_and_seen_by_the_range_sensor():
    sim = make("mid_4wd", "goto_open")
    be = sim.backend
    sim.reset(seed=1)
    park = {n: (0.0, 0.0, 0.0, -10.0) for n in sim.episode.placements if n.startswith("obstacle")}
    be.reset((-3.0, 0.0, 0.0, 0.0), {**park, "obstacle0": (0.0, 0.0, 0.0)}, {}, rng=sim.rng)   # one obstacle dead ahead
    r = be.step([0.0, 0.0]).sensors["range"]
    assert r[len(r) // 2] < 3.0 - 0.17                                 # the centre ray sees something nearer than the far wall
    hit, x = False, -3.0
    for _ in range(200):
        obs = be.step([0.5, 0.0])
        hit, x = hit or obs.state.collided, obs.state.pos[0]
    assert hit and x < 0.0, "the rover drove through an obstacle"


def test_range_rays_measure_the_wall():
    sim = make("mid_4wd")
    be = sim.backend
    sim.reset(seed=1)
    obs = be.reset((5.0, 0.0, 0.0, 0.0), {}, {}, rng=sim.rng)
    r = obs.sensors["range"]
    centre = r[len(r) // 2]
    assert centre == pytest.approx(7.0 - 5.0 - 0.175, abs=0.03)       # wall face - x - half the vehicle length
    assert r.min() <= centre + 1e-9 and r.max() <= sim.task.max_range + 1e-9
    obs = be.reset((5.0, 0.0, 0.0, math.pi), {}, {}, rng=sim.rng)     # facing away: the far wall is out of range
    assert obs.sensors["range"][len(r) // 2] == pytest.approx(sim.task.max_range)


def test_pose_and_imu_sensors_report_truth_plus_noise():
    d = get_vehicle("mid_4wd").to_dict()                              # ground presets carry only a camera: add pose + imu
    d["sensors"] += [{"type": "pose", "name": "pose", "pos_noise": 0.05}, {"type": "imu", "name": "imu"}]
    rover = VehicleSpec.from_dict(d)
    sim = Sim(rover, get_task_spec("goto_empty"), seed=3)
    sim.reset(seed=3)
    for _ in range(40):
        sim.step_physical([0.4, 0.3])
    st, s = sim.obs.state, sim.obs.sensors
    assert np.allclose(s["pose"][:3], st.pos, atol=0.25) and not np.allclose(s["pose"][:3], st.pos, atol=1e-9)
    assert s["imu"]["gyro"][2] == pytest.approx(st.omega_body[2], abs=0.05)
    assert s["imu"]["accel"][2] == pytest.approx(9.81, abs=0.5)        # standing still vertically: specific force = g


def test_camera_renders_and_segments():
    sim = make("mid_4wd", "goto_open")
    sim.reset(seed=4)
    img = sim.backend.render("front")
    assert img.shape == (480, 640, 3) and img.dtype == np.uint8 and 20 < img.mean() < 235
    seg = sim.backend.render("front", segmentation=True)
    assert seg.shape == (480, 640, 2) and (seg[..., 0] >= -1).all()
    sim.close()


# ---------------------------------------------------------------- scene export for the 3D viewer
def test_scene_and_poses_describe_the_same_bodies():
    sim = make("tracked_crawler", "goto_open")
    sim.reset(seed=1)
    sc, poses = sim.backend.scene(), sim.backend.poses()
    json.dumps(sc)                                                   # must be serialisable as-is
    assert poses.shape == (len(sc["bodies"]), 7)
    assert all(0 <= g["body"] < len(sc["bodies"]) for g in sc["geoms"])
    assert sc["bodies"][sc["vehicle_body"]] == "vehicle"
    types = {g["type"] for g in sc["geoms"]}
    assert {"plane", "box", "cylinder"} <= types
    assert sum(g["name"].startswith("wheel") or g["type"] == "cylinder" for g in sc["geoms"]) >= 8      # tracked: 8 road wheels
    p0 = poses.copy()
    sim.step_physical([0.3, 0.0])
    assert not np.allclose(sim.backend.poses()[sc["vehicle_body"]], p0[sc["vehicle_body"]])


# ---------------------------------------------------------------- task contract
def test_goto_rejects_the_wrong_vehicle_and_bad_settings():
    with pytest.raises(SpecError, match="ground vehicles"):
        Sim(get_vehicle("quad_250"), get_task_spec("goto_open"))
    bad = TaskSpec.from_dict({"name": "x", "task": "goto", "reward": {"speed": 1.0}, "params": {"goal_min_dist": 9, "goal_max_dist": 3}})
    with pytest.raises(SpecError) as e:
        Sim(get_vehicle("mid_4wd"), bad)
    fields = {i.field for i in e.value.issues}
    assert {"reward.speed", "params.goal_max_dist"} <= fields


def test_normalised_and_physical_steps_agree():
    a, b = make("mid_4wd", seed=1), make("mid_4wd", seed=1)
    a.reset(seed=5), b.reset(seed=5)
    for _ in range(20):
        a.step([0.6, -0.4])
        b.step_physical(b.space.denormalise([0.6, -0.4]))
    assert np.allclose(a.obs.state.pos, b.obs.state.pos)


def test_episode_ends_on_success_or_timeout_with_reward_terms():
    sim = make("mid_4wd", "goto_empty")
    pol = make_policy("baseline", sim)
    sim.reset(seed=8)
    done, steps = False, 0
    while not done:
        _, r, term, trunc, info = sim.step_physical(pol.act(sim))
        done, steps = term or trunc, steps + 1
        assert set(info["terms"]) == {"progress", "reached", "collision", "time", "action"}
        assert r == pytest.approx(sum(info["terms"].values()))
    assert term and info["success"] and info["dist"] < sim.task.goal_r
    idle = make("mid_4wd", "goto_empty")
    idle.reset(seed=8)
    for _ in range(int(idle.episode.info["max_time"] / idle.backend.policy_dt) + 5):
        _, _, term, trunc, info = idle.step_physical([0.0, 0.0])
        if trunc:
            break
    assert trunc and not term and not info["success"]


def test_manual_policy_stops_when_the_operator_goes_quiet():
    sim = make("mid_4wd")
    sim.reset(seed=1)
    m = Manual()
    m.set({"v": 0.3, "w": 0.1})
    assert np.allclose(m.act(sim), [0.3, 0.1])
    time.sleep(Manual.TIMEOUT + 0.1)
    assert np.allclose(m.act(sim), [0.0, 0.0])


# ---------------------------------------------------------------- the baseline works on every rover
@pytest.mark.parametrize("name", GROUND)
def test_baseline_reaches_goals_with_every_rover(name):
    sim = make(name, "goto_open", seed=5)
    pol = make_policy("baseline", sim)
    wins = 0
    for ep in range(6):
        sim.reset(seed=1000 + ep)
        pol.reset(sim)
        while True:
            _, _, term, trunc, info = sim.step_physical(pol.act(sim))
            if term or trunc:
                wins += info["success"]
                break
    assert wins >= 5, f"{name}: only {wins}/6 goals reached"


def test_simulation_is_fast_enough_to_train_on():
    sim = make("mid_4wd", "goto_open")
    sim.reset(seed=1)
    t0 = time.perf_counter()
    n = 600
    for _ in range(n):
        sim.step([0.5, 0.1])
        if sim.t > 20:
            sim.reset()
    assert n / (time.perf_counter() - t0) > 1500                        # policy steps per second on one core
