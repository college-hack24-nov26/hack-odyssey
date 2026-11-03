"""Webots behind the same Backend contract as MuJoCo: the high-fidelity, photorealistic-ish sewer simulator.

Webots runs as its own process and talks to this backend over the TCP link of protocol v2 (the same link the Sewer tab
uses). One policy step is one camera frame: the controller sends an observation, waits for the command, and applies
it. Worlds are generated from sewer maps by tools.gen_world, so this backend is tied to ground vehicles and to tasks
whose world theme is "sewer:<map name>" (see simlab.tasks.sewer).

Limits, by design of the Webots side: one simulation at a time, a new run means starting Webots again (reset() takes
tens of seconds), no segmentation render, no 3D scene export (use the Webots window).
"""
from __future__ import annotations

import math
import socket
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from ..protocol import HOST, PORT, Reader, cmd_header, pack
from ..spec.caps import WEBOTS_CAPS
from ..spec.vehicle import VehicleSpec
from ..tasks.base import World
from .base import Backend, Observation, State


RANGE_MAX = 4.0         # m, what the sewer task's observation scales by (see simlab.tasks.sewer)


class WebotsBackend(Backend):
    name = "webots"
    caps = WEBOTS_CAPS

    def __init__(self, vehicle: VehicleSpec, world: World, *, policy_hz: float = 12.0, seed: int = 0, map_name: str | None = None,
                 mode: str = "fast", ambient: float = 0.35, startup_timeout: float = 120.0):
        from sewerbot.core.profiles import profile_from_spec                    # the sewer/Webots view of a ground vehicle
        self.vehicle = vehicle.check(self.caps)
        self.profile = profile_from_spec(self.vehicle)
        self.map_name = map_name or (world.theme.split(":", 1)[1] if world.theme.startswith("sewer:") else "straight_t")
        self.mode, self.ambient, self.startup_timeout, self.seed = mode, ambient, startup_timeout, seed
        self.policy_dt = 1.0 / self.profile.camera.fps
        self.proc: subprocess.Popen | None = None
        self.server: socket.socket | None = None
        self.conn: socket.socket | None = None
        self.reader, self._inbox = Reader(), []
        self.cameras = {s.name: s.options for s in vehicle.sensors if s.type == "camera"}
        self._frame: np.ndarray | None = None
        self._t = 0.0

    # ---------------------------------------------------------------- process and link
    def _spawn(self, wbt: Path) -> subprocess.Popen | None:
        from tools.webots_runner import find_webots
        return subprocess.Popen([str(find_webots()), f"--mode={self.mode}", str(wbt)])

    def _launch(self) -> None:
        from tools.gen_world import ROOT, generate
        from tools.webots_runner import kill_webots
        self.close()
        kill_webots()
        wbt = generate(ROOT / "maps" / f"{self.map_name}.yaml", self.profile, ambient=self.ambient, defect_seed=self.seed)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)    # see SimManager._serve
        try:
            self.server.bind((HOST, PORT))
        except OSError as e:
            self.server.close()
            self.server = None
            raise OSError(f"port {PORT} is in use (another dashboard or Webots run?): {e}") from None
        self.server.listen(1)
        self.server.settimeout(self.startup_timeout)
        self.proc = self._spawn(wbt)
        try:
            self.conn, _ = self.server.accept()
        except socket.timeout:
            self.close()
            raise TimeoutError(f"Webots did not connect within {self.startup_timeout:.0f} s (is its welcome dialog open?)") from None
        self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader, self._inbox = Reader(), []

    def _recv(self, want: str, timeout: float = 30.0) -> tuple[dict, bytes]:
        """The newest message of type `want` (if Webots sent several while we were busy, older ones are stale)."""
        deadline = time.time() + timeout
        while True:
            hits = [i for i, (h, _) in enumerate(self._inbox) if h.get("t") == want]
            if hits:
                msg = self._inbox[hits[-1]]
                del self._inbox[: hits[-1] + 1]
                return msg
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"no {want!r} message from Webots within {timeout:.0f} s")
            self.conn.settimeout(left)
            try:
                data = self.conn.recv(1 << 20)
            except socket.timeout:
                raise TimeoutError(f"no {want!r} message from Webots within {timeout:.0f} s") from None
            if not data:
                raise ConnectionError("Webots closed the connection")
            self._inbox += self.reader.feed(data)

    def _observe(self, header: dict, payload: bytes) -> Observation:
        pose = header["pose"]
        bgr = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        if bgr is not None:
            self._frame = np.ascontiguousarray(bgr[:, :, ::-1])
        self._t = float(header["time"])
        yaw, v, w = float(pose["yaw"]), float(header.get("v", 0.0)), float(header.get("w", 0.0))
        st = State(self._t, np.array([pose["x"], pose["y"], pose["z"]]), np.array([math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]), yaw,
                   np.array([v * math.cos(yaw), v * math.sin(yaw), 0.0]), np.array([v, 0.0, 0.0]), np.array([0.0, 0.0, w]),
                   extra={"ground_truth": header.get("gt", [])})
        ranges = header.get("ranges") or {}
        rays = np.array([np.nan if ranges.get(k) is None else ranges[k] for k in ("left", "front", "right")], float)
        sensors = {"range": np.where(np.isfinite(rays), np.minimum(rays, RANGE_MAX), RANGE_MAX)}      # null / NaN / inf = no echo
        return Observation(st, sensors)

    # ---------------------------------------------------------------- Backend API
    @property
    def time(self) -> float:
        return self._t

    def reset(self, spawn=None, placements=None, randomize=None, rng=None) -> Observation:
        """Starts Webots from scratch: the sewer map decides the start pose, so `spawn` is ignored."""
        self._launch()
        self._recv("hello", self.startup_timeout)
        return self._observe(*self._recv("obs", self.startup_timeout))

    def step(self, command, space: str | None = None) -> Observation:
        space = space or self.vehicle.default_space
        u = self.vehicle.space(space).as_dict(self.vehicle.space(space).vector(command))
        self.conn.sendall(pack(cmd_header(space, u)))
        return self._observe(*self._recv("obs"))

    def render(self, camera: str, segmentation: bool = False) -> np.ndarray:
        if segmentation:
            raise NotImplementedError("the Webots backend does not render segmentation (use MuJoCo for dataset capture)")
        if self._frame is None:
            raise RuntimeError("no camera frame yet: call reset() first")
        return self._frame

    def scene(self) -> dict:
        raise NotImplementedError("Webots has its own 3D window; the dashboard's 3D viewer is for the MuJoCo backend")

    def poses(self) -> np.ndarray:
        raise NotImplementedError("see scene()")

    def close(self) -> None:
        for s in (self.conn, self.server):
            if s is not None:
                try:
                    if s is self.conn:
                        s.sendall(pack({"t": "quit"}))
                    s.close()
                except OSError:
                    pass
        self.conn = self.server = None
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                from tools.webots_runner import kill_webots
                kill_webots()
        self.proc = None
