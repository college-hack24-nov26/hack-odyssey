"""Calibration: measurements on the real machine replace the build's assumptions, and the simulator then behaves like them."""
import copy
import math

import pytest

from simlab.parts import Build, evaluate
from simlab.parts.calibrate import fit
from simlab.parts.templates import example_builds

ROVER = example_builds()["esp32_pipe_rover"]
ALL = {"mass_g": 520, "straight_cm": 90, "straight_s": 3, "spin_deg": 540, "spin_s": 3, "t90_s": 1.2, "runtime_min": 95}


def facts(res):
    return {f["key"]: f["value"] for f in res["facts"]}


def test_every_measurement_becomes_the_prediction():
    r = fit(ROVER, ALL)
    assert [t["what"] for t in r["table"]] == ["mass", "top speed", "turn rate", "acceleration", "runtime"]
    f = facts(r["result"])
    assert f["mass"] == pytest.approx(0.52)
    assert f["top_speed"] == pytest.approx(0.30, rel=1e-6)                     # 90 cm in 3 s
    assert f["turn_rate"] == pytest.approx(math.radians(540) / 3, rel=1e-6)
    assert f["accel"] == pytest.approx(0.9 * 0.30 / 1.2, rel=1e-6)
    assert f["runtime"] == pytest.approx(95.0, rel=1e-6)
    for row in r["table"]:
        assert row["after"] == pytest.approx(row["measured"], rel=1e-6)
        assert row["error_pct"] == pytest.approx(100 * (row["predicted"] / row["measured"] - 1), abs=0.06)
    cal = r["build"]["calibration"]
    assert cal["measured"]["straight_cm"] == 90 and cal["date"] and set(cal) >= {"mass_kg", "speed_scale", "yaw_gain", "accel_scale", "battery_derate"}
    assert r["result"]["ok"]
    assert any("your measurement" in a for a in r["result"]["assumptions"])
    assert any("30 %" in n for n in r["notes"])                                  # the default prediction was far off, and it says where to look


def test_a_prediction_close_to_reality_stays_close_and_raises_no_alarm():
    base = facts(evaluate(ROVER))
    r = fit(ROVER, {"straight_cm": base["top_speed"] * 300 * 1.05, "straight_s": 3, "runtime_min": base["runtime"] * 0.9})
    assert [t["what"] for t in r["table"]] == ["top speed", "runtime"]
    assert abs(r["table"][0]["error_pct"]) < 6 and abs(r["table"][1]["error_pct"]) < 12 and not r["notes"]


def test_partial_unusable_and_absurd_input():
    assert fit(ROVER, {"mass_g": 400})["build"]["calibration"].keys() == {"mass_kg", "measured", "date"}
    assert "both a distance and a time" in " ".join(fit(ROVER, {"straight_cm": 90})["notes"])
    assert "both an angle and a time" in " ".join(fit(ROVER, {"spin_deg": 360})["notes"])
    for junk in ({}, {"mass_g": 0}, {"mass_g": -5}, {"mass_g": float("nan")}, {"mass_g": "heavy"}, {"mass_g": None}, {"bogus": 3}):
        r = fit(ROVER, junk)
        assert r["table"] == [] and "nothing to fit" in " ".join(r["notes"]) and r["build"]["calibration"] == {}
    wild = fit(ROVER, {"straight_cm": 1_000_000, "straight_s": 3})
    assert "clamped" in " ".join(wild["notes"]) and wild["result"]["ok"]
    with pytest.raises(ValueError):
        fit({"name": "x"}, ALL)


def test_clear_removes_the_calibration_and_fitting_twice_gives_the_same_answer():
    once = fit(ROVER, ALL)
    twice = fit(once["build"], ALL)
    strip = lambda c: {k: v for k, v in c.items() if k != "date"}               # noqa: E731
    assert strip(once["build"]["calibration"]) == strip(twice["build"]["calibration"])
    cleared = fit(once["build"], clear=True)
    assert cleared["build"]["calibration"] == {} and facts(cleared["result"]) == facts(evaluate(ROVER))


def test_the_calibrated_build_still_parses_saves_and_keeps_its_numbers():
    b = Build.from_dict(fit(ROVER, ALL)["build"])
    assert b.calibration["mass_kg"] == 0.52 and b.calibration["measured"]["runtime_min"] == 95
    again = Build.from_dict(b.to_dict())
    assert again.calibration == b.calibration


def test_a_measured_top_speed_beats_the_firmware_limit_only_up_to_the_limit():
    limited = copy.deepcopy(ROVER)
    limited["drive"]["speed_limit_mps"] = 0.2
    r = fit(limited, {"straight_cm": 90, "straight_s": 3})                       # the motors can do 0.30 m/s but the firmware stops at 0.2
    assert facts(r["result"])["top_speed"] == pytest.approx(0.2)


# ---------------------------------------------------------------- the simulator behaves like the measurements
def test_the_calibrated_vehicle_moves_and_turns_like_the_real_one_in_mujoco():
    pytest.importorskip("mujoco")
    from simlab import VehicleSpec, get_task_spec
    from simlab.envs import Sim
    r = fit(ROVER, ALL)
    spec = VehicleSpec.from_dict(r["result"]["vehicle"])
    sim = Sim(spec, get_task_spec("goto_empty"))
    sim.reset(seed=2)
    t90, peak, t = None, 0.0, 0.0
    for _ in range(60):                                                          # straight at full command: speed after the ramp, and the time to 90 %
        sim.step_physical([spec.params["max_speed"], 0.0])
        t += sim.task.policy_dt
        v = float(sim.obs.state.vel_body[0])
        peak = max(peak, v)
        if t90 is None and v >= 0.9 * spec.params["max_speed"]:
            t90 = t
    assert peak == pytest.approx(0.30, rel=0.04)
    assert t90 == pytest.approx(1.2, rel=0.35)                                   # a slew-limited ramp plus the motor lag: within about a third
    sim.reset(seed=2)
    yaw0 = float(sim.obs.state.yaw)
    turned, prev = 0.0, yaw0
    for _ in range(int(3.0 / sim.task.policy_dt)):                               # spin at full command for 3 s
        sim.step_physical([0.0, spec.params["max_yaw_rate"]])
        y = float(sim.obs.state.yaw)
        turned += (y - prev + math.pi) % (2 * math.pi) - math.pi
        prev = y
    assert math.degrees(turned) == pytest.approx(540, rel=0.12)
    sim.close()


# ---------------------------------------------------------------- the API
def test_the_calibrate_endpoint():
    pytest.importorskip("mujoco")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from simlab.dashboard.build_router import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        r = c.post("/api/build/calibrate", json={"build": ROVER, "measurements": ALL}).json()
        assert r["build"]["calibration"]["mass_kg"] == 0.52 and len(r["table"]) == 5 and r["result"]["ok"]
        cleared = c.post("/api/build/calibrate", json={"build": r["build"], "clear": True}).json()
        assert cleared["build"]["calibration"] == {}
        assert c.post("/api/build/calibrate", json={"build": {"name": "x"}, "measurements": ALL}).status_code == 422
        assert c.post("/api/build/calibrate", json={"build": ROVER, "measurements": {}}).json()["table"] == []
