"""Hazard hooks: fixed, repeatable stress conditions on the ground, air and water plants (simlab/backends/mujoco/plants.py, plugins/boat.py)."""
import math

import numpy as np
import pytest

pytest.importorskip("mujoco")
from simlab import TaskSpec, get_task_spec, get_vehicle  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.parts import Build, build_to_vehicle  # noqa: E402
from simlab.parts.templates import example_builds  # noqa: E402

OPEN = TaskSpec.from_dict({**get_task_spec("sandbox_water").to_dict(), "params": {"pillars": 0}, "world": {"width": 600, "depth": 600, "theme": "water"}})


def boat(hazards=None, seed=1):
    spec, _ = build_to_vehicle(Build.from_dict(example_builds()["small_boat"]))
    sim = Sim(spec, OPEN, seed=seed, hazards=hazards)
    sim.reset(seed=seed)
    return sim


def rover(hazards=None, seed=0):
    sim = Sim(get_vehicle("mid_4wd"), get_task_spec("goto_empty"), hazards=hazards)
    sim.reset(seed=seed)
    return sim


def run(sim, cmd, steps):
    out = []
    for _ in range(steps):
        sim.step_physical(cmd)
        out.append((float(sim.obs.state.vel_body[0]), float(sim.obs.state.omega_body[2]), sim.obs.state))
    return out


# ------------------------------------------------------------------ ground
def test_no_hazards_is_the_old_behaviour():
    a, b = rover(), rover({})
    ra, rb = run(a, [0.4, 0.0], 40), run(b, [0.4, 0.0], 40)
    assert [round(x[0], 9) for x in ra] == [round(x[0], 9) for x in rb]


def test_resistance_eats_the_motors_margin_and_a_flat_battery_stops_the_rover():
    free = run(rover(), [0.4, 0.0], 60)[-1][0]
    amax = get_vehicle("mid_4wd").params["max_accel"]
    mud = run(rover({"drag_ms2": 0.97 * amax}), [0.4, 0.0], 60)[-1][0]
    assert free > 0.35 and mud < 0.6 * free                              # almost no force left to accelerate with
    assert run(rover({"energy_frac": 0.0}), [0.4, 0.0], 40)[-1][0] < 0.01
    drain = run(rover({"runtime_s": 3.0}), [0.4, 0.0], 80)               # 80 policy steps are far more than 3 s
    assert drain[10][0] > 0.2 and drain[-1][0] < 0.01
    assert drain[-1][2].extra["soc"] == 0.0


def test_a_dead_motor_makes_it_circle_and_lost_commands_slow_it():
    straight = run(rover(), [0.4, 0.0], 60)
    assert abs(straight[-1][1]) < 0.02
    dead = run(rover({"side_gain": (0.0, 1.0)}), [0.4, 0.0], 60)
    assert abs(dead[-1][1]) > 0.2
    base = run(rover(), [0.4, 0.0], 8)[-1][0]
    late = run(rover({"cmd_delay_s": 0.6}), [0.4, 0.0], 8)[-1][0]
    assert late < 0.5 * base


def test_a_slippery_floor_limits_acceleration():
    def t90(h):
        sim = rover(h)
        for k in range(200):
            sim.step_physical([0.4, 0.0])
            if sim.obs.state.vel_body[0] >= 0.9 * 0.4:
                return k
        return 200
    assert t90({"grip_mu": 0.03}) >= 1.8 * t90({})


# ------------------------------------------------------------------ air
def test_wind_and_rotor_loss_hurt_a_quad():
    def fly(h, steps=200):
        sim = Sim(get_vehicle("quad_250"), get_task_spec("hover_hold"), hazards=h)
        sim.reset(seed=0)
        pol = __import__("simlab.control", fromlist=["make_policy"]).make_policy("baseline", sim)
        pol.reset(sim)
        tilts, info = [], {}
        for _ in range(steps):
            _, _, term, trunc, info = sim.step_physical(pol.act(sim))
            q = sim.obs.state.quat
            tilts.append(math.degrees(2 * math.asin(min(1.0, math.hypot(q[1], q[2])))))
            if term or trunc:
                break
        return float(np.mean(tilts[len(tilts) // 2:])), info
    calm, _ = fly({})
    windy, _ = fly({"wind_ms": 12.0})
    assert windy > calm + 5.0                                            # it has to lean into the wind to hold its place
    _, info = fly({"rotor_eff": [0.0, 1.0, 1.0, 1.0]})
    assert info.get("crash"), info                                       # no fault-tolerant control: one dead rotor flips it
    _, info = fly({"energy_frac": 0.0})
    assert info.get("crash") == "battery empty"


# ------------------------------------------------------------------ water
def test_waves_have_the_requested_height():
    sim = boat({"wave_hs": 0.8})
    pl = sim.backend.plant
    vals = []
    for k in range(4000):
        pl._clock = k * 0.1
        vals.append(float(pl.surface(np.array([0.0]), np.array([0.0]))[0]))
    assert np.std(vals) == pytest.approx(0.8 / 4, rel=0.15)             # Hs = 4 sigma


def test_a_calm_boat_is_unchanged_and_waves_move_it():
    calm = run(boat({}), [0.6, 0.0], 150)
    assert abs(calm[-1][2].extra["roll_deg"]) < 0.5
    sea = run(boat({"wave_hs": 0.5}), [0.6, 0.0], 150)
    heave = [r[2].pos[2] for r in sea]
    assert max(heave) - min(heave) > 0.2
    assert max(abs(r[2].extra["pitch_deg"]) for r in sea) > 2.0


def test_dead_thruster_current_and_wind_move_the_boat():
    assert abs(run(boat({"thruster_eff": 0.0}), [1.0, 0.0], 100)[-1][0]) < 0.1
    drift = boat({"current_ms": 1.0})
    run(drift, [0.0, 0.0], 400)
    speed = float(np.linalg.norm(drift.obs.state.vel[:2]))
    assert speed == pytest.approx(1.0, rel=0.3)                          # an unpowered hull goes with the water
    still = boat({})
    windy = boat({"wind_ms": 12.0})
    for s in (still, windy):
        run(s, [0.0, 0.0], 300)
    assert np.linalg.norm(windy.obs.state.pos[:2]) > np.linalg.norm(still.obs.state.pos[:2]) + 2.0


def test_the_scene_is_fixed_by_the_seed_not_by_the_severity():
    a, b = boat({"wave_hs": 0.2}, seed=3), boat({"wave_hs": 0.9}, seed=3)
    assert np.allclose(a.backend.plant.dirs, b.backend.plant.dirs) and np.allclose(a.backend.plant.phase, b.backend.plant.phase)
    c = boat({"wave_hs": 0.2}, seed=4)
    assert not np.allclose(a.backend.plant.dirs, c.backend.plant.dirs)


# ------------------------------------------------------------------ what the picture needs (V0)
def test_the_boat_reports_exactly_the_waves_its_physics_uses():
    sim = boat({"wave_hs": 0.5, "wind_ms": 6.0, "current_ms": 0.8})
    run(sim, [0.3, 0.0], 20)
    e = sim.backend.plant.env()
    w = e["wave"]
    assert w["hs"] == 0.5 and len(w["phase"]) == len(w["share"]) == len(w["per"]) == 5 and w["t"] == pytest.approx(sim.obs.state.t, abs=1e-6)
    assert e["wind"]["ms"] == 6.0 and e["current"]["ms"] == 0.8
    # a viewer that re-evaluates the same five waves from this block gets the physics' surface height
    import math
    g, sigma2 = 9.81, (w["hs"] / 4.0) ** 2
    def height(x, y):
        h = 0.0
        for i in range(5):
            tp = max(w["tp"] * w["per"][i], 0.3)
            om = 2 * math.pi / tp
            k, a = om * om / g, math.sqrt(2 * sigma2 * w["share"][i])
            h += a * math.cos(k * (x * math.cos(w["dir"]) + y * math.sin(w["dir"])) - om * w["t"] + w["phase"][i])
        return h
    plant = sim.backend.plant
    for x, y in ((0.0, 0.0), (3.0, -2.0), (-7.5, 4.1)):
        assert height(x, y) == pytest.approx(float(plant.surface(np.array([x]), np.array([y]))[0]), abs=2e-3)


def test_other_machines_have_no_environment_block_and_lab_conditions_are_clamped():
    assert rover().backend.plant.env() == {}
    from simlab.dashboard.router import _clean_hazards
    h = _clean_hazards({"wave_hs": 9.0, "wind_ms": 0, "grip_mu": 0.8, "current_ms": "fast", "launch": 1, "drag_ms2": 5.0, "cmd_dropout": -1})
    assert h == {"wave_hs": 1.5, "drag_ms2": 5.0}
    assert _clean_hazards(None) == {} and _clean_hazards({"grip_mu": 0.1}) == {"grip_mu": 0.1}
