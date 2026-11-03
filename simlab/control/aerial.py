"""Classical controllers for multirotors: the baselines trained policies are compared against."""
from __future__ import annotations

import math

import numpy as np

from ..vehicles.multirotor import Autopilot

G = 9.81


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class WaypointController:
    """Fly at the current waypoint with a braking-distance speed profile; hold the starting heading."""

    def __init__(self, task):
        self.task = task
        self.vmax = 0.8 * task.vmax
        self.a = 0.45 * G * math.tan(task.tilt * 0.8)             # braking deceleration it dares to use
        self.kp = max(0.25, min(1.5, 0.4 * Autopilot(task.vehicle, 0.005).kv))    # stay well inside the velocity loop's bandwidth
        self.yaw_ref = None

    def reset(self):
        self.yaw_ref = None

    def __call__(self, obs, episode, space=None) -> np.ndarray:
        st, info = obs.state, episode.info
        if self.yaw_ref is None:
            self.yaw_ref = st.yaw
        wp = info["wps"][min(info["idx"], len(info["wps"]) - 1)]
        d = wp - st.pos
        if space == "position":
            return np.array([wp[0], wp[1], wp[2], self.yaw_ref])
        dh = math.hypot(d[0], d[1])
        speed = min(self.vmax, math.sqrt(2.0 * self.a * dh), self.kp * dh + 0.05)
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        ux, uy = (d[0] / dh, d[1] / dh) if dh > 1e-6 else (0.0, 0.0)
        vz = max(-0.7 * self.task.vdown, min(0.7 * self.task.vup, 1.2 * d[2]))
        return np.array([speed * (c * ux + s * uy), speed * (-s * ux + c * uy), vz,
                         max(-1.5, min(1.5, 1.5 * _wrap(self.yaw_ref - st.yaw)))])


class LandController:
    """Move over the pad at altitude, then descend at a rate that shrinks near the ground."""

    def __init__(self, task):
        self.task = task

    def reset(self):
        pass

    def __call__(self, obs, episode, space=None) -> np.ndarray:
        st, pad = obs.state, episode.info["pad"]
        d = pad - st.pos
        dh, alt = math.hypot(d[0], d[1]), st.pos[2]
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        speed = min(0.6 * self.task.vmax, 1.2 * dh)
        ux, uy = (d[0] / dh, d[1] / dh) if dh > 1e-6 else (0.0, 0.0)
        aligned = dh < max(0.25, 0.25 * alt)
        vz = -min(0.8 * self.task.p["land_speed"], 0.25 + 0.4 * alt) if aligned else max(-0.3, min(0.3, 0.8 * (1.5 - alt)))
        if space == "position":
            return np.array([pad[0], pad[1], 0.0 if aligned else max(alt, 1.0), st.yaw])
        return np.array([speed * (c * ux + s * uy), speed * (-s * ux + c * uy), vz, 0.0])
