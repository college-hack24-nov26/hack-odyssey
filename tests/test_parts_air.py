"""Air and water builds: the thrust-curve model against a published table, rules, and the derived vehicles in the simulator."""
import copy
import math

import pytest

from simlab.parts import Build, build_to_vehicle, evaluate, get_part, save_part
from simlab.parts.derive.air import full_throttle, motor_current
from simlab.parts.schema import Part
from simlab.parts.templates import example_builds

QUAD = example_builds()["quad_5in_racer"]
BOAT = example_builds()["small_boat"]


def variant(base, **kw):
    b = copy.deepcopy(base)
    for k, v in kw.items():
        b[k] = {**b[k], **v} if isinstance(v, dict) and isinstance(b.get(k), dict) else v
    return b


def ids(res, severity=None):
    return {f["id"] for f in res["findings"] if severity is None or f["severity"] == severity}


def facts(res):
    return {f["key"]: f["value"] for f in res["facts"]}


@pytest.fixture()
def user_dirs(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inventory.yaml"), ("SIMLAB_BUILDS", "builds"), ("SIMLAB_VEHICLES", "vehicles")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path


# ---------------------------------------------------------------- the thrust and current model against measurements
def test_the_current_curve_matches_the_published_thrust_table():
    """BrotherHobby R5 2306 2450KV, HQ 5x4x3, 4S, Mini Quad Test Bench: (thrust g, current A) at 25/50/75/100 % throttle."""
    p = get_part("motorprop_2306_2400kv_5x4_4s").params
    table = [(144, 2.7), (438, 8.0), (821, 17.2), (1258, 31.1)]
    for thrust_g, amps in table:
        frac = thrust_g / 1258.0
        predicted = motor_current(p["current_max_a_at_ref"], p["idle_current_a"], frac)
        assert predicted == pytest.approx(amps, rel=0.12), (thrust_g, predicted, amps)
    assert motor_current(31.1, 1.3, 1.0) == pytest.approx(31.1) and motor_current(31.1, 1.3, 0.0) == pytest.approx(1.3)


def test_thrust_and_current_scale_with_voltage_squared_and_the_battery_sags():
    stiff = full_throttle(15.6, 0.0, 4, 12.3, 31.0, 15.6, 0.5)
    assert stiff[0] == pytest.approx(15.6) and stiff[1] == pytest.approx(12.3) and stiff[2] == pytest.approx(31.0)
    half = full_throttle(7.8, 0.0, 4, 12.3, 31.0, 15.6, 0.5)
    assert half[1] == pytest.approx(12.3 / 4) and half[2] == pytest.approx(31.0 / 4)
    sagging = full_throttle(15.6, 0.05, 4, 12.3, 31.0, 15.6, 0.5)
    assert sagging[0] < 15.6 - 3 and sagging[1] < 12.3                              # four motors at 31 A through 50 mohm lose several volts


# ---------------------------------------------------------------- derived quantities
def test_the_quad_numbers_hang_together():
    r = evaluate(QUAD)
    assert r["ok"] and r["vehicle"]["kind"] == "multirotor"
    f = facts(r)
    mass = r["vehicle"]["body"]["mass"]
    assert 0.35 < mass < 0.7 and 2.0 < f["twr"] < 9.0
    assert f["twr"] == pytest.approx(4 * r["vehicle"]["params"]["thrust_max"] / (mass * 9.81), rel=1e-3)
    assert 4 < f["runtime"] < 15 and 3 < f["hover_current"] < 20 and f["hover_thrust"] < 30
    assert r["vehicle"]["params"]["torque_coef"] == pytest.approx(0.125 * 5 * 0.0254, rel=0.01)
    assert {s["type"] for s in r["vehicle"]["sensors"]} >= {"imu", "camera", "battery"}
    assert next(s for s in r["vehicle"]["sensors"] if s["type"] == "battery")["cells"] == 4


def test_heavier_means_slower_to_climb_and_shorter_flights_and_a_bigger_battery_lasts_longer():
    base = facts(evaluate(QUAD))
    heavy = facts(evaluate(variant(QUAD, chassis={"extra_mass_kg": 0.25})))
    assert heavy["twr"] < base["twr"] and heavy["runtime"] < base["runtime"] and heavy["hover_thrust"] > base["hover_thrust"]
    big = facts(evaluate(variant(QUAD, power={"parallel": 2})))
    assert big["runtime"] > 1.4 * base["runtime"]
    six = facts(evaluate(variant(QUAD, power={"series": 6}, drive={"esc": "esc_30a"})))
    assert six["thrust_rotor"] > 1.5 * base["thrust_rotor"]                         # 6S: voltage squared


def test_hex_and_micro_builds():
    hexa = evaluate(variant(QUAD, chassis={"frame": "frame_f550_hex"}, drive={"propulsor": "motorprop_2212_920kv_1045_3s", "esc": "esc_30a"},
                            power={"battery": "lipo_cell_2200_30c", "series": 3, "parallel": 2}))
    assert hexa["vehicle"]["params"]["layout"] == "hex" and facts(hexa)["twr"] > 1.2                              # ESC-limited 3S test data: honest, and enough to fly
    micro = evaluate(variant(QUAD, chassis={"frame": "frame_micro_90mm"}, drive={"propulsor": "motorprop_0802_23000kv_40mm_1s", "esc": "esc_5a_micro"},
                             power={"battery": "lipo_cell_1000_25c", "series": 1, "regulator": ""}, controller="rpi_pico_w", sensors=[]))
    assert micro["vehicle"] is not None and facts(micro)["twr"] > 1.3


# ---------------------------------------------------------------- the rules
def test_air_rules_fire_on_the_right_mistakes():
    assert "A01" in ids(evaluate(variant(QUAD, chassis={"extra_mass_kg": 2.5})), "error")                        # cannot fly
    assert "A04" in ids(evaluate(variant(QUAD, drive={"esc": "esc_5a_micro"})), "error")                         # ESC far too small
    big_props = evaluate(variant(QUAD, drive={"propulsor": "motorprop_2212_920kv_1045_3s"}))
    assert "A06" in ids(big_props, "error")                                                                       # 10 inch propellers on a 5 inch frame
    assert "A05" in ids(evaluate(variant(QUAD, power={"battery": "lipo_cell_1000_25c"})), "error")               # a 25C pack cannot feed ~90 A
    short = evaluate(variant(QUAD, power={"battery": "lipo_cell_1000_25c", "series": 4}, chassis={"extra_mass_kg": 0.3}))
    assert "A05" in ids(short)
    assert "E05" in ids(evaluate(variant(QUAD, power={"regulator": ""})), "error")                                # 16.8 V straight into a 12 V board
    assert evaluate(QUAD)["ok"] and not ids(evaluate(QUAD), "error")


def test_a_controller_without_pwm_cannot_drive_the_escs(user_dirs):
    mcu = get_part("esp32_devkit")
    save_part(Part("no_pwm_board", "mcu", "", dict(mcu.params, interfaces=["i2c", "uart"]), 0.01, "test", "assumed"))
    assert "E08" in ids(evaluate(variant(QUAD, controller="no_pwm_board")), "error")


def test_water_rules_and_the_boat_that_sinks():
    r = evaluate(BOAT)
    assert r["ok"] and r["vehicle"]["kind"] == "boat"
    f = facts(r)
    assert 0.0 < f["draft"] < 0.3 and f["freeboard"] > 0.1 and f["top_speed"] > 1 and 0.2 < f["bollard"] < 2
    assert f["runtime"] > 20
    sunk = evaluate(variant(BOAT, chassis={"ballast_kg": 500.0}))
    assert "W01" in ids(sunk, "error") and not sunk["ok"]
    weak = evaluate(variant(BOAT, chassis={"ballast_kg": 150.0}))
    assert ids(weak) & {"W02", "W03"}
    assert "W04" in ids(evaluate(variant(BOAT, power={"series": 4, "parallel": 1})), "warn")                     # 48 V on a 12 V thruster
    assert "E04" in ids(evaluate(variant(BOAT, power={"parallel": 1})), "error")                                 # one 7 Ah lead-acid battery cannot feed 26 A


def test_platform_specific_part_names_are_checked():
    b, issues = Build.parse(variant(QUAD, chassis={"frame": "wheel_65mm_tt"}))
    assert b is None and "frame" in " ".join(i.msg for i in issues) and "frame_5in_x" in " ".join(i.msg for i in issues)
    b, issues = Build.parse(variant(BOAT, drive={"thruster": "tt_motor_6v"}))
    assert b is None
    assert Build.parse({**QUAD, "drive": {"motor": "tt_motor_6v"}})[0] is None                                    # ground keys on an air build


def test_air_and_water_calibration_is_refused_not_faked():
    from simlab.parts.calibrate import fit
    with pytest.raises(ValueError, match="ground builds only"):
        fit(QUAD, {"mass_g": 500})


# ---------------------------------------------------------------- the gate: the derived vehicles work in the simulator
def test_the_derived_quad_hovers_and_flies_a_task():
    pytest.importorskip("mujoco")
    from simlab import get_task_spec
    from simlab.control import make_policy
    from simlab.envs import Sim
    spec, _ = build_to_vehicle(Build.from_dict(QUAD))
    sim = Sim(spec, get_task_spec("hover_hold"))
    sim.reset(seed=1)
    pol = make_policy("baseline", sim)
    pol.reset(sim)
    out = None
    for _ in range(int(sim.task.spec.episode["max_time"] * sim.task.spec.episode["policy_hz"]) + 5):
        out = sim.step_physical(pol.act(sim))
        if out[2] or out[3]:
            break
    assert out[4]["success"], out[4]
    sim.close()


def test_the_derived_hex_flies_waypoints():
    pytest.importorskip("mujoco")
    from simlab import get_task_spec
    from simlab.control import make_policy
    from simlab.envs import Sim
    hexa = variant(QUAD, chassis={"frame": "frame_f550_hex"}, drive={"propulsor": "motorprop_2212_920kv_1045_3s", "esc": "esc_30a"},
                   power={"battery": "lipo_cell_2200_30c", "series": 3, "parallel": 2, "regulator": "lm2596_buck_module"})
    spec, _ = build_to_vehicle(Build.from_dict(hexa))
    sim = Sim(spec, get_task_spec("waypoints_easy"))
    wins = 0
    for seed in range(2):
        sim.reset(seed=seed)
        pol = make_policy("baseline", sim)
        pol.reset(sim)
        for _ in range(int(sim.task.max_time * sim.task.spec.episode["policy_hz"]) + 5):
            out = sim.step_physical(pol.act(sim))
            if out[2] or out[3]:
                break
        wins += bool(out[4]["success"])
    assert wins >= 1
    sim.close()


def test_the_derived_boat_floats_and_moves():
    pytest.importorskip("mujoco")
    from simlab import TaskSpec, get_task_spec
    from simlab.envs import Sim
    spec, dv = build_to_vehicle(Build.from_dict(BOAT))
    open_water = TaskSpec.from_dict({**get_task_spec("sandbox_water").to_dict(), "params": {"pillars": 0}, "world": {"width": 400, "depth": 400, "theme": "water"}})
    sim = Sim(spec, open_water, seed=1)
    sim.reset(seed=1)
    z0 = float(sim.obs.state.pos[2])
    for _ in range(300):
        sim.step_physical([1.0, 0.0])
    st = sim.obs.state
    assert abs(float(st.pos[2]) - z0) < 0.1 and abs(float(st.pos[1])) < 0.2              # riding on the water, straight
    assert float(st.vel_body[0]) == pytest.approx(dv.numbers["v_top"], rel=0.25)         # the simulated top speed is near the boat model's own estimate
    sim.close()


# ---------------------------------------------------------------- the API and the picture
def test_api_platforms_preview_and_saving():
    pytest.importorskip("mujoco")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from simlab.dashboard.build_router import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        lib = c.get("/api/build/library").json()
        assert lib["platforms"] == ["ground", "air", "water"] and {"quad_5in_racer", "small_boat"} <= set(lib["examples"])
        assert {"frame", "propulsor_air", "esc", "hull", "thruster_water"} <= set(lib["kinds"])
        for name in ("quad_5in_racer", "small_boat"):
            pv = c.post("/api/build/preview", json={"build": lib["examples"][name]}).json()
            assert {"battery", "controller"} <= {g["name"] for g in pv["scene"]["geoms"]}
        r = c.post("/api/build/evaluate", json={"build": QUAD, "task": "waypoints_easy"}).json()
        assert r["ok"] and r["task_ok"] and r["fit"]["task"] == "waypoints_easy"
        assert c.post("/api/build/calibrate", json={"build": QUAD, "measurements": {"mass_g": 500}}).status_code == 422


def test_a_broken_air_build_gets_a_verified_part_suggestion():
    broken = variant(QUAD, drive={"esc": "esc_5a_micro"})
    r = evaluate(broken)
    a04 = next(f for f in r["findings"] if f["id"] == "A04" and "cannot carry" in f["title"])
    names = [c["part"] for c in a04["fix"]["candidates"]]
    assert set(names) == {"esc_30a", "esc_40a", "esc_60a"}                           # every ESC that carries ~24 A at 16.8 V; not the 18 A or the 5 A one
    from simlab.parts.recommend import apply_patch
    fixed = evaluate(apply_patch(broken, a04["fix"]["candidates"][0]["patch"]), suggest=False)
    assert fixed["ok"] and "A04" not in ids(fixed, "error")
