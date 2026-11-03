"""Classical controller for water_rescue: steer at the person, slow down on approach, stop beside them."""
from __future__ import annotations

import math

import numpy as np


class RescueController:
    """Speed follows the braking the boat can really do (its thrust over its mass), so a heavy boat starts slowing early."""

    def __init__(self, task):
        self.task = task
        pr, b = task.vehicle.params, task.vehicle.body
        self.a_brake = max(0.05, 0.25 * pr["thrust_max"] / b["mass"])       # m/s^2: reverse thrust is weaker than forward, drag helps a little

    def reset(self):
        pass

    def __call__(self, obs, episode, space=None) -> np.ndarray:
        st = obs.state
        gx, gy = episode.info["goal"]
        dx, dy = gx - st.pos[0], gy - st.pos[1]
        dist = math.hypot(dx, dy)
        err = (math.atan2(dy, dx) - st.yaw + math.pi) % (2 * math.pi) - math.pi          # positive: the person is to the left
        reach = self.task.p["reach_radius"]
        rudder = float(np.clip(1.5 * err, -1.0, 1.0))                                     # positive rudder turns left
        v_des = math.sqrt(2.0 * self.a_brake * max(0.0, dist - 0.6 * reach))
        if abs(err) > 1.2:
            v_des = min(v_des, 1.0)                                                       # turn first
        throttle = float(np.clip(1.5 * (v_des - float(st.vel_body[0])), -0.6, 1.0))
        return np.array([throttle, rudder])
