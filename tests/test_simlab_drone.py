"""Multirotors: autopilot, flight dynamics on the MuJoCo plant, aerial tasks and their baselines."""
import math

import numpy as np
import pytest

pytest.importorskip("mujoco")

from simlab import TaskSpec, get_task_spec, get_vehicle  # noqa: E402
from simlab.backends.mujoco import MujocoBackend  # noqa: E402
from simlab.control import make_policy  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.spec import SpecError  # noqa: E402
from simlab.tasks.base import World  # noqa: E402
from simlab.vehicles.multirotor import Autopilot, allocation_matrix, euler  # noqa: E402

DRONES = ("quad_micro", "quad_250", "hex_450")
OPEN_SKY = World([], (40.0, 40.0))


def fly(name="quad_250", seed=0, **overrides):
    be = MujocoBackend(get_vehicle(name, **overrides), OPEN_SKY, policy_hz=20, seed=seed)
    return be, np.random.default_rng(seed)


def run(be, cmd, seconds, space=None):
    obs = None
    for _ in range(int(seconds / be.policy_dt)):
        obs = be.step(cmd, space)
    return obs


# ---------------------------------------------------------------- the mixer
@pytest.mark.parametrize("name", DRONES)
def test_mixer_realises_feasible_wrenches_exactly(name):
    spec = get_vehicle(name)
    ap, A = Autopilot(spec, 0.005), allocation_matrix(spec)
    hover = spec.body["mass"] * 9.81
    for tau in ([0, 0, 0], [0.01, -0.01, 0.002], [-0.02, 0.0, 0.0]):
        t = ap.mix(hover, np.array(tau) * spec.body["mass"])
        assert t.min() >= -1e-9 and t.max() <= spec.params["thrust_max"] + 1e-9
        assert A @ t == pytest.approx([hover, *(np.array(tau) * spec.body["mass"])], abs=1e-6)


def test_mixer_gives_up_thrust_before_attitude_when_saturated():
    spec = get_vehicle("quad_250")
    ap, A = Autopilot(spec, 0.005), allocation_matrix(spec)
    tmax = spec.params["thrust_max"]
    tau = np.array([0.35, 0.0, 0.0])                                 # near the limit of what the rotors can do
    t = ap.mix(4 * tmax, tau)                                         # ... while also asking for full thrust
    assert t.min() >= -1e-9 and t.max() <= tmax + 1e-9
    assert (A @ t)[1] == pytest.approx(tau[0], rel=1e-6)              # the torque survived
    assert (A @ t)[0] < 4 * tmax                                      # the thrust did not
    huge = ap.mix(10.0, np.array([5.0, 0, 0]))                        # impossible torque: scaled, never out of range
    assert huge.min() >= -1e-9 and huge.max() <= tmax + 1e-9


# ---------------------------------------------------------------- flight
@pytest.mark.parametrize("name", DRONES)
def test_hover_is_steady_and_level(name):
    be, rng = fly(name)
    be.reset((1.0, -2.0, 1.5, 0.3), {}, {}, rng=rng)
    o = run(be, [0, 0, 0, 0], 10)
    roll, pitch, _ = euler(o.state.quat)
    assert abs(o.state.pos[2] - 1.5) < 0.03 and math.hypot(o.state.pos[0] - 1.0, o.state.pos[1] + 2.0) < 0.05
    assert max(abs(roll), abs(pitch)) < math.radians(1.0) and np.linalg.norm(o.state.vel) < 0.02


@pytest.mark.parametrize("name", DRONES)
def test_velocity_command_tracks_without_exceeding_the_tilt_limit(name):
    v = get_vehicle(name)
    be, rng = fly(name)
    be.reset((0, 0, 1.5, 0), {}, {}, rng=rng)
    target = 0.5 * v.params["max_speed"]
    peak = tilt = 0.0
    for _ in range(int(6 / be.policy_dt)):
        o = be.step([target, 0, 0, 0])
        peak, r_p = max(peak, o.state.vel[0]), euler(o.state.quat)
        tilt = max(tilt, abs(r_p[0]), abs(r_p[1]))
    assert o.state.vel[0] == pytest.approx(target, rel=0.03) and peak < 1.35 * target
    assert tilt < math.radians(v.params["max_tilt_deg"]) + math.radians(3) and abs(o.state.pos[2] - 1.5) < 0.1
    o = run(be, [v.params["max_speed"], 0, 0, 0], 8)                  # full speed: tilt-limited, still level in altitude
    assert o.state.vel[0] > 0.9 * v.params["max_speed"] and abs(o.state.pos[2] - 1.5) < 0.3


@pytest.mark.parametrize("name", DRONES)
def test_climb_and_yaw_rate_track_their_commands(name):
    be, rng = fly(name)
    be.reset((0, 0, 1.0, 0), {}, {}, rng=rng)
    o = run(be, [0, 0, 1.0, 1.0], 3)
    assert o.state.pos[2] == pytest.approx(4.0, abs=0.25) and o.state.omega_body[2] == pytest.approx(1.0, rel=0.03)


@pytest.mark.parametrize("name", DRONES)
def test_position_command_converges_to_position_and_heading(name):
    be, rng = fly(name)
    be.reset((0, 0, 1.0, 0), {}, {}, rng=rng)
    o = run(be, [3, -2, 2.5, 0.8], 14, "position")
    assert np.allclose(o.state.pos, [3, -2, 2.5], atol=0.05) and o.state.yaw == pytest.approx(0.8, abs=0.03)


def test_tilting_forward_accelerates_forward_and_rolling_left_down_accelerates_right():
    """Guards the sign conventions (x forward, y left, z up; positive pitch = nose down; positive roll = left side up)."""
    v = get_vehicle("quad_250")
    hover = v.body["mass"] * 9.81 / (4 * v.params["thrust_max"])
    be, rng = fly("quad_250")
    be.reset((0, 0, 1.5, 0), {}, {}, rng=rng)
    assert run(be, [0, math.radians(10), 0, hover], 1, "attitude_thrust").state.vel[0] > 0.5
    be.reset((0, 0, 1.5, 0), {}, {}, rng=rng)
    assert run(be, [math.radians(10), 0, 0, hover], 1, "attitude_thrust").state.vel[1] < -0.5


def test_motors_space_bypasses_the_autopilot():
    v = get_vehicle("quad_250")
    be, rng = fly("quad_250")
    be.reset((0, 0, 1.5, 0), {}, {}, rng=rng)
    hover = v.body["mass"] * 9.81 / (4 * v.params["thrust_max"])
    o = run(be, [hover] * 4, 0.5, "motors")
    assert abs(o.state.vel[2]) < 0.1
    o = run(be, [1.0] * 4, 1.0, "motors")                             # full throttle: climbs hard
    assert o.state.vel[2] > 5.0


@pytest.mark.parametrize("name", DRONES)
def test_steady_wind_is_rejected(name):
    be, rng = fly(name, seed=3)
    be.reset((0, 0, 1.5, 0), {}, {"wind": 6.0}, rng=rng)
    speeds = []
    for _ in range(int(15 / be.policy_dt)):
        o = be.step([0, 0, 0, 0])
        speeds.append(np.linalg.norm(o.state.vel[:2]))
    assert np.mean(speeds[-60:]) < 0.15 and abs(o.state.pos[2] - 1.5) < 0.1 and math.hypot(*o.state.pos[:2]) < 3.0


def test_a_wrong_mass_is_trimmed_out_by_the_altitude_integrator():
    be, rng = fly("quad_250", seed=1)
    be.reset((0, 0, 1.5, 0), {}, {"mass": 0.2}, rng=rng)             # +-20 % mass the autopilot does not know about
    o = run(be, [0, 0, 0, 0], 12)
    assert abs(o.state.pos[2] - 1.5) < 0.08 and abs(be.plant.m - 0.65) > 1e-3


def test_takeoff_from_the_ground_and_gentle_landing():
    be, rng = fly("quad_250")
    h = get_vehicle("quad_250").body["height"]
    o = be.reset((0, 0, h / 2, 0), {}, {}, rng=rng)
    assert o.state.extra["on_ground"] is False                        # contact is reported after the first step
    o = run(be, [0, 0, 0, 0], 1)
    assert o.state.extra["on_ground"] and o.state.pos[2] == pytest.approx(h / 2, abs=0.01)
    o = run(be, [0, 0, 1.5, 0], 3)
    assert not o.state.extra["on_ground"] and o.state.pos[2] > 3.0
    impact, o = 0.0, None
    for _ in range(int(12 / be.policy_dt)):
        o = be.step([0, 0, -0.5, 0])
        if o.state.extra["on_ground"]:
            break
        impact = o.state.vel[2]
    assert o.state.extra["on_ground"] and abs(impact) < 1.0


def test_resting_on_the_ground_is_ground_contact_not_a_collision():
    be, rng = fly("quad_250")
    h = get_vehicle("quad_250").body["height"]
    be.reset((0, 0, h / 2, 0), {}, {}, rng=rng)
    o = run(be, [0, 0, 0, 0], 1)
    assert o.state.extra["on_ground"] and not o.state.collided


def test_flying_into_a_wall_is_a_collision():
    from simlab.tasks.base import Item
    wall = Item("wall", "box", (0.1, 6.0, 4.0), (6.0, 0.0, 4.0))
    be = MujocoBackend(get_vehicle("quad_250"), World([wall], (40.0, 40.0)), policy_hz=20, seed=0)
    be.reset((0, 0, 2.0, 0), {}, {}, rng=np.random.default_rng(0))
    hit = False
    for _ in range(int(6 / be.policy_dt)):
        o = be.step([4.0, 0, 0, 0])
        hit = hit or o.state.collided
    assert hit and o.state.pos[0] < 6.0 - 0.1


@pytest.mark.parametrize("name", DRONES)
def test_battery_drains_and_an_empty_pack_cuts_the_motors(name):
    spec = get_vehicle(name)
    if spec.sensor("battery") is None:
        pytest.skip("no battery on this preset")
    be, rng = fly(name, seed=0)
    be.reset((0, 0, 1.5, 0), {}, {}, rng=rng)
    o = run(be, [0, 0, 0, 0], 10)
    b = o.sensors["battery"]
    assert 0.9 < b["soc"] < 1.0 and b["power_w"] > 50 * spec.body["mass"] and b["voltage"] > 0
    # endurance: energy / hover power, within a sane band for a real vehicle (roughly 150 W per kg)
    cap_s = (spec.sensor("battery").options["capacity_mah"] / 1000 * b["voltage"] / (1.0 - 0.0) * 3600) / b["power_w"]
    assert 300 < cap_s < 3600
    tiny = spec.to_dict()
    tiny["sensors"] = [dict(s, capacity_mah=10.0) if s["type"] == "battery" else s for s in tiny["sensors"]]
    from simlab import VehicleSpec
    be2 = MujocoBackend(VehicleSpec.from_dict(tiny), OPEN_SKY, policy_hz=20, seed=0)
    be2.reset((0, 0, 3.0, 0), {}, {}, rng=np.random.default_rng(0))
    o = run(be2, [0, 0, 0, 0], 40)
    assert o.state.extra["battery_empty"] and o.state.pos[2] < 0.5      # it ran out and came down


def test_flight_is_deterministic():
    def trace(seed):
        be, rng = fly("hex_450", seed=seed)
        be.reset((0, 0, 1.5, 0), {}, {"wind": 3.0, "mass": 0.1}, rng=rng)
        return np.array([be.step([1, 0.5, 0.2, 0.1]).state.pos for _ in range(80)])
    assert np.array_equal(trace(4), trace(4)) and not np.allclose(trace(4), trace(5))


def test_scene_shows_the_airframe_and_the_rotors_spin():
    be, rng = fly("hex_450")
    be.reset((0, 0, 1.5, 0), {}, {}, rng=rng)
    sc = be.scene()
    assert [n for n in sc["bodies"] if n.startswith("rotor")] == [f"rotor{i}" for i in range(6)]
    assert {"box", "capsule", "cylinder"} <= {g["type"] for g in sc["geoms"]}
    p0 = be.poses().copy()
    run(be, [0, 0, 0, 0], 0.3)
    i = sc["bodies"].index("rotor0")
    assert not np.allclose(be.poses()[i][3:], p0[i][3:])             # the propeller turned


# ---------------------------------------------------------------- tasks
def test_tasks_reject_the_wrong_vehicle_kind():
    with pytest.raises(SpecError, match="flies multirotors"):
        Sim(get_vehicle("mid_4wd"), get_task_spec("waypoints_easy"))
    with pytest.raises(SpecError, match="ground vehicles"):
        Sim(get_vehicle("quad_250"), get_task_spec("goto_open"))
    bad = TaskSpec.from_dict({"name": "x", "task": "waypoints", "params": {"alt_min": 3, "alt_max": 1}, "reward": {"speed": 1}})
    with pytest.raises(SpecError) as e:
        Sim(get_vehicle("quad_250"), bad)
    assert {"params.alt_max", "reward.speed"} <= {i.field for i in e.value.issues}


def test_waypoint_episode_structure_and_reward_terms():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("waypoints_easy"), seed=1)
    pol = make_policy("baseline", sim)
    assert sim.obs_dim == 17
    sim.reset(seed=3)
    pol.reset(sim)
    wps = sim.episode.info["wps"]
    assert len(wps) == 3 and all(1.0 <= w[2] <= 3.0 for w in wps)
    assert all(np.linalg.norm(b - a) >= 2.0 - 1e-9 for a, b in zip([np.array(sim.episode.spawn[:3])] + wps, wps))
    reached, seen = 0, set()
    while True:
        vec, r, term, trunc, info = sim.step_physical(pol.act(sim))
        assert vec.shape == (17,) and np.isfinite(vec).all() and abs(vec).max() <= 3.0
        assert r == pytest.approx(sum(info["terms"].values()))
        seen.add(info["waypoint"])
        if term or trunc:
            break
    assert info["success"] and seen == {0, 1, 2} and info["terms"]["done"] == 10.0


def test_flying_into_the_ground_is_a_crash():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("waypoints_easy"), seed=1)
    sim.reset(seed=2)
    for _ in range(400):
        _, r, term, trunc, info = sim.step_physical([0, 0, -get_vehicle("quad_250").params["max_descent"], 0])
        if term:
            break
    assert term and info["crash"] in ("hard impact", "collision") and not info["success"] and info["terms"]["crash"] < 0
    out = Sim(get_vehicle("quad_250"), get_task_spec("waypoints_easy"), seed=1)
    out.reset(seed=2)
    for _ in range(600):
        _, _, term, _, info = out.step_physical([12, 0, 0, 0])
        if term:
            break
    assert term and info["crash"] == "out of bounds"


def test_hover_hold_needs_the_full_hold_time():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("hover_hold"), seed=1)
    pol = make_policy("baseline", sim)
    sim.reset(seed=4)
    pol.reset(sim)
    first_inside = None
    while True:
        _, _, term, trunc, info = sim.step_physical(pol.act(sim))
        if first_inside is None and info["dist"] < sim.task.radius:
            first_inside = info["t"]
        if term or trunc:
            break
    assert info["success"] and info["t"] - first_inside >= sim.task.p["hold_time"] - 0.1


def test_landing_on_the_pad_succeeds_and_missing_it_fails():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("land_pad"), seed=1)
    pol = make_policy("baseline", sim)
    sim.reset(seed=5)
    pol.reset(sim)
    while True:
        _, _, term, trunc, info = sim.step_physical(pol.act(sim))
        if term or trunc:
            break
    assert info["success"]
    sim.reset(seed=5)                                                  # same scenario, but just drop straight down
    for _ in range(400):
        _, _, term, trunc, info = sim.step_physical([0, 0, -2.0, 0])
        if term:
            break
    assert term and not info["success"] and info["crash"] in ("missed the pad", "hard landing", "hard impact")


@pytest.mark.parametrize("name", DRONES)
@pytest.mark.parametrize("task", ["waypoints_easy", "takeoff_waypoints", "land_pad"])
def test_baseline_succeeds_with_every_drone(name, task):
    sim = Sim(get_vehicle(name), get_task_spec(task), seed=5)
    pol = make_policy("baseline", sim)
    wins = 0
    for ep in range(4):
        sim.reset(seed=2000 + ep)
        pol.reset(sim)
        while True:
            _, _, term, trunc, info = sim.step_physical(pol.act(sim))
            if term or trunc:
                wins += info["success"]
                break
    assert wins >= 3, f"{name} on {task}: {wins}/4"


def test_the_baseline_also_works_through_the_position_command_space():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("waypoints_easy"), seed=1, space="position")
    pol = make_policy("baseline", sim)
    sim.reset(seed=6)
    pol.reset(sim)
    while True:
        _, _, term, trunc, info = sim.step_physical(pol.act(sim))
        if term or trunc:
            break
    assert info["success"]


def test_the_windy_task_randomises_and_is_still_flyable():
    sim = Sim(get_vehicle("quad_250"), get_task_spec("waypoints_windy"), seed=2)
    pol = make_policy("baseline", sim)
    winds, wins = set(), 0
    for ep in range(4):
        sim.reset(seed=10 + ep)
        pol.reset(sim)
        winds.add(round(float(np.linalg.norm(sim.backend.plant.wind)), 3))
        while True:
            _, _, term, trunc, info = sim.step_physical(pol.act(sim))
            if term or trunc:
                wins += info["success"]
                break
    assert len(winds) == 4 and wins >= 3
