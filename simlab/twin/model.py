"""The twin's motion model: a differential drive with a strength per side, the same law the simulator's ground plant uses.

    wheel speed   v_side = duty x mps_per_duty x strength_side x (trim_side)        (0 below the motor's start duty)
    turn rate     w = yaw_gain x (v_right - v_left) / track                          (positive = turning left)

(`simlab/backends/mujoco/plants.py` GroundPlant.apply: `wt = yaw_gain * (right - left) / track` after `side_gain` is applied.)
It is analytic on purpose: the reality-gap tests need thousands of what-ifs in milliseconds, and a test compares it with the real plant.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class TwinModel:
    track_m: float = 0.12
    mps_per_duty: float = 0.2 / 180.0
    yaw_gain: float = 1.0
    side_gain: tuple[float, float] = (1.0, 1.0)         # (left, right) strength: 1.0 = as designed, 0.88 = 12 % weak
    start_duty: tuple[float, float] = (0.0, 0.0)        # duty each motor needs before it turns
    trim: tuple[float, float] = (1.0, 1.0)              # firmware duty multipliers
    notes: list[str] = field(default_factory=list)

    def wheel_speeds(self, left_duty: float, right_duty: float) -> tuple[float, float]:
        out = []
        for duty, trim, g, start in zip((left_duty, right_duty), self.trim, self.side_gain, self.start_duty):
            d = max(-255.0, min(255.0, duty * trim))
            out.append(0.0 if abs(d) < start else d * self.mps_per_duty * g)
        return out[0], out[1]

    def turn_rate_dps(self, left_duty: float, right_duty: float) -> float:
        vl, vr = self.wheel_speeds(left_duty, right_duty)
        return math.degrees(self.yaw_gain * (vr - vl) / self.track_m)

    def predict_move_yaw_deg(self, direction: str, duty: float, secs: float) -> float:
        l, r = {"F": (duty, duty), "B": (-duty, -duty), "L": (-duty, duty), "R": (duty, -duty)}[direction]
        return self.turn_rate_dps(l, r) * secs

    def drift_cm(self, duty: float, distance_m: float) -> float:
        """Sideways miss after driving `distance_m` straight at `duty` (positive = to the left)."""
        vl, vr = self.wheel_speeds(duty, duty)
        v = 0.5 * (vl + vr)
        if v <= 0:
            return 0.0
        w = math.radians(self.turn_rate_dps(duty, duty))
        t = distance_m / v
        return 100.0 * 0.5 * w * v * t * t               # small-angle: y = v w t^2 / 2
