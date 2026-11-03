"""The USB-serial rover: driver, safety behaviour and the Hardware manager's pre-drive gate, against the firmware emulator."""
import time

import pytest

from sewerbot.hardware.mock import MockSerialPort
from sewerbot.hardware.serial_rover import RoverLinkError, SerialRover, is_serial_target, open_port


def wait(cond, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def rig():
    port = MockSerialPort()
    rover = SerialRover("mock", opener=lambda *_: port, boot_wait=0.0)
    yield rover, port
    rover.close()


def test_targets_are_recognised():
    assert all(is_serial_target(t) for t in ("COM6", "com12", "serial:COM6", "serial:mock", "/dev/ttyUSB0"))
    assert not any(is_serial_target(t) for t in ("", "127.0.0.1", "192.168.29.5:4210", "community"))


def test_the_first_byte_is_a_stop_and_drive_commands_map_to_keys(rig):
    rover, port = rig
    assert wait(lambda: rover.connected)
    assert wait(lambda: port.history and port.history[0][1] == "S")        # a known state before anything else
    for (v, w), state in (((0.2, 0.0), "F"), ((-0.2, 0.0), "B"), ((0.0, 1.0), "L"), ((0.0, -1.0), "R")):
        rover.drive(v, w, hold=2.0)
        assert wait(lambda: port.state == state), (v, w, port.state)
    rover.stop()
    assert wait(lambda: port.state == "S")


def test_a_drive_command_lapses_by_itself(rig):
    rover, port = rig
    rover.drive(0.2, 0.0, hold=0.3)
    assert wait(lambda: port.state == "F")
    assert wait(lambda: port.state == "S", 2.0)                            # nobody pressed stop


def test_heartbeat_keeps_the_firmware_watchdog_quiet_but_the_firmware_stops_alone_without_it(rig):
    rover, port = rig
    rover.drive(0.2, 0.0, hold=3.0)
    assert wait(lambda: port.state == "F")
    time.sleep(1.4)                                                        # longer than the 1 s firmware watchdog
    assert port.state == "F" and "link lost" not in rover.last_line        # the heartbeat did its job
    solo = MockSerialPort()
    solo.write(b"w")
    assert wait(lambda: solo.state == "F")
    assert wait(lambda: solo.state == "S", 2.5)                            # no bytes at all: the firmware stops itself
    solo.close()


def test_distance_sensor_blocks_forward_and_spin_still_works(rig):
    rover, port = rig
    port.obstacle_cm = 8.0
    assert wait(lambda: rover.telemetry.get("dist_cm") == 8.0, 2.0)
    rover.drive(0.2, 0.0, hold=2.0)
    assert wait(lambda: "obstacle" in rover.blocked)
    time.sleep(0.3)
    assert port.state == "S" and port.odo_m <= 0.001
    rover.drive(0.0, 1.0, hold=2.0)                                        # turning away is allowed
    assert wait(lambda: port.state == "L")
    port.obstacle_cm = 120.0
    assert wait(lambda: rover.telemetry.get("dist_cm") == 120.0, 2.0)
    rover.drive(0.2, 0.0, hold=2.0)
    assert wait(lambda: port.state == "F") and rover.blocked == ""


def test_a_missing_or_silent_sensor_counts_as_blocked(rig):
    rover, port = rig
    assert wait(lambda: rover.telemetry.get("dist_cm") is not None, 2.0)
    port.obstacle_cm = -1.0                                                # the firmware's "no valid reading"
    assert wait(lambda: rover.telemetry.get("dist_cm") is None and rover.sensors["dist"] > 0, 2.0)
    rover.drive(0.2, 0.0, hold=2.0)
    assert wait(lambda: "no valid reading" in rover.blocked)
    port.obstacle_cm = 100.0
    assert wait(lambda: rover.telemetry.get("dist_cm") == 100.0, 2.0)
    rover.sensors["dist"] -= 10                                            # as if the sensor stopped talking
    assert "silent" in rover._guard("w")


def test_ir_sensor_blocks_forward(rig):
    rover, port = rig
    port.ir_hit = True
    assert wait(lambda: rover.telemetry.get("ir") == 1, 2.0)
    rover.drive(0.2, 0.0, hold=2.0)
    assert wait(lambda: "IR" in rover.blocked)
    assert port.state != "F"


def test_close_leaves_the_rover_stopped_and_a_dead_port_is_reported_not_raised():
    port = MockSerialPort()
    rover = SerialRover("mock", opener=lambda *_: port, boot_wait=0.0)
    rover.drive(0.2, 0.0, hold=5.0)
    assert wait(lambda: port.state == "F")
    rover.close()
    assert port.state == "S"
    port2 = MockSerialPort()
    r2 = SerialRover("mock", opener=lambda *_: port2, boot_wait=0.0)
    port2.close()                                                          # the cable was pulled
    assert wait(lambda: r2.error != "", 2.0) and not r2.connected
    r2.drive(0.2, 0.0)
    r2.close()


def test_speed_level_steps_and_clamps(rig):
    rover, port = rig
    assert wait(lambda: rover.connected)
    assert rover.set_level(130) == 130 and wait(lambda: port.level == 130)
    assert rover.set_level(10) == 100 and wait(lambda: port.level == 100)
    assert rover.set_level(999) == 255 and wait(lambda: port.level == 255)


def test_estimates_follow_calibration(rig):
    rover, port = rig
    rover.configure(vmax=0.31)
    rover.drive(0.2, 0.0, hold=2.0)
    assert wait(lambda: abs(rover.telemetry.get("v", 0) - 0.31) < 1e-6) and rover.calibrated
    rover.drive(0.0, 1.0, hold=2.0)
    assert wait(lambda: rover.telemetry.get("w", 0) > 0.5)


def test_a_missing_port_gives_a_message_you_can_act_on():
    with pytest.raises(RoverLinkError) as e:
        open_port("COM98", 115200)
    assert "COM98" in str(e.value)


# ------------------------------------------------------------------ the Hardware manager's gate
def test_drive_controls_stay_locked_until_the_checklist_is_complete(tmp_path, monkeypatch):
    import sewerbot.brain.hardware as hwmod
    from sewerbot.brain.detectors import make_detector
    monkeypatch.setattr(hwmod, "CONFIG", tmp_path / "hw.json")
    monkeypatch.setattr(hwmod, "REAL_DIR", tmp_path / "real")
    monkeypatch.setattr("sewerbot.hardware.serial_rover.SerialRover.ready", property(lambda self: True))
    hw = hwmod.HardwareManager(make_detector("classical"))
    try:
        with pytest.raises(hwmod.NotArmed):
            hw.arm({})                                                     # not connected yet
        hw.connect("serial:mock", "")
        assert wait(lambda: hw.state()["rover_connected"])
        st = hw.state()
        assert st["rover_link"] == "serial" and st["armed"] is False and len(st["pre_drive"]) == 4
        with pytest.raises(hwmod.NotArmed):
            hw.drive(0.2, 0.0)
        with pytest.raises(hwmod.NotArmed) as e:
            hw.arm({"wheels_off": True, "battery_ok": True})
        assert "stop" in str(e.value).lower()
        hw.arm({k: True for k, _ in hwmod.PRE_DRIVE})
        hw.drive(0.2, 0.0)
        assert wait(lambda: hw.rover._ser.state == "F")
        hw.disarm()
        assert wait(lambda: hw.rover._ser.state == "S") and hw.state()["armed"] is False
        with pytest.raises(hwmod.NotArmed):
            hw.drive(0.2, 0.0)
        cal = hw.calibrate_speed(0.2, 3.0, 0.45)                           # 45 cm in 3 s at this speed level
        assert cal["vmax_new"] == pytest.approx(0.15) and hw.serial_cal["calibrated"]
        assert hw.calibrate_yaw(1.0, 3.0, 360)["yaw_gain_new"] == pytest.approx(1.396, abs=0.01)
    finally:
        hw.disconnect()


def test_hardware_endpoints_map_errors_to_409(tmp_path, monkeypatch):
    """The routes only, with a Hardware manager of our own: starting the whole dashboard would bind the Webots port (8770)."""
    from fastapi.testclient import TestClient
    import sewerbot.brain.hardware as hwmod
    from sewerbot.brain.detectors import make_detector
    monkeypatch.setattr(hwmod, "CONFIG", tmp_path / "hw.json")
    monkeypatch.setattr(hwmod, "REAL_DIR", tmp_path / "real")
    import sewerbot.dashboard.app as appmod
    hw = hwmod.HardwareManager(make_detector("classical"))
    monkeypatch.setattr(appmod, "hw", hw)
    c = TestClient(appmod.app)                                 # no `with`: the startup handlers (SimManager, port 8770) do not run
    try:
        assert c.get("/api/hw/ports").status_code == 200
        assert c.post("/api/hw/connect", json={"rover_host": "COM98", "cam_url": ""}).status_code == 409
        assert c.post("/api/hw/connect", json={"rover_host": "serial:mock", "cam_url": ""}).status_code == 200
        assert c.post("/api/hw/drive", json={"v": 0.2, "w": 0}).status_code == 409        # not armed
        assert c.post("/api/hw/arm", json={"checklist": {}}).status_code == 409
    finally:
        hw.disconnect()
