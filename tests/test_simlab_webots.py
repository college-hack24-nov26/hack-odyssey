"""The Webots backend and the sewer task, without opening Webots: a fake controller speaks protocol v2 over a real socket.

A live run against the real Webots is in tools/webots_smoke.py (it opens the Webots window, so it is not part of pytest).
"""
import socket
import threading
import time

import cv2
import numpy as np
import pytest

from simlab import TaskSpec, get_task_spec, get_vehicle
from simlab.backends.webots import WebotsBackend
from simlab.envs import Sim
from simlab.protocol import HOST, PORT, Reader, hello_caps, pack, parse_cmd
from simlab.registry import BACKENDS
from simlab.spec import SpecError
from simlab.tasks.base import World


def jpeg(value: int) -> bytes:
    return cv2.imencode(".jpg", np.full((48, 64, 3), value, np.uint8))[1].tobytes()


class FakeWebots:
    """Plays the Webots controller: hello, then one observation per command, moving the rover as asked."""

    def __init__(self):
        self.commands, self.error = [], None

    def __call__(self, wbt):
        threading.Thread(target=self.run, daemon=True).start()
        return None

    def run(self):
        try:
            s = socket.create_connection((HOST, PORT), timeout=5)
            rd, x, yaw, t = Reader(), 0.0, 0.0, 0.0
            s.sendall(pack({"t": "hello", "proto": 2, "caps": hello_caps(["unicycle"], ["camera"]), "map": "fake"}))

            def obs(v=0.0, w=0.0):
                s.sendall(pack({"t": "obs", "time": t, "pose": {"x": x, "y": 0.0, "z": 0.1, "yaw": yaw}, "v": v, "w": w,
                                "ranges": {"front": 3.0, "left": 1.0, "right": 2.0}, "gt": []}, jpeg(int(40 + 10 * t) % 255)))
            obs()
            while True:
                data = s.recv(65536)
                if not data:
                    return
                for h, _ in rd.feed(data):
                    if h.get("t") == "quit":
                        return
                    if h.get("t") == "cmd":
                        space, u = parse_cmd(h)
                        self.commands.append((space, u))
                        x, yaw, t = x + u["v"] * 0.1, yaw + u["w"] * 0.1, t + 0.1
                        obs(u["v"], u["w"])
        except Exception as e:  # noqa: BLE001 - surfaced by the test through .error
            self.error = e


@pytest.fixture
def backend(monkeypatch):
    import tools.webots_runner as wr
    monkeypatch.setattr(wr, "kill_webots", lambda: None)                      # never touch a real Webots during unit tests
    fake = FakeWebots()
    monkeypatch.setattr(WebotsBackend, "_spawn", lambda self, wbt: fake(wbt))
    be = WebotsBackend(get_vehicle("mid_4wd"), World([], (0, 0), theme="sewer:straight_t"), startup_timeout=10)
    be.fake = fake
    yield be
    be.close()


def test_the_backend_is_registered_and_refuses_what_webots_cannot_simulate():
    assert "webots" in BACKENDS.names() and BACKENDS.get("webots") is WebotsBackend
    with pytest.raises(SpecError, match="cannot simulate"):
        WebotsBackend(get_vehicle("quad_250"), World([], (0, 0)))
    with pytest.raises(SpecError, match="exceeds"):
        WebotsBackend(get_vehicle("mid_4wd", max_speed=3.0), World([], (0, 0)))


def test_one_step_is_one_command_and_one_observation(backend):
    o = backend.reset()
    assert o.state.pos[0] == 0.0 and backend.map_name == "straight_t" and o.sensors["range"].tolist() == [1.0, 3.0, 2.0]
    for _ in range(5):
        o = backend.step([0.4, 0.5])
    assert o.state.pos[0] == pytest.approx(5 * 0.4 * 0.1) and o.state.yaw == pytest.approx(5 * 0.5 * 0.1)
    assert o.state.vel_body[0] == pytest.approx(0.4) and o.state.omega_body[2] == pytest.approx(0.5) and o.state.t == pytest.approx(0.5)
    assert backend.fake.commands[-1] == ("unicycle", {"v": 0.4, "w": 0.5}) and backend.fake.error is None
    img = backend.render("front")
    assert img.shape == (48, 64, 3) and abs(int(img.mean()) - int(40 + 10 * 0.5) % 255) <= 3        # the frame that came with the last observation
    big = backend.step([9.0, 9.0])                                                                  # clipped to the vehicle's limits before it is sent
    assert backend.fake.commands[-1][1]["v"] == get_vehicle("mid_4wd").params["max_speed"] and big.state.t > o.state.t


def test_unsupported_calls_say_what_to_use_instead(backend):
    backend.reset()
    with pytest.raises(NotImplementedError, match="MuJoCo"):
        backend.render("front", segmentation=True)
    with pytest.raises(NotImplementedError, match="Webots has its own"):
        backend.scene()


def test_a_webots_that_never_connects_times_out_with_advice(monkeypatch):
    import tools.webots_runner as wr
    monkeypatch.setattr(wr, "kill_webots", lambda: None)
    monkeypatch.setattr(WebotsBackend, "_spawn", lambda self, wbt: None)
    be = WebotsBackend(get_vehicle("mid_4wd"), World([], (0, 0), theme="sewer:straight_t"), startup_timeout=0.5)
    t0 = time.time()
    with pytest.raises(TimeoutError, match="welcome dialog"):
        be.reset()
    assert time.time() - t0 < 5 and be.server is None


def test_the_sewer_task_works_through_the_generic_sim_loop(backend, monkeypatch):
    sim = Sim.__new__(Sim)                                                       # a Sim whose backend is the (fake-driven) Webots one
    task_spec = get_task_spec("sewer_straight")
    vehicle = get_vehicle("mid_4wd")
    from simlab.registry import TASKS
    sim.vehicle, sim.task = vehicle, TASKS.get("sewer_drive")(task_spec, vehicle)
    sim.backend, sim.space_name, sim.space = backend, vehicle.default_space, vehicle.space()
    sim.rng = np.random.default_rng(0)
    sim.episode = sim.obs = sim.vec = None
    sim.t, sim.steps, sim.ret = 0.0, 0, 0.0
    vec = sim.reset()
    assert vec.shape == (5,) and np.isfinite(vec).all() and sim.task.build_world().theme == "sewer:straight_t"
    for _ in range(3):
        vec, r, term, trunc, info = sim.step([0.5, 0.0])
    assert r == 0.0 and not term and not trunc and sim.t == pytest.approx(0.3)


def test_sewer_task_validation():
    for bad in ({"name": "x", "task": "sewer_drive", "params": {"map": "no_such_map"}},):
        with pytest.raises(SpecError, match="no map file"):
            from simlab.registry import TASKS
            TASKS.get("sewer_drive")(TaskSpec.from_dict(bad), get_vehicle("mid_4wd"))
    with pytest.raises(SpecError, match="ground vehicles"):
        from simlab.registry import TASKS
        TASKS.get("sewer_drive")(get_task_spec("sewer_star"), get_vehicle("quad_250"))


def test_tasks_declare_which_simulator_builds_them():
    from simlab.dashboard.catalog import build_catalog
    t = build_catalog()["tasks"]
    assert t["sewer_star"]["backends"] == ["webots"] and t["goto_open"]["backends"] == ["mujoco"]
