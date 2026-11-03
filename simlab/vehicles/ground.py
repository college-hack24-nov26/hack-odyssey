"""Wheeled / tracked ground vehicles: 2WD differential, 4WD and 6WD skid-steer, tracked crawlers."""
from __future__ import annotations

import math

from ..registry import KINDS
from ..spec.caps import Caps
from ..spec.commands import Channel, CommandSpace
from ..spec.params import Issue, Param
from .base import Fact, VehicleKind

DRIVES = ("diff2wd", "skid4wd", "skid6wd", "tracked")


def wheel_width(radius: float) -> float:
    return min(0.06, max(0.015, 0.5 * radius))


def track_width(width: float, wheel_radius: float) -> float:
    """Distance between left and right wheel/track centres."""
    return width - wheel_width(wheel_radius)


def wheel_xs(drive: str, length: float) -> list[float]:
    """Longitudinal position of each wheel pair, front to back (robot frame, x forward)."""
    if drive == "diff2wd":
        return [0.0]
    if drive == "skid4wd":
        return [0.30 * length, -0.30 * length]
    if drive == "skid6wd":
        return [0.35 * length, 0.0, -0.35 * length]
    return [0.375 * length, 0.125 * length, -0.125 * length, -0.375 * length]   # tracked: four road wheels per side


def unicycle_to_sides(v: float, w: float, *, max_speed: float, max_yaw_rate: float, track: float,
                      yaw_gain: float) -> tuple[float, float]:
    """(v, omega) -> (left, right) ground speeds in m/s, clamped to the vehicle limits.

    Skid-steer slips, so the real yaw rate is `yaw_gain` times the ideal one; we ask for 1/yaw_gain more
    differential to get the turn we want.
    """
    v = max(-max_speed, min(max_speed, v))
    w = max(-max_yaw_rate, min(max_yaw_rate, w))
    half = track / 2.0
    w_wheels = w / max(yaw_gain, 0.05)
    left, right = v - w_wheels * half, v + w_wheels * half
    peak = max(abs(left), abs(right))
    if peak > max_speed:
        k = max_speed / peak
        left, right = left * k, right * k
    return left, right


class GroundWheeled(VehicleKind):
    name = "ground_wheeled"
    title = "Ground vehicle (wheels or tracks)"
    description = "Rovers, 4WD and 6WD skid-steer chassis, tracked crawlers; commanded as (v, omega) or per-side speed."
    schema = (
        Param("drive", "enum", "skid4wd", doc="Drive layout", choices=DRIVES),
        Param("wheel_radius", "float", 0.05, 0.003, 2.0, "m"),
        Param("max_speed", "float", 0.5, 0.01, 40.0, "m/s", "Top forward speed"),
        Param("max_accel", "float", 0.6, 0.01, 50.0, "m/s^2", "Acceleration limit"),
        Param("max_yaw_rate", "float", 1.5, 0.05, 20.0, "rad/s", "Top body rotation rate"),
        Param("yaw_gain", "float", 1.0, 0.05, 1.5, "", "Measured actual/ideal yaw rate; skid-steer slips (< 1)"),
        Param("light_intensity", "float", 1.2, 0.0, 50.0, "", "Headlamp strength (0 = no lamp)"),
    )
    body_defaults = {"mass": 3.5, "length": 0.35, "width": 0.26, "height": 0.12}
    default_space = "unicycle"

    def spaces(self, spec):
        p = spec.params
        s, yr = p["max_speed"], p["max_yaw_rate"]
        return {
            "unicycle": CommandSpace("unicycle", (Channel("v", -s, s, "m/s"), Channel("w", -yr, yr, "rad/s")),
                                     "Forward speed and turn rate; the mixer splits it across the sides."),
            "wheels": CommandSpace("wheels", (Channel("left", -s, s, "m/s"), Channel("right", -s, s, "m/s")),
                                   "Left and right ground speed, no mixing."),
        }

    def mix(self, spec, v: float, w: float) -> tuple[float, float]:
        p = spec.params
        return unicycle_to_sides(v, w, max_speed=p["max_speed"], max_yaw_rate=p["max_yaw_rate"],
                                 track=track_width(spec.body["width"], p["wheel_radius"]), yaw_gain=p["yaw_gain"])

    def validate(self, spec, caps: Caps | None = None):
        issues = super().validate(spec, caps)
        p, b = spec.params, spec.body
        if p["wheel_radius"] * 2 > b["height"] + 0.02:
            issues.append(Issue("error", "params.wheel_radius", "wheels taller than the body: reduce wheel_radius or raise height"))
        if p["wheel_radius"] * 2 > b["width"]:
            issues.append(Issue("error", "params.wheel_radius", "wheels wider apart than the body is wide"))
        if caps is not None and p["max_speed"] > caps.max_ground_speed:
            issues.append(Issue("error", "params.max_speed",
                                f"{p['max_speed']:g} m/s exceeds the {caps.max_ground_speed:g} m/s limit of backend {caps.name!r}"))
        if p["max_speed"] / max(p["max_accel"], 1e-6) > 15.0:
            issues.append(Issue("warn", "params.max_accel", "takes over 15 s to reach top speed"))
        return issues

    def derived(self, spec):
        p = spec.params
        r = p["wheel_radius"]
        return [
            Fact("top_speed", "Top speed", p["max_speed"], "m/s"),
            Fact("time_to_top_speed", "Time to top speed", p["max_speed"] / p["max_accel"], "s"),
            Fact("spin_time", "Time for a full in-place turn", 2 * math.pi / p["max_yaw_rate"], "s"),
            Fact("wheel_rpm", "Wheel speed at top speed", p["max_speed"] / (2 * math.pi * r) * 60.0, "rpm"),
            Fact("track_width", "Wheel track", track_width(spec.body["width"], r), "m"),
        ]


KINDS.add(GroundWheeled())
