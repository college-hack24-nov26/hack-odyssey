"""The reality-gap engine on the firmware v2 emulator: every injected fault must be found, on the right side, at about the right size."""
from __future__ import annotations

import math
import threading
import time

import pytest

from sewerbot.hardware.mock_v2 import MockSerialPortV2
from sewerbot.hardware.serial_rover import SerialRover
from simlab.twin import (Dataset, ProtocolAborted, TwinModel, apply_to_hardware, diagnose, explain, fit_twin, measure_gap, ratio_from_ruler,
                         run_protocol)

QUICK = dict(settle_s=0.1, repeats=2, move_ms=1200, spin_ms=700)


def rover(**faults) -> SerialRover:
    r = SerialRover("mock", opener=lambda t, b: MockSerialPortV2(**faults), boot_wait=0.05, us_guard=False)
    time.sleep(0.25)
    return r


@pytest.fixture
def make():
    made = []

    def _make(**faults):
        r = rover(**faults)
        made.append(r)
        return r
    yield _make
    for r in made:
        r.close()


@pytest.fixture(scope="module")
def weak_right(tmp_path_factory):
    r = rover(side_gain=(1.0, 0.88))
    try:
        yield measure_gap(r, environment="tile floor", out_dir=tmp_path_factory.mktemp("gap"), include=("rest", "straight", "spin", "single"), **QUICK), r
    finally:
        r.close()


# ------------------------------------------------------------------ the main fault: one motor weaker
def test_a_weak_right_motor_is_found_on_the_right_side_and_at_the_right_size(weak_right):
    res, _ = weak_right
    f = res.diagnosis.get("imbalance")
    assert f is not None and f.evidence["weak_side"] == "right"
    assert f.evidence["weak_pct"] == pytest.approx(12.0, abs=2.0)
    assert f.confidence >= 0.8
    assert res.diagnosis.trim[0] == 1.0 and res.diagnosis.trim[1] == pytest.approx(1 / 0.88, abs=0.03)
    assert res.diagnosis.side_gain[1] == pytest.approx(0.88, abs=0.02) and res.diagnosis.side_gain[0] == 1.0
    assert "right motor delivers about" in " ".join(explain(res.diagnosis))


def test_the_twin_reproduces_what_the_rover_did_and_the_ideal_simulation_does_not(weak_right):
    res, _ = weak_right
    row = next(r for r in res.table if r["what"].startswith("straight"))
    assert row["ideal_simulation"] == 0.0 and row["measured"] < -5
    assert row["after_fit"] == pytest.approx(row["measured"], abs=0.6)
    spin = next(r for r in res.table if r["what"] == "spin left: turn rate")
    assert spin["after_fit"] == pytest.approx(spin["measured"], rel=0.03)


def test_evidence_is_saved(weak_right):
    res, _ = weak_right
    import json
    from pathlib import Path
    folder = Path(res.folder)
    saved = json.loads((folder / "gap.json").read_text())
    assert saved["environment"] == "tile floor" and saved["diagnosis"]["findings"] and (folder / "gap.txt").read_text().startswith("FIX:")


def test_the_trim_it_recommends_cures_the_pull(weak_right):
    res, r = weak_right
    r.ask(f"CFG trimL={res.diagnosis.trim[0]} trimR={res.diagnosis.trim[1]}", "cfg")
    after = run_protocol(r, include=("rest", "straight", "spin"), **QUICK)
    d = diagnose(after)
    assert abs(d.estimates["straight_rate_dps"]) < 1.5
    assert d.get("imbalance") is None and d.get("straight").level == "ok"


def test_an_active_trim_is_taken_into_account(make):
    r = make(side_gain=(1.0, 0.88))
    r.ask("CFG trimR=1.05", "cfg")                                               # a half-corrected rover: the new advice builds on it
    d = diagnose(run_protocol(r, include=("rest", "straight", "spin", "single"), **QUICK))
    assert d.trim[1] == pytest.approx(1 / 0.88, abs=0.03)                        # the total trim, not another 1.136 on top of 1.05
    assert d.side_gain[1] == pytest.approx(0.88, abs=0.02)


def test_a_weak_left_motor_pulls_left_and_gets_the_boost(make):
    d = diagnose(run_protocol(make(side_gain=(0.92, 1.0)), include=("rest", "straight", "spin", "single"), **QUICK))
    f = d.get("imbalance")
    assert f.evidence["weak_side"] == "left" and d.trim[0] == pytest.approx(1 / 0.92, abs=0.03) and d.trim[1] == 1.0


def test_a_balanced_rover_needs_no_trim(make):
    d = diagnose(run_protocol(make(), include=("rest", "straight", "spin", "single"), **QUICK))
    assert d.get("imbalance") is None and d.get("straight").level == "ok" and d.trim == (1.0, 1.0)


def test_an_imbalance_beyond_the_trim_range_says_check_the_motor(make):
    d = diagnose(run_protocol(make(side_gain=(1.0, 0.6)), include=("rest", "straight", "spin", "single"), **QUICK))
    f = d.get("imbalance")
    assert f.level == "fix" and "check the motor" in f.text and d.trim[1] == 1.3


# ------------------------------------------------------------------ other faults
def test_a_backwards_wheel_is_named(make):
    d = diagnose(run_protocol(make(wheel_sign=(1, -1)), include=("single",), **QUICK))
    f = d.get("inverted_right")
    assert f is not None and f.level == "fix" and f.action == {"cfg": {"invR": -1}} and d.get("inverted_left") is None


def test_a_dead_motor_is_named(make):
    d = diagnose(run_protocol(make(side_gain=(0.0, 1.0)), include=("single",), **QUICK))
    assert d.get("dead_left").level == "fail" and d.get("dead_right") is None


def test_a_humming_motor_is_found_by_its_start_duty(make):
    d = diagnose(run_protocol(make(motor_min=(0, 130)), include=("thresh",), settle_s=0.05))
    f = d.get("hum_right")
    assert f is not None and f.evidence["start_duty"] == 130.0 and d.start_duty[1] == 130.0 and d.get("hum_left") is None
    assert f.action["cfg"]["minduty"] >= 130


def test_a_motor_that_never_starts_is_a_fix(make):
    d = diagnose(run_protocol(make(motor_min=(250, 0)), include=("thresh",), settle_s=0.05))
    assert d.get("hum_left").level == "fix"


def test_power_resets_during_the_test_are_reported(make):
    r = make(sag_prob=1.0)
    ds = run_protocol(r, include=("straight",), repeats=1, move_ms=500, settle_s=0.05)
    d = diagnose(ds)
    assert d.get("power_sag") is not None and d.get("power_sag").evidence["resets"] >= 1


# ------------------------------------------------------------------ safety of the protocol itself
def test_the_protocol_refuses_without_a_gyro(make):
    with pytest.raises(ProtocolAborted, match="gyro is not connected"):
        run_protocol(make(mpu_present=False), include=("straight",), **QUICK)


def test_a_guard_that_says_no_means_no_motion(make):
    r = make()
    with pytest.raises(ProtocolAborted, match="not armed"):
        run_protocol(r, guard=lambda: "not armed: complete the checklist first", include=("straight",), **QUICK)
    assert r._ser.odo_m == 0.0 and r._ser.mode == "idle"


def test_stop_during_the_protocol_stops_the_rover(make):
    r = make()
    abort = threading.Event()
    threading.Timer(1.0, abort.set).start()
    with pytest.raises(ProtocolAborted, match="stopped by you"):
        run_protocol(r, abort=abort, include=("rest", "straight"), repeats=5, move_ms=1500, settle_s=0.3)
    time.sleep(0.2)
    assert r._ser.mode == "idle"


def test_the_protocol_puts_the_minimum_duty_back(make):
    r = make()
    run_protocol(r, include=("thresh",), settle_s=0.05)
    assert r.ask("CFG", "cfg")["minduty"] == 90.0


# ------------------------------------------------------------------ the camera-free fallback, the model, the app profile
def test_the_ruler_method_gives_the_same_kind_of_answer():
    d = diagnose(Dataset(), ruler={"offset_cm": 30.0, "distance_cm": 100.0})          # it ended 30 cm to the left after 1 m
    f = d.get("imbalance")
    assert d.usable and f.evidence["weak_side"] == "left" and d.trim[0] > 1.0 and f.confidence == 0.5
    assert ratio_from_ruler(10, 100) == pytest.approx(0.012)
    assert diagnose(Dataset()).usable is False and diagnose(Dataset()).get("not_enough_data")


def test_the_model_matches_the_simulators_ground_plant():
    """Steady-state turn rate of the real plant for two weak-left-motor strengths has the same sign and ratio as the twin's law."""
    pytest.importorskip("mujoco")
    from simlab import get_task_spec, get_vehicle
    from simlab.envs import Sim

    def plant_rate(g):
        sim = Sim(get_vehicle("mid_4wd"), get_task_spec("goto_empty"), hazards={"side_gain": (g, 1.0)})
        sim.reset(seed=0)
        for _ in range(60):
            sim.step_physical([0.4, 0.0])
        return float(sim.obs.state.omega_body[2])

    a, b = plant_rate(0.88), plant_rate(0.76)
    ma = TwinModel(side_gain=(0.88, 1.0)).turn_rate_dps(180, 180)
    mb = TwinModel(side_gain=(0.76, 1.0)).turn_rate_dps(180, 180)
    assert a > 0 and ma > 0                                                      # a weak LEFT motor turns the rover left in both
    assert a / b == pytest.approx(ma / mb, rel=0.08)


def test_model_basics():
    m = TwinModel(side_gain=(0.9, 1.0))
    assert m.turn_rate_dps(180, 180) > 0 and m.drift_cm(180, 1.0) > 0
    assert math.isclose(TwinModel().turn_rate_dps(180, 180), 0.0, abs_tol=1e-9)
    assert TwinModel(start_duty=(100, 0)).wheel_speeds(80, 80)[0] == 0.0


def test_apply_to_hardware_stores_the_measured_spin_rate():
    calls = []

    class Hw:
        def calibrate_yaw(self, w, secs, deg):
            calls.append((w, secs, deg))
            return {"yaw_gain_new": 0.5}
    ds = Dataset(runs={"spin_l": [{"yaw": 180.0, "secs": 1.0}], "spin_r": [{"yaw": -170.0, "secs": 1.0}]})
    assert apply_to_hardware(Hw(), ds, diagnose(ds)) == {"yaw_gain_new": 0.5} and calls[0][2] == pytest.approx(175.0)
    assert apply_to_hardware(Hw(), Dataset(), diagnose(Dataset())) == {}
