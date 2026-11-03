"""Firmware v2 protocol, the v2 emulator, the bring-up checks, the pin map and the flash tool's guards."""
from __future__ import annotations

import time

import pytest

from sewerbot.hardware import bringup, flash as flashmod, pins as pinmod
from sewerbot.hardware.mock_v2 import MockSerialPortV2
from sewerbot.hardware.serial_rover import SerialRover, parse_kv


def make(**kw) -> SerialRover:
    return SerialRover("serial:mock-v2", opener=lambda t, b: MockSerialPortV2(**kw), boot_wait=0.05, us_guard=False)


@pytest.fixture
def rover():
    made = []

    def _make(**kw):
        r = make(**kw)
        made.append(r)
        time.sleep(0.2)
        return r
    yield _make
    for r in made:
        r.close()


# ------------------------------------------------------------------ parsing
def test_parse_kv_keeps_text_and_numbers():
    d = parse_kv("hello fw=2.0.0 reset=brownout mpu=1 pins=26,27,32,33 mpu_id=0x68 ip=-")
    assert d["fw"] == "2.0.0" and d["mpu"] == 1.0 and d["pins"] == "26,27,32,33" and d["mpu_id"] == "0x68" and d["ip"] == "-"


def test_v1_telemetry_still_parses(rover):
    r = rover()
    r._on_line("tel up=12 speed=180 dir=F dist=23.5 ir=0")
    assert r.telemetry["dist_cm"] == 23.5 and r.telemetry["level"] == 180
    r._on_line("tel up=13 speed=180 dir=F hold=1 yaw=2.5 rate=-0.4")
    assert r.telemetry["yaw_deg"] == 2.5 and r.telemetry["rate_dps"] == -0.4 and r.telemetry["hold"] == 1


# ------------------------------------------------------------------ the emulator speaks the real protocol
def test_hello_reports_sensors_and_reset_reason(rover):
    r = rover(reset_reason="brownout", brownouts=2)
    h = r.ask("HELLO", "hello")
    assert h["reset"] == "brownout" and h["brownouts"] == 2 and h["mpu"] == 1 and h["pins"] == "26,27,32,33,21,22,18,35"


def test_missing_mpu_is_reported(rover):
    r = rover(mpu_present=False)
    assert r.ask("HELLO", "hello")["mpu"] == 0


def test_i2cscan_refuses_motor_pins_and_finds_the_mpu(rover):
    r = rover(mpu_pins=(16, 17))
    refused = r.ask("I2CSCAN 32 33", "i2cscan")
    assert refused is not None and "found" not in refused                      # the refusal line has no 'found='
    assert r.ask("I2CSCAN 16 17", "i2cscan")["found"] == "0x68"
    assert r.ask("I2CSCAN 21 22", "i2cscan")["found"] == "-"


def test_timed_move_reports_the_turn_of_a_weak_right_motor(rover):
    r = rover(side_gain=(1.0, 0.88))                                         # the right motor is 12 % weak: the rover pulls to the right (negative yaw)
    done = r.run_move("F", 1500)
    assert done is not None and done["why"] == "time"
    assert done["yaw"] < -3.0, done
    assert r.moves, "the move streamed no samples"


def test_a_balanced_rover_goes_straight(rover):
    r = rover()
    assert abs(r.run_move("F", 1500)["yaw"]) < 1.5


def test_trim_cancels_the_drift(rover):
    r = rover(side_gain=(1.0, 0.88))
    r.ask("CFG trimR=1.136", "cfg")                                          # 1 / 0.88
    assert abs(r.run_move("F", 1500)["yaw"]) < 1.5


def test_heading_hold_straightens_a_weak_motor_within_its_bound(rover):
    r = rover(side_gain=(1.0, 0.88))
    open_loop = r.run_move("F", 2000)["yaw"]
    held = r.run_move("F", 2000, hold=True)
    assert abs(held["yaw"]) < abs(open_loop) / 2
    assert 0 < abs(held["corr_avg"]) <= 27.1                                  # 15 % of duty 180


def test_hold_with_the_wrong_gyro_sign_is_visible_in_the_bringup_turn_check(rover):
    r = rover(gyro_mount_sign=-1)
    answers = iter([])
    # the turn check reads yaw before and after a hand turn; simulate it by rotating the emulated rover
    port = r._ser

    def ask(prompt):
        if "turn it by hand" in prompt:
            port.yaw_est += port.cfg["gyrosign"] * -90.0                      # the gyro reads the left turn with the wrong sign
        return next(answers, "")
    rep = bringup.Report()
    rep.add(bringup.Check("gyro", "Gyro", "pass", ""))
    bringup.check_turn(r, rep, ask)
    c = rep.get("turn")
    assert c.status == "warn" and c.data["gyrosign"] == -1
    assert r.ask("CFG", "cfg")["gyrosign"] == -1


def test_direct_drive_ends_by_itself(rover):
    r = rover()
    r.drive_duty(150, 150, 300)
    time.sleep(0.8)
    assert r._ser.mode == "idle"                                              # stopped without any further command


def test_watchdog_stops_a_move_when_the_host_goes_quiet(rover):
    r = rover()
    r._stop.set()                                                             # stop the heartbeat thread: the host "goes quiet"
    time.sleep(0.1)
    r._ser.write(b"!MOVE F 5000\n")
    time.sleep(1.5)
    assert r._ser.mode == "idle"


# ------------------------------------------------------------------ bring-up
def test_bringup_on_a_healthy_emulated_rover(rover, tmp_path):
    r = rover()
    rep = bringup.run_bringup(r, ask=None, out_dir=tmp_path, listen_s=0.1)
    assert rep.get("link").status == "pass" and rep.get("firmware").status == "pass"
    assert rep.get("mpu").status == "pass" and rep.get("gyro").status == "pass"
    assert rep.ok_to_test and (tmp_path / sorted(p.name for p in tmp_path.iterdir())[0] / "report.json").exists()


def test_bringup_flags_a_brownout_and_a_missing_mpu(rover, tmp_path):
    r = rover(reset_reason="brownout", brownouts=3, mpu_present=False)
    rep = bringup.run_bringup(r, ask=None, out_dir=tmp_path, listen_s=0.1)
    assert rep.get("firmware").status == "warn" and "brownout" in rep.get("firmware").detail
    mpu = rep.get("mpu")
    assert mpu.status == "fail" and "GPIO32/33" in mpu.detail and "RIGHT MOTOR" in mpu.detail
    assert not rep.ok_to_test


def test_bringup_finds_an_mpu_on_other_pins(rover, tmp_path):
    r = rover(mpu_pins=(16, 17))
    rep = bringup.run_bringup(r, ask=None, out_dir=tmp_path, listen_s=0.1)
    assert rep.get("mpu").status == "warn" and "GPIO16" in rep.get("mpu").detail


def test_bringup_skips_motion_without_confirmation(rover, tmp_path):
    r = rover()
    rep = bringup.run_bringup(r, ask=lambda p: "no", drive=True, out_dir=tmp_path, listen_s=0.1)
    assert rep.get("motors").status == "skip"
    assert r._ser.mode == "idle"


def test_bringup_motor_check_reports_a_reversed_wheel(rover, tmp_path):
    r = rover()
    answers = iter(["yes", "y", "n", "y", "y"])
    rep = bringup.Report()
    bringup.check_motors(r, rep, lambda p: next(answers), True)
    c = rep.get("motors")
    assert c.status == "warn" and "right forward" in c.detail and "wrong way" in c.detail


def test_bringup_power_check(tmp_path):
    def says(*values):
        it = iter(values)
        return lambda prompt: next(it)
    rep = bringup.Report()
    bringup.check_power(rep, says("6.9", "5.2"))
    assert rep.get("power").status == "warn" and "nearly empty" in rep.get("power").detail
    rep = bringup.Report()
    bringup.check_power(rep, says("7.9", "4.6"))
    assert rep.get("power").status == "fail"


def test_camera_check_uses_the_probe():
    rep = bringup.Report()
    bringup.check_camera(rep, "http://x/stream", probe=lambda u: {"ok": True, "fps": 9.5, "w": 96, "h": 96, "brightness": 120.0, "sharpness": 60.0})
    assert rep.get("camera").status == "pass"
    rep = bringup.Report()
    bringup.check_camera(rep, "http://x/stream", probe=lambda u: {"ok": True, "fps": 3.0, "w": 96, "h": 96, "brightness": 10.0, "sharpness": 60.0})
    assert rep.get("camera").status == "warn" and "too dark" in rep.get("camera").detail
    rep = bringup.Report()
    bringup.check_camera(rep, "http://x/stream", probe=lambda u: {"ok": False, "error": "no frames"})
    assert rep.get("camera").status == "fail"


def test_find_port_prefers_usb_serial_and_ignores_bluetooth():
    ports = [{"port": "COM4", "description": "Standard Serial over Bluetooth link (COM4)"}, {"port": "COM5", "description": "Standard Serial over Bluetooth link (COM5)"}]
    assert bringup.find_port(ports)[0] is None
    ports.append({"port": "COM6", "description": "Silicon Labs CP210x USB to UART Bridge (COM6)"})
    assert bringup.find_port(ports)[0] == "COM6"
    ports.append({"port": "COM7", "description": "USB-SERIAL CH340 (COM7)"})
    assert bringup.find_port(ports)[0] is None                                 # two candidates: never guess


# ------------------------------------------------------------------ pin map
def test_pin_check_catches_the_wiring_mistakes_made_on_this_rover():
    bad = dict(pinmod.DEFAULT_PINS, sda=32, scl=33, trig=34)
    problems = " | ".join(pinmod.check_pins(bad))
    assert "GPIO32" in problems and "GPIO33" in problems and "GPIO34" in problems and "input-only" in problems
    assert pinmod.check_pins(pinmod.DEFAULT_PINS) == []


def test_pins_become_compiler_flags():
    flags = pinmod.pins_to_flags(pinmod.DEFAULT_PINS)
    assert "-DSDA_PIN=21" in flags and "-DTRIG_PIN=18" in flags and "-DECHO_PIN=35" in flags and len(flags) == 8


def test_default_pins_match_the_firmware_header_comment():
    src = (flashmod.SKETCHES["v2"] / "esp32_rover_v2.ino").read_text(encoding="utf-8")
    for key, flag in pinmod.FLAGS.items():
        assert f"#define {flag} {pinmod.DEFAULT_PINS[key]}" in src, flag


# ------------------------------------------------------------------ flashing guards
def test_flash_refuses_without_the_confirm_word():
    with pytest.raises(flashmod.FlashError, match="not confirmed"):
        flashmod.flash("COM6", "yes please")


def test_flash_refuses_a_port_that_is_not_there(monkeypatch):
    monkeypatch.setattr(flashmod, "list_ports", lambda: [{"port": "COM3", "description": ""}])
    with pytest.raises(flashmod.FlashError, match="not connected"):
        flashmod.flash("COM6", flashmod.CONFIRM_WORD)


def test_flash_refuses_a_conflicting_pin_map(monkeypatch):
    monkeypatch.setattr(flashmod, "list_ports", lambda: [{"port": "COM6", "description": ""}])
    monkeypatch.setattr(flashmod, "find_cli", lambda: "arduino-cli")
    with pytest.raises(flashmod.FlashError, match="pin map"):
        flashmod.flash("COM6", flashmod.CONFIRM_WORD, pins=dict(pinmod.DEFAULT_PINS, sda=32, scl=33))


def test_wifi_secrets_go_to_a_temp_copy_only(tmp_path):
    out = flashmod.prepare("v2", {"ssid": 'My "Net"', "password": "pw"}, tmp_path)
    text = (out / "wifi_secrets.h").read_text()
    assert 'WIFI_SSID "My \\"Net\\""' in text and 'WIFI_PASS "pw"' in text
    assert not (flashmod.SKETCHES["v2"] / "wifi_secrets.h").exists()


def test_scrub_hides_the_password():
    assert "hunter2" not in flashmod._scrub("connecting with hunter2 now", ["hunter2"])


def test_manual_steps_name_the_sketch():
    assert "esp32_rover_v2.ino" in flashmod.manual_steps("v2") and "BOOT" in flashmod.manual_steps("v2")


@pytest.mark.skipif(flashmod.find_cli() is None, reason="arduino-cli is not installed")
def test_firmware_v2_compiles():
    r = flashmod.compile_sketch("v2")
    assert r["ok"], r["output"]
