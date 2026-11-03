"""Parts and builds: the library, the motor/battery model, the rules, and the link to the simulator."""
import copy
import math

import pytest
import yaml

from simlab.parts import Build, BuildError, Part, all_parts, evaluate, get_part, parts_by_kind, save_part
from simlab.parts.derive.ground import D_TAU, MotorModel, Supply, top_speed
from simlab.parts.library import broken_parts, load_inventory, save_inventory
from simlab.parts.schema import CONFIDENCE, PART_KINDS, check_part, motor_efficiency_at_max_power

ROVER = {
    "name": "esp32_pipe_rover", "platform": "ground",
    "chassis": {"length": 0.16, "width": 0.12, "height": 0.055, "frame_mass_kg": 0.12, "drive": "diff2wd", "caster_count": 1},
    "drive": {"motor": "tt_motor_6v", "motor_count": 2, "wheel": "wheel_65mm_tt", "driver": "drv8833_module_pwp"},
    "power": {"battery": "aa_nimh_2000", "series": 4, "parallel": 1, "regulator": "mt3608_boost_module"},
    "controller": "esp32_devkit",
    "sensors": [{"part": "esp32cam_stream_board", "name": "front", "mount": {"forward": 0.07, "height": 0.075, "tilt_deg": -10}},
                {"part": "hc_sr04", "name": "sonar", "mount": {"forward": 0.08, "height": 0.03}}],
    "lamp": {"part": "led_headlamp_small", "count": 1},
    "extras": [{"name": "wiring and board", "mass_kg": 0.02, "current_a": 0.0}],
}


def rover(**changes):
    """ROVER with nested changes: rover(power={"regulator": ""}) replaces just that key."""
    b = copy.deepcopy(ROVER)
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(b.get(k), dict):
            b[k].update(v)
        else:
            b[k] = v
    return b


def ids(result, severity=None):
    return {f["id"] for f in result["findings"] if severity is None or f["severity"] == severity}


@pytest.fixture()
def user_dirs(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inventory.yaml"), ("SIMLAB_BUILDS", "builds"), ("SIMLAB_VEHICLES", "vehicles")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path


# ---------------------------------------------------------------- library
def test_every_shipped_part_loads_and_says_where_its_numbers_came_from():
    assert not broken_parts()
    parts = all_parts()
    assert len(parts) >= 30
    for name, p in parts.items():
        assert p.confidence in CONFIDENCE, name
        assert p.source.strip(), f"{name} has no source"
        assert p.origin == "library"
        assert p.mass_kg >= 0
        assert not [i for i in check_part(p) if i.level == "error"], name
    kinds = {p.kind for p in parts.values()}
    assert {"motor_dc", "wheel", "driver_dc", "battery", "regulator", "mcu", "sensor_camera", "sensor_range", "lamp"} <= kinds
    assert set(parts_by_kind()) >= set(PART_KINDS)


def test_a_part_file_with_a_unit_mistake_or_a_typo_is_rejected_with_a_reason():
    base = get_part("tt_motor_6v").to_dict()
    bad_unit = copy.deepcopy(base)
    bad_unit["params"]["stall_torque_nm"] = 8.0                 # kg*cm typed as N*m, ten times too big
    part, issues = Part.parse(bad_unit)
    assert part is None and any("efficiency" in i.msg for i in issues)
    typo = copy.deepcopy(base)
    typo["params"]["stall_torq_nm"] = 0.08
    assert Part.parse(typo)[0] is None
    bad_kind = copy.deepcopy(base)
    bad_kind["kind"] = "motor"
    part, issues = Part.parse(bad_kind)
    assert part is None and "known" in issues[0].msg
    bad_batt = {"name": "b", "kind": "battery", "params": {"cell_v_nominal": 1.5, "cell_v_full": 1.4, "cell_v_cutoff": 1.0}}
    assert Part.parse(bad_batt)[0] is None


def test_user_parts_override_library_parts_and_survive_a_round_trip(user_dirs):
    mine = Part("tt_motor_6v", "motor_dc", "my measured TT motor", dict(get_part("tt_motor_6v").params, no_load_rpm=250.0), 0.031, "measured by me", "measured")
    path = save_part(mine)
    assert path.is_file() and str(user_dirs) in str(path)
    got = get_part("tt_motor_6v")
    assert got.origin == "user" and got.params["no_load_rpm"] == 250.0 and got.confidence == "measured"
    with pytest.raises(ValueError):
        save_part(Part("bad name!", "motor_dc", "", {}, 0.0))
    save_inventory({"tt_motor_6v": 2, "hc_sr04": 0, "esp32_devkit": 1})
    assert load_inventory() == {"tt_motor_6v": 2, "esp32_devkit": 1}


# ---------------------------------------------------------------- the motor model against published numbers
def ideal(m: MotorModel, n=1):
    return Supply(m.v_r, 0.0, 0.0, 0.0, 0.0, n)


@pytest.mark.parametrize("name", ["tt_motor_6v", "pololu_hp_75_1_6v", "pololu_120_1_hp_4v5"])
def test_motor_model_identities(name):
    m = MotorModel.from_part(get_part(name))
    s = ideal(m)
    assert s.speed_at(m, 0.0) == pytest.approx(m.w_nl)                          # unloaded at the rated voltage = the rated speed
    assert s.speed_at(m, m.tau_s) == pytest.approx(0.0, abs=1e-9)               # stalled
    assert s.torque_at(m, 0.0) == pytest.approx(m.tau_s)
    assert s.torque_at(m, m.w_nl) == pytest.approx(0.0, abs=1e-9)
    assert s.stall_current(m) == pytest.approx(m.i_s)
    assert m.current_at(m.tau_s) == pytest.approx(m.i_s)
    half = Supply(m.v_r / 2, 0.0, 0.0, 0.0, 0.0, 1)                             # torque and speed both scale with voltage
    assert half.torque_at(m, 0.0) == pytest.approx(m.tau_s / 2)
    assert half.speed_at(m, 0.0) == pytest.approx(m.w_nl / 2)
    assert m.v_required(s.speed_at(m, 0.4 * m.tau_s), 0.4 * m.tau_s) == pytest.approx(m.v_r)


@pytest.mark.parametrize("name,published_w", [("pololu_hp_50_1_6v", 1.3), ("pololu_hp_75_1_6v", 1.4), ("pololu_hp_100_1_6v", 1.3)])
def test_maximum_output_power_matches_the_manufacturers_published_figure(name, published_w):
    m = MotorModel.from_part(get_part(name))
    assert m.tau_s * m.w_nl / 4.0 == pytest.approx(published_w, rel=0.08)       # P_max = stall torque * no-load speed / 4 for a linear motor


def test_the_motor_line_reproduces_pololus_maximum_efficiency_point():
    """Pololu publishes the 50:1 HP motor at 6 V running at 490 rpm, 0.15 kg*cm, 0.32 A."""
    part = get_part("pololu_hp_50_1_6v")
    m = MotorModel.from_part(part)
    tau = 0.15 * 0.0980665
    rpm = ideal(m).speed_at(m, tau) * 60 / (2 * math.pi)
    assert rpm == pytest.approx(490.0, rel=0.03)
    assert m.current_at(tau) == pytest.approx(0.32, rel=0.15)
    assert motor_efficiency_at_max_power(part.params) < 0.9


def test_top_speed_in_closed_form_equals_a_brute_force_search():
    m = MotorModel.from_part(get_part("tt_motor_6v"))
    s = Supply(4.8, 0.16, 0.0, 0.36, 0.4, 2)
    r, mass, crr, cd_a = 0.0325, 0.41, 0.03, 0.005

    def net_force(v):
        drive = s.n * D_TAU * s.torque_at(m, v / r) / r
        return drive - (crr * mass * 9.81 + 0.5 * 1.2 * cd_a * v * v)

    lo, hi = 0.0, 5.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if net_force(mid) > 0 else (lo, mid)
    v, _ = top_speed(m, s, r, mass, crr, cd_a)
    assert v == pytest.approx(lo, rel=1e-6)


# ---------------------------------------------------------------- reference robot: Pololu Zumo
def test_speed_ratios_across_gear_ratios_match_the_zumo_measurements():
    """Pololu measured a Zumo (tracks, 500 g, 6 V) at 100, 65 and 50 cm/s with the 50:1, 75:1 and 100:1 motors.

    The wheel radius is not published, so it is fitted on the 75:1 motor and the other two are predicted. The result is
    recorded in docs/RESULTS_LAB.md; the tolerance here is what the model is expected to do, not a claim of exactness."""
    measured = {"pololu_hp_50_1_6v": 1.00, "pololu_hp_75_1_6v": 0.65, "pololu_hp_100_1_6v": 0.50}

    def predict(name, r):
        m = MotorModel.from_part(get_part(name))
        return top_speed(m, Supply(6.0, 0.0, 0.0, 0.0, 0.0, 2), r, 0.5, 0.06, 0.0)[0]

    lo, hi = 0.005, 0.05
    for _ in range(60):                                                          # fit the radius on the 75:1 motor
        r = 0.5 * (lo + hi)
        lo, hi = (r, hi) if predict("pololu_hp_75_1_6v", r) < measured["pololu_hp_75_1_6v"] else (lo, r)
    r_fit = 0.5 * (lo + hi)
    assert 0.010 < r_fit < 0.025                                                 # a plausible sprocket for a 10 cm robot
    errors = {n: predict(n, r_fit) / v - 1 for n, v in measured.items()}
    assert abs(errors["pololu_hp_50_1_6v"]) < 0.15 and abs(errors["pololu_hp_100_1_6v"]) < 0.15, errors


# ---------------------------------------------------------------- build -> vehicle
def test_the_esp32_rover_becomes_a_valid_vehicle_with_sensible_numbers():
    r = evaluate(ROVER)
    assert r["ok"], r["findings"]
    v = r["vehicle"]
    assert v["kind"] == "ground_wheeled" and v["params"]["drive"] == "diff2wd"
    assert 0.3 < v["body"]["mass"] < 0.6                                         # a 4-AA rover with a chassis plate
    assert 0.2 < v["params"]["max_speed"] < 0.9
    assert v["params"]["wheel_radius"] == pytest.approx(0.0325)
    kinds = [s["type"] for s in v["sensors"]]
    assert kinds.count("camera") == 1 and "rangefinder" in kinds and "battery" in kinds
    cam = next(s for s in v["sensors"] if s["type"] == "camera")
    assert cam["tilt_deg"] == -10 and cam["height"] == 0.075 and cam["fps"] == 12
    batt = next(s for s in v["sensors"] if s["type"] == "battery")
    assert batt["cells"] == 4 and batt["chemistry"] == "nimh"
    facts = {f["key"]: f for f in r["facts"]}
    assert facts["top_speed"]["lo"] < facts["top_speed"]["value"] < facts["top_speed"]["hi"]    # spent < nominal < fresh
    assert 30 < facts["runtime"]["value"] < 600
    assert r["budget"]["mass_total"] == pytest.approx(sum(m["kg"] for m in r["budget"]["mass"]))
    from simlab import VehicleSpec
    VehicleSpec.from_dict(v).check()


def test_the_hand_calculation_agrees_with_the_model():
    """Top speed worked out independently from the numbers the model reports."""
    b = Build.from_dict(ROVER)
    from simlab.parts.derive import build_to_vehicle
    spec, dv = build_to_vehicle(b)
    n = dv.numbers
    motor = get_part("tt_motor_6v").params
    w_nl = motor["no_load_rpm"] * 2 * math.pi / 60
    f_roll = 0.03 * n["mass"] * 9.81
    tau = f_roll * 0.0325 / (2 * D_TAU)
    k = (motor["stall_current_a"] - motor["no_load_current_a"]) / motor["stall_torque_nm"]
    i_m = motor["no_load_current_a"] + k * tau
    i_elec = 0.15 + 0.18 + 0.015 + 0.06
    i_batt = (5.0 * i_elec) / (0.9 * 4.8) + 0.002
    v_m = 4.8 - 0.36 * i_m - (2 * i_m + i_batt) * 0.16
    w = w_nl * (v_m / 6.0 - tau / motor["stall_torque_nm"])
    assert spec.params["max_speed"] == pytest.approx(w * 0.0325, rel=0.02)         # (2 % allows for the small air-drag term)


def test_changing_parts_moves_the_numbers_the_right_way():
    base = {f["key"]: f["value"] for f in evaluate(ROVER)["facts"]}
    heavy = {f["key"]: f["value"] for f in evaluate(rover(chassis={"frame_mass_kg": 1.5}))["facts"]}
    assert heavy["top_speed"] < base["top_speed"] and heavy["accel"] < base["accel"] and heavy["runtime"] < base["runtime"]
    big = {f["key"]: f["value"] for f in evaluate(rover(drive={"wheel": "wheel_85mm"}))["facts"]}
    assert big["top_speed"] > base["top_speed"] and big["accel"] < base["accel"]
    more_cells = {f["key"]: f["value"] for f in evaluate(rover(power={"series": 5}))["facts"]}
    assert more_cells["top_speed"] > base["top_speed"]
    twin = {f["key"]: f["value"] for f in evaluate(rover(power={"parallel": 2}))["facts"]}
    assert twin["runtime"] > 1.5 * base["runtime"]
    geared = {f["key"]: f["value"] for f in evaluate(rover(drive={"motor": "pololu_hp_100_1_6v"}))["facts"]}
    assert geared["top_speed"] > base["top_speed"]                                    # 310 rpm against the TT motor's 200 rpm, at the same voltage
    limited = evaluate(rover(drive={"speed_limit_mps": 0.2}))
    assert limited["vehicle"]["params"]["max_speed"] == pytest.approx(0.2)
    assert next(f for f in limited["facts"] if f["key"] == "top_speed")["hi"] <= 0.2 + 1e-9


def test_calibration_numbers_replace_the_predictions():
    base = evaluate(ROVER)
    cal = rover(calibration={"speed_scale": 0.8, "mass_kg": 0.5, "battery_derate": 0.5, "yaw_gain": 0.5})
    r = evaluate(cal)
    assert r["vehicle"]["body"]["mass"] == pytest.approx(0.5)
    assert r["vehicle"]["params"]["yaw_gain"] == 0.5
    assert r["vehicle"]["params"]["max_speed"] < base["vehicle"]["params"]["max_speed"]
    runtime = lambda res: next(f for f in res["facts"] if f["key"] == "runtime")["value"]       # noqa: E731
    assert runtime(r) < runtime(base)
    assert any("your measurement" in a for a in r["assumptions"])


# ---------------------------------------------------------------- the rules
def test_the_brownout_story_from_the_hardware_guide():
    """docs/HARDWARE.md: an ESP32 wired to 4xAA NiMH browns out; behind a boost converter it does not."""
    assert evaluate(ROVER)["ok"]
    bare = evaluate(rover(power={"regulator": ""}))
    assert not bare["ok"] and "E05" in ids(bare, "error")
    err = next(f for f in bare["findings"] if f["id"] == "E05" and f["severity"] == "error")
    assert "brown out" in err["title"] and err["fix"]["action"] == "add_part" and err["fix"]["kind"] == "regulator"
    assert err["fix"]["constraints"]["topology"] == "boost"


def test_a_boost_converter_passes_a_fresh_pack_through_and_a_camera_board_may_not_like_it():
    r = evaluate(ROVER)
    msgs = " ".join(f["title"] + f["detail"] for f in r["findings"] if f["id"] == "E05")
    assert "front" in msgs and "5.60" in msgs and "passes a fresh pack" in msgs                  # 4 x 1.4 V = 5.6 V against the camera board's 5.25 V


def test_driver_current_rules():
    small = evaluate(rover(drive={"driver": "drv8833_module_pw"}))                                # 0.5 A RMS package, motor stalls at ~1 A
    assert "E03" in ids(small, "warn") and small["ok"]
    two_amp = evaluate(rover(drive={"motor": "gearmotor_12v_200rpm", "driver": "l9110s_module"}, power={"battery": "lipo_cell_1000_25c", "series": 3, "regulator": ""}))
    assert "E03" in ids(two_amp, "error")


def test_pack_voltage_rules():
    hi = evaluate(rover(power={"battery": "lipo_cell_1000_25c", "series": 4, "regulator": ""}))   # 16.8 V fresh into a 10.8 V driver and a 12 V board
    assert {"E01", "E02", "E05"} <= ids(hi)
    assert "E02" in ids(hi, "error") and "E05" in ids(hi, "error")
    weak = evaluate(rover(drive={"motor": "gearmotor_12v_200rpm"}, power={"series": 4}))
    assert "E01" in ids(weak, "warn")
    big = evaluate(rover(drive={"motor_count": 4}, chassis={"drive": "skid4wd"}))                 # four TT motors from 4 AA cells: 4 A stall > the pack's rating
    assert "E04" in ids(big, "error")


def test_pin_interface_and_logic_level_rules():
    cam_brain = evaluate(rover(controller="esp32cam_ai_thinker", drive={"driver": "l298n_module"}, power={"series": 4}))
    assert "E07" in ids(cam_brain, "error") and "E09" in ids(cam_brain, "warn")                    # 6 driver pins on a board with 4 free
    five_volt = evaluate(ROVER)
    assert "E06" in ids(five_volt, "warn")                                                         # HC-SR04 echo is 5 V
    shifted = evaluate(rover(controller="arduino_nano", power={"series": 5}))
    assert "E06" not in ids(shifted)
    cam_integrated = evaluate(rover(controller="esp32_devkit", sensors=[{"part": "ov2640_on_esp32cam", "name": "front"}]))
    assert "E08" in ids(cam_integrated, "error")                                                   # that camera is built onto a different board
    esp_cam = evaluate(rover(controller="esp32cam_ai_thinker", sensors=[{"part": "ov2640_on_esp32cam", "name": "front"}], drive={"driver": "drv8833_module_pwp"}))
    assert "E08" not in ids(esp_cam)


def test_user_parts_drive_the_rules(user_dirs):
    save_part(Part("one_channel_driver", "driver_dc", "", dict(get_part("drv8833_module_pwp").params, channels=1), 0.002, "test", "assumed"))
    save_part(Part("hot_logic_driver", "driver_dc", "", dict(get_part("drv8833_module_pwp").params, logic_high_min_v=4.0), 0.002, "test", "assumed"))
    save_part(Part("bare_mcu", "mcu", "", dict(get_part("esp32_devkit").params, interfaces=[]), 0.01, "test", "assumed"))
    save_part(Part("spi_imu", "sensor_imu", "", dict(get_part("mpu6050").params, interface="spi"), 0.002, "test", "assumed"))
    assert "E02" in ids(evaluate(rover(drive={"driver": "one_channel_driver"})), "error")
    assert "E06" in ids(evaluate(rover(drive={"driver": "hot_logic_driver"})), "error")
    r = evaluate(rover(controller="bare_mcu", sensors=[{"part": "spi_imu", "name": "imu"}]))
    assert "E08" in ids(r, "error")
    assert evaluate(rover(sensors=[{"part": "spi_imu", "name": "imu"}]))["ok"]                      # the ESP32 has spi


def test_performance_rules():
    heavy = evaluate(rover(chassis={"frame_mass_kg": 25.0}))
    assert {"M02"} <= ids(heavy, "error") or {"M01"} <= ids(heavy, "error")
    skid = evaluate(rover(chassis={"drive": "skid4wd", "frame_mass_kg": 4.0, "length": 0.4, "width": 0.3, "height": 0.15}, drive={"motor_count": 4}))
    assert "M03" in ids(skid)                                                                       # four small motors cannot skid-steer a 4.5 kg rover
    wheels_too_big = evaluate(rover(drive={"wheel": "wheel_100mm_rubber"}))
    assert "M06" in ids(wheels_too_big, "error")                                                    # 10 cm wheels under a 5.5 cm tall body
    bright = evaluate(rover(lamp={"part": "led_headlamp_bright", "count": 12}))
    assert "E10" in ids(bright, "info")


def test_findings_are_sorted_errors_first_and_explain_themselves():
    r = evaluate(rover(power={"regulator": ""}, drive={"driver": "drv8833_module_pw"}))
    order = [f["severity"] for f in r["findings"]]
    assert order == sorted(order, key=["error", "warn", "info"].index)
    assert all(f["title"] and f["id"] for f in r["findings"])


# ---------------------------------------------------------------- bad input never crashes
@pytest.mark.parametrize("bad", [None, {}, [], 5, "x", {"name": "x"}, {"name": "x", "platform": "submarine"}, {"name": 5}, {"name": "ok", "drive": 3},
                                 rover(drive={"motor": "nope"}), rover(drive={"motor_count": 3}), rover(power={"series": 0}), rover(extra=1),
                                 rover(sensors="camera"), rover(sensors=[{"part": "tt_motor_6v"}]), rover(lamp={"part": "hc_sr04"}),
                                 rover(calibration={"speed_scale": -1}), rover(calibration={"bogus": 1})])
def test_evaluate_never_raises_on_garbage(bad):
    r = evaluate(bad)
    assert r["ok"] is False and r["vehicle"] is None
    assert r["issues"] or r["findings"]


def test_unknown_part_errors_list_what_is_valid():
    b, issues = Build.parse(rover(drive={"motor": "tt_motor_9v"}))
    assert b is None
    msg = " ".join(i.msg for i in issues)
    assert "tt_motor_9v" in msg and "tt_motor_6v" in msg
    with pytest.raises(BuildError):
        Build.from_dict({"name": "x"})
    b, issues = Build.parse(rover(platform="submarine"))
    assert b is None and "platform" in issues[0].field


def test_build_round_trips_through_yaml(user_dirs):
    b = Build.from_dict(ROVER)
    from simlab.parts import build_names, get_build, save_build
    path = save_build(b)
    assert yaml.safe_load(path.read_text())["name"] == "esp32_pipe_rover"
    assert build_names() == ["esp32_pipe_rover"]
    assert get_build("esp32_pipe_rover").to_dict() == b.to_dict()
    with pytest.raises(KeyError):
        get_build("../etc/passwd")


# ---------------------------------------------------------------- the gate: a built machine runs in the simulator at the predicted speed
def test_the_built_rover_reaches_the_predicted_top_speed_in_mujoco():
    pytest.importorskip("mujoco")
    from simlab import VehicleSpec, get_task_spec
    from simlab.envs import Sim
    r = evaluate(ROVER)
    spec = VehicleSpec.from_dict(r["vehicle"])
    sim = Sim(spec, get_task_spec("goto_empty"))
    sim.reset(seed=3)
    peak = 0.0
    for _ in range(int(2.0 * 20)):                                   # 2 s is plenty: the first-order lag settles in about 1 s
        sim.step_physical([spec.params["max_speed"], 0.0])
        peak = max(peak, float(sim.obs.state.vel_body[0]))
    assert peak == pytest.approx(spec.params["max_speed"], rel=0.05)
    sim.close()
