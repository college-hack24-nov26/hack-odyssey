"""Simulated sensors for the MuJoCo backend. Cameras are rendered on demand by the backend; the rest are sampled once
per policy step from ground truth plus noise (noise scaled by the episode's `sensor_noise` randomisation factor)."""
from __future__ import annotations

import math
from collections import deque

import mujoco
import numpy as np

from ..base import State


class PoseSensor:
    """[x, y, z, yaw] with optional noise and latency (a GPS/mocap/odometry stand-in)."""

    def __init__(self, opts: dict, rng: np.random.Generator):
        self.o, self.rng = opts, rng
        self.buf: deque = deque()

    def reset(self):
        self.buf.clear()

    def read(self, st: State, scale: float) -> np.ndarray:
        o = self.o
        z = np.array([*st.pos, st.yaw])
        z[:3] += self.rng.normal(0, o["pos_noise"] * scale, 3)
        z[3] += self.rng.normal(0, o["yaw_noise"] * scale)
        self.buf.append((st.t, z))
        out = self.buf[0][1]
        while len(self.buf) > 1 and st.t - self.buf[1][0] >= o["delay_s"]:
            self.buf.popleft()
            out = self.buf[0][1]
        return out.copy()


class ImuSensor:
    """Gyro (rad/s) and specific force (m/s^2) in the body frame."""

    def __init__(self, opts: dict, rng: np.random.Generator):
        self.o, self.rng = opts, rng

    def read(self, st: State, accel_body: np.ndarray, scale: float) -> dict:
        o = self.o
        return {"gyro": st.omega_body + self.rng.normal(0, o["gyro_noise"] * scale, 3),
                "accel": accel_body + self.rng.normal(0, o["accel_noise"] * scale, 3)}


class BatterySensor:
    """Pack voltage, state of charge (0..1) and electrical power draw; vehicles without a power model read full."""

    def read(self, st: State) -> dict:
        e = st.extra
        return {"voltage": float(e.get("voltage", 0.0)), "soc": float(e.get("soc", 1.0)), "power_w": float(e.get("power_w", 0.0))}


class RangeSensor:
    """Distance rays from the vehicle, cast with mj_multiRay; max_range where nothing is hit."""

    def __init__(self, opts: dict, rng: np.random.Generator, model, body_id: int, half_length: float):
        self.o, self.rng, self.model, self.body_id = opts, rng, model, body_id
        n, fov = opts["rays"], math.radians(opts["fov_deg"])
        mode = opts["direction"]
        if mode == "down":
            self.local = np.array([[0.0, 0.0, -1.0]])
            self.origin = np.zeros(3)
        else:
            span = 2 * math.pi if mode == "ring" else fov
            angles = (np.arange(n) * span / n) if mode == "ring" else (
                np.linspace(-span / 2, span / 2, n) if n > 1 else np.zeros(1))
            self.local = np.stack([np.cos(angles), np.sin(angles), np.zeros_like(angles)], 1)
            self.origin = np.array([half_length, 0.0, 0.0]) if mode == "forward" else np.zeros(3)
        self.n = len(self.local)
        self.geomid = np.zeros(self.n, np.int32)
        self.dist = np.zeros(self.n)
        self.normal = np.zeros(self.n * 3)
        self.mask = np.array([1, 1, 1, 0, 1, 1], np.uint8)       # skip group 3: wheels and other decoration

    @property
    def angles(self) -> np.ndarray:
        return np.arctan2(self.local[:, 1], self.local[:, 0])

    def read(self, data, scale: float) -> np.ndarray:
        R = data.xmat[self.body_id].reshape(3, 3)
        pnt = data.xpos[self.body_id] + R @ self.origin
        vec = (self.local @ R.T).reshape(-1)
        mujoco.mj_multiRay(self.model, data, pnt, vec, self.mask, 1, self.body_id, self.geomid, self.dist, self.normal,
                           self.n, self.o["max_range"])
        d = np.where(self.dist < 0, self.o["max_range"], self.dist)
        if self.o["noise"] > 0:
            d = d + self.rng.normal(0, self.o["noise"] * scale, self.n)
        return np.clip(d, 0.0, self.o["max_range"])
