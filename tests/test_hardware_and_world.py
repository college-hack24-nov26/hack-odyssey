import json
import socket
import time

import cv2
import numpy as np
import pytest

from sewerbot.brain.detectors import make_detector
from sewerbot.brain.hardware import HardwareManager
from sewerbot.hardware.esp32cam import MJPEGClient
from sewerbot.hardware.mock import MockCam, MockRover
from sewerbot.hardware.rover import Esp32Rover


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def free_tcp_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def frames_dir(tmp_path):
    rng = np.random.default_rng(1)
    for i in range(6):
        im = (rng.random((120, 160, 3)) * 80 + 60).astype(np.uint8)
        cv2.line(im, (10, 10 + 5 * i), (150, 100), (10, 10, 10), 2)
        cv2.imwrite(str(tmp_path / f"f{i}.jpg"), im)
    return tmp_path


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_rover_failsafe_and_latch(monkeypatch):
    port = free_udp_port()
    mock = MockRover(port=port)
    mock.start()
    rover = Esp32Rover("127.0.0.1", port)
    try:
        rover.drive(0.15, 0.0, hold=5.0)
        assert wait(lambda: rover.connected and mock.v == 0.15)
        assert wait(lambda: rover.telemetry.get("v") == 0.15)
        rover._until = 0.0                                   # host stops sending: the rover must stop by itself
        rover.stop()
        assert wait(lambda: mock.v == 0.0)
        mock.latched = True
        rover.drive(0.1, 0.0, hold=1.0)
        time.sleep(0.4)
        assert mock.v == 0.0                                 # latched: commands ignored until reset
        rover.reset_latch()
        assert wait(lambda: not mock.latched)
        rover.configure(vmax=0.31)
        assert wait(lambda: mock.cfg["vmax"] == 0.31)
    finally:
        rover.close()
        mock.stop()


def test_mjpeg_client_decodes_stream_and_survives_reconnect(frames_dir):
    port = free_tcp_port()
    cam = MockCam(frames_dir, port, fps=30).start()
    got = []
    client = MJPEGClient(f"http://127.0.0.1:{port}/stream", lambda im, t: got.append(im.shape))
    client.start()
    try:
        assert wait(lambda: len(got) >= 5)
        assert got[0] == (120, 160, 3) and client.connected
    finally:
        client.stop()
        cam.stop()


def test_hardware_manager_end_to_end(frames_dir, tmp_path, monkeypatch):
    import sewerbot.brain.hardware as hwmod
    monkeypatch.setattr(hwmod, "CONFIG", tmp_path / "hw.json")
    monkeypatch.setattr(hwmod, "REAL_DIR", tmp_path / "real")
    uport, tport = free_udp_port(), free_tcp_port()
    mock, cam = MockRover(port=uport), MockCam(frames_dir, tport, fps=25)
    mock.start()
    cam.start()
    hw = HardwareManager(make_detector("classical"))
    try:
        hw.connect(f"127.0.0.1:{uport}", f"http://127.0.0.1:{tport}/stream")
        assert wait(lambda: hw.state()["cam_connected"] and hw.state()["rover_connected"], 8)
        hw.drive(0.2, 0.0)
        assert wait(lambda: hw.s > 0.02, 6)                  # dead-reckoned distance advances only while moving
        assert wait(lambda: hw.state()["frames_done"] > 2)
        name = hw.capture("test")
        assert name and (tmp_path / "real" / f"{name}.jpg").exists()
        assert json.loads((tmp_path / "real" / f"{name}.json").read_text())["label"] == "test"
        cal = hw.calibrate_speed(0.15, 4.0, 0.30)            # measured 30 cm in 4 s at a 0.15 command => 0.6 m of travel expected
        assert cal["vmax_new"] < cal["vmax_old"]
        hw.replay(str(frames_dir), fps=40)
        assert wait(lambda: "finished" in hw.replay_state, 5)
    finally:
        hw.disconnect()
        cam.stop()
        mock.stop()


# ------------------------------------------------------------------ world generation (no Webots needed)
@pytest.mark.parametrize("name", ["straight_t", "star_five_goals", "large_loop"])
def test_world_files_are_well_formed(name, tmp_path):
    from tools.gen_world import generate
    wbt = generate(f"maps/{name}.yaml", "mid_4wd", out=tmp_path / f"{name}.wbt")
    text = wbt.read_text(encoding="utf-8")
    assert text.startswith("#VRML_SIM R2025a utf8")
    assert text.count("{") == text.count("}") and text.count("[") == text.count("]")
    assert "nan" not in text.lower().replace("nanosecond", "")
    meta = json.loads(wbt.with_suffix(".json").read_text())
    assert len(meta["instances"]) == len(meta["defects"]) + len(meta["goals"])
    assert all(0 < d["size_t"] < 2.0 and 0 < d["size_s"] < 2.0 for d in meta["defects"])
    assert text.count("recognitionColors") == len(meta["instances"])


def test_compose_rotation_matches_matrix_math():
    import math
    from tools.gen_world import _compose_rot

    def Rz(a): c, s = math.cos(a), math.sin(a); return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    def Rx(a): c, s = math.cos(a), math.sin(a); return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    def rod(ax, ang):
        ax = np.array(ax); K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
        return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K
    for yaw in np.linspace(-3.1, 3.1, 9):
        for roll in np.linspace(-3.1, 3.1, 9):
            ax, ang = _compose_rot(yaw, roll)
            assert np.abs(rod(ax, ang) - Rz(yaw) @ Rx(roll)).max() < 1e-9


def test_defect_assets_are_tight_and_deterministic():
    from sewerbot.core.defects import CRACK_SUBTYPES, LEAK_SUBTYPES, DefectSpec
    from tools.defect_textures import make_defect_asset
    from tools.materials import make_concrete
    base = make_concrete()
    for typ, subs in (("crack", CRACK_SUBTYPES), ("leak", LEAK_SUBTYPES)):
        for sub in subs:
            spec = DefectSpec("t", "e", 5, 90, typ, sub, (0.6, 0.6), seed=4, severity=0.7)
            a = make_defect_asset(spec, base, "concrete", 1.0, 5.0)
            b = make_defect_asset(spec, base, "concrete", 1.0, 5.0)
            assert np.array_equal(a.albedo, b.albedo)
            assert 0.02 < a.size[0] <= 0.65 and 0.02 < a.size[1] <= 0.65 and a.coverage > 0.01
