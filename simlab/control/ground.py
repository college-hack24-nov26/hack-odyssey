"""Classical controllers for ground vehicles. They double as the baseline an RL policy is compared against."""
from __future__ import annotations

import math

import numpy as np


class GotoController:
    """Steer at the goal; bend away from anything the front range rays see; slow down when something is close.

    Reactive only (no map), so it can wedge itself in a dead end; that is exactly what a trained policy is
    expected to beat.
    """

    def __init__(self, task):
        v = task.vehicle
        self.task = task
        self.vmax, self.wmax = v.params["max_speed"], v.params["max_yaw_rate"]
        self.r = task.radius
        o = v.sensor("rangefinder", "range").options
        n = o["rays"]
        self.angles = np.linspace(-math.radians(o["fov_deg"]) / 2, math.radians(o["fov_deg"]) / 2, n) if n > 1 else np.zeros(1)
        self.d_safe = self.r + 0.55 + 0.9 * self.vmax
        self.side = 0.0

    def reset(self):
        self.side = 0.0

    def __call__(self, obs, episode, space=None) -> np.ndarray:
        st = obs.state
        dx, dy = episode.info["goal"][0] - st.pos[0], episode.info["goal"][1] - st.pos[1]
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        bearing = math.atan2(-s * dx + c * dy, c * dx + s * dy)
        d = obs.sensors["range"]
        front = float(np.min(d[np.abs(self.angles) < math.radians(35)])) if np.any(np.abs(self.angles) < math.radians(35)) else float(d.min())
        push = np.maximum(0.0, (self.d_safe - d) / self.d_safe)
        rep = float(np.sum(-np.sin(self.angles) * push))          # an obstacle on the left (angle > 0) pushes us right
        if front < 0.7 * self.d_safe and abs(rep) < 0.15:          # dead ahead and symmetric: commit to one side
            self.side = self.side or (1.0 if bearing >= 0 else -1.0)
            rep = self.side * 0.8
        elif front > self.d_safe:
            self.side = 0.0
        blocked = min(1.0, max(0.0, (self.d_safe - front) / self.d_safe))
        w = (1.0 - 0.7 * blocked) * 1.8 * bearing + 2.2 * rep
        v = self.vmax * max(0.0, math.cos(bearing)) * (1.0 - 0.85 * blocked)
        if abs(bearing) > math.radians(75):
            v = 0.0
        return np.array([max(-self.vmax, min(self.vmax, v)), max(-self.wmax, min(self.wmax, w))])
