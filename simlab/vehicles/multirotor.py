"""Multirotors: quad (X or +), hex and octo layouts with any arm length, mass and motor size.

Forces are modelled as thrust along each rotor's axis plus a reaction yaw torque proportional to thrust. The
allocation matrix maps per-rotor thrust to the body wrench [Fz, tau_x, tau_y, tau_z]; the autopilot (M3) inverts it.
"""
from __future__ import annotations

import math

import numpy as np

from ..registry import KINDS
from ..spec.caps import Caps
from ..spec.commands import Channel, CommandSpace
from ..spec.params import Issue, Param
from .base import Fact, VehicleKind, box_inertia

G = 9.81
LAYOUTS = ("quad_x", "quad_plus", "hex", "octo")


def rotor_layout(layout: str, arm: float) -> list[tuple[float, float, int]]:
    """(x, y, yaw_sign) per rotor; x forward, y left. yaw_sign is the sign of the yaw torque the rotor puts on the
    body when it thrusts (neighbouring rotors counter-rotate so the torques cancel in hover)."""
    if layout == "quad_x":
        angles = [45, 135, 225, 315]
    elif layout == "quad_plus":
        angles = [0, 90, 180, 270]
    elif layout == "hex":
        angles = [0, 60, 120, 180, 240, 300]
    elif layout == "octo":
        angles = [22.5 + 45 * i for i in range(8)]
    else:
        raise ValueError(f"unknown multirotor layout {layout!r}; known: {LAYOUTS}")
    return [(arm * math.cos(math.radians(a)), arm * math.sin(math.radians(a)), -1 if i % 2 == 0 else 1)
            for i, a in enumerate(angles)]


def rotors(spec) -> list[tuple[float, float, int]]:
    return rotor_layout(spec.params["layout"], spec.params["arm_length"])


def allocation_matrix(spec) -> np.ndarray:
    """4 x n matrix A with [Fz, tau_x, tau_y, tau_z] = A @ thrusts (a rotor at (x, y) gives tau_x = y*T, tau_y = -x*T)."""
    k = spec.params["torque_coef"]
    r = rotors(spec)
    return np.array([[1.0] * len(r), [y for _, y, _ in r], [-x for x, _, _ in r], [s * k for _, _, s in r]])


def mix(spec, wrench) -> np.ndarray:
    """Per-rotor thrust (N) for a wanted [Fz, tau_x, tau_y, tau_z]; the minimum-norm solution, clipped to the motors."""
    t = np.linalg.pinv(allocation_matrix(spec)) @ np.asarray(wrench, float)
    return np.clip(t, 0.0, spec.params["thrust_max"])


def prop_radius(spec) -> float:
    """Visual propeller radius: just under half the gap between neighbouring rotors."""
    n = len(rotors(spec))
    return 0.45 * 2.0 * spec.params["arm_length"] * math.sin(math.pi / n)


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def euler(q) -> tuple[float, float, float]:
    """(roll, pitch, yaw) of a (w, x, y, z) quaternion, ZYX order."""
    w, x, y, z = q
    return (math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)),
            math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x)))),
            math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))


class Autopilot:
    """Cascaded flight controller: position -> velocity -> attitude -> body rate -> per-rotor thrust.

    Written against plain numbers (pose, velocity, attitude, rates) so any backend can run it. Gains follow from the
    vehicle's motor time constant: the rate loop is as fast as the motors allow, each outer loop ~3x slower.
    When the motors saturate the mixer gives up altitude before attitude, so a hard command cannot flip the vehicle.
    """

    def __init__(self, spec, dt: float):
        p, b = spec.params, spec.body
        self.dt, self.m, self.n = dt, b["mass"], len(rotors(spec))
        self.ixx, self.iyy, self.izz = box_inertia(b["mass"], b["length"], b["width"], b["height"])
        self.pinv_tau = np.linalg.pinv(allocation_matrix(spec)[1:])
        self.tmax = p["thrust_max"]
        self.kr = min(0.9 / p["motor_tau"], 45.0)             # rate loop gain (1/s): ~45 deg of phase margin against the motor lag
        self.katt = self.kr / 3.0
        self.kv = self.katt / 3.0
        self.kp = self.kv / 2.5
        self.kz = self.kr / 3.5                               # altitude only has to beat the motor lag, not the tilt dynamics
        self.kyaw = 0.5 * self.kr
        self.max_tilt = math.radians(p["max_tilt_deg"])
        self.a_max = G * math.tan(self.max_tilt) * 0.9
        self.vmax, self.vup, self.vdown, self.wmax = p["max_speed"], p["max_climb"], p["max_descent"], p["max_yaw_rate"]
        self.max_rate = 8.0
        self.ki = self.kv * self.kv / 3.0
        self.reset()

    def reset(self):
        self.ix = self.iy = self.iz = 0.0

    # ---------------------------------------------------------------- command spaces -> setpoints
    def update(self, space: str, u, pos, vel, quat, omega) -> np.ndarray:
        """Per-rotor thrust (N) for a command `u` in `space`, given the current state."""
        if space == "motors":
            return np.clip(np.asarray(u, float), 0.0, 1.0) * self.tmax
        roll, pitch, yaw = euler(quat)
        if space == "attitude_thrust":
            roll_d, pitch_d, r_d, fz = u[0], u[1], u[2], u[3] * self.n * self.tmax
            return self._rates(roll, pitch, roll_d, pitch_d, r_d, fz, omega)
        c, s = math.cos(yaw), math.sin(yaw)
        if space == "position":
            ex, ey = u[0] - pos[0], u[1] - pos[1]
            vxc, vyc = self.kp * (c * ex + s * ey), self.kp * (-s * ex + c * ey)
            sp = math.hypot(vxc, vyc)
            if sp > self.vmax:
                vxc, vyc = vxc * self.vmax / sp, vyc * self.vmax / sp
            vzc = max(-self.vdown, min(self.vup, 0.5 * self.kz * (u[2] - pos[2])))
            r_d = max(-self.wmax, min(self.wmax, 2.0 * _wrap(u[3] - yaw)))
        else:                                                   # "velocity" (heading-aligned frame)
            vxc, vyc, vzc, r_d = u[0], u[1], u[2], u[3]
        vxh, vyh = c * vel[0] + s * vel[1], -s * vel[0] + c * vel[1]
        ex, ey, ez = vxc - vxh, vyc - vyh, vzc - vel[2]
        lim, dt = 0.3 * self.a_max, self.dt
        self.ix = max(-lim, min(lim, self.ix + self.ki * ex * dt))
        self.iy = max(-lim, min(lim, self.iy + self.ki * ey * dt))
        self.iz = max(-3.0, min(3.0, self.iz + self.kz * self.kz / 3.0 * ez * dt))
        ax = max(-self.a_max, min(self.a_max, self.kv * ex + self.ix))
        ay = max(-self.a_max, min(self.a_max, self.kv * ey + self.iy))
        az = self.kz * ez + self.iz
        pitch_d = math.atan2(ax, G + az)                      # tilt the thrust vector toward the wanted acceleration
        roll_d = -math.atan2(ay, G + az)
        tilt = max(self.max_tilt, 1e-3)
        pitch_d, roll_d = max(-tilt, min(tilt, pitch_d)), max(-tilt, min(tilt, roll_d))
        fz = self.m * (G + az) / max(math.cos(roll) * math.cos(pitch), 0.5)
        return self._rates(roll, pitch, roll_d, pitch_d, r_d, fz, omega)

    def _rates(self, roll, pitch, roll_d, pitch_d, r_d, fz, omega) -> np.ndarray:
        mr = self.max_rate
        p_d = max(-mr, min(mr, self.katt * (roll_d - roll)))
        q_d = max(-mr, min(mr, self.katt * (pitch_d - pitch)))
        tau = np.array([self.ixx * self.kr * (p_d - omega[0]), self.iyy * self.kr * (q_d - omega[1]),
                        self.izz * self.kyaw * (r_d - omega[2])])
        return self.mix(max(fz, 0.0), tau)

    def mix(self, fz: float, tau) -> np.ndarray:
        """Thrusts realising total thrust `fz` and body torques `tau`; torques win over thrust when motors saturate."""
        t_tau = self.pinv_tau @ tau
        lo, hi = float(t_tau.min()), float(t_tau.max())
        if hi - lo > self.tmax:                                  # cannot even realise the torques: scale them down
            t_tau = t_tau * (self.tmax / (hi - lo))
            lo, hi = float(t_tau.min()), float(t_tau.max())
        t_f = max(-lo, min(self.tmax - hi, fz / self.n))        # the common thrust that keeps every motor in range
        return t_f + t_tau


class Multirotor(VehicleKind):
    name = "multirotor"
    title = "Multirotor drone"
    description = "Quad, hex or octo copter; commanded by velocity, position, attitude+thrust or raw motors."
    schema = (
        Param("layout", "enum", "quad_x", doc="Rotor arrangement", choices=LAYOUTS),
        Param("arm_length", "float", 0.125, 0.01, 3.0, "m", "Centre to rotor distance"),
        Param("thrust_max", "float", 4.0, 0.02, 1000.0, "N", "Maximum thrust of one rotor"),
        Param("torque_coef", "float", 0.016, 0.002, 0.2, "m", "Yaw reaction torque per unit thrust"),
        Param("motor_tau", "float", 0.03, 0.002, 1.0, "s", "Motor + propeller spin-up time constant"),
        Param("drag", "float", 0.05, 0.0, 10.0, "N/(m/s)", "Linear body drag coefficient"),
        Param("max_tilt_deg", "float", 35.0, 5.0, 75.0, "deg", "Largest roll/pitch the autopilot commands"),
        Param("max_speed", "float", 10.0, 0.2, 100.0, "m/s", "Top horizontal speed"),
        Param("max_climb", "float", 3.0, 0.1, 30.0, "m/s"),
        Param("max_descent", "float", 2.0, 0.1, 30.0, "m/s"),
        Param("max_yaw_rate", "float", 3.0, 0.1, 15.0, "rad/s"),
        Param("light_intensity", "float", 0.0, 0.0, 50.0, "", "Lamp strength (0 = no lamp)"),
    )
    body_defaults = {"mass": 0.65, "length": 0.25, "width": 0.25, "height": 0.08}
    default_space = "velocity"

    def spaces(self, spec):
        p = spec.params
        s, c, d, y = p["max_speed"], p["max_climb"], p["max_descent"], p["max_yaw_rate"]
        t = math.radians(p["max_tilt_deg"])
        return {
            "velocity": CommandSpace("velocity", (Channel("vx", -s, s, "m/s"), Channel("vy", -s, s, "m/s"),
                                                  Channel("vz", -d, c, "m/s"), Channel("yaw_rate", -y, y, "rad/s")),
                                     "Velocity in the heading-aligned frame (x forward, y left, z up); the autopilot holds attitude."),
            "position": CommandSpace("position", (Channel("x", -1e4, 1e4, "m"), Channel("y", -1e4, 1e4, "m"),
                                                  Channel("z", 0.0, 1e3, "m"), Channel("yaw", -math.pi, math.pi, "rad")),
                                     "World-frame position and heading setpoint."),
            "attitude_thrust": CommandSpace("attitude_thrust", (Channel("roll", -t, t, "rad"), Channel("pitch", -t, t, "rad"),
                                                                Channel("yaw_rate", -y, y, "rad/s"), Channel("thrust", 0.0, 1.0)),
                                            "Roll, pitch, yaw rate and collective thrust fraction."),
            "motors": CommandSpace("motors", tuple(Channel(f"m{i}", 0.0, 1.0) for i in range(len(rotors(spec)))),
                                   "Throttle 0..1 per rotor, no autopilot."),
        }

    def validate(self, spec, caps: Caps | None = None):
        issues = super().validate(spec, caps)
        p, b = spec.params, spec.body
        rank = int(np.linalg.matrix_rank(allocation_matrix(spec)))
        if rank < 4:
            issues.append(Issue("error", "params.layout", f"this rotor layout cannot control roll, pitch, yaw and thrust "
                                                           f"independently (rank {rank} of 4)"))
        twr = self._twr(spec)
        if twr < 1.15:
            issues.append(Issue("error", "params.thrust_max", f"cannot hover with margin: thrust-to-weight is {twr:.2f}, needs >= 1.15"))
        elif twr < 1.6:
            issues.append(Issue("warn", "params.thrust_max", f"thrust-to-weight {twr:.2f} is low: sluggish, little reserve for gusts or battery sag"))
        if twr > 1.0:
            max_hover_tilt = math.degrees(math.acos(1.0 / twr))
            if p["max_tilt_deg"] > max_hover_tilt:
                issues.append(Issue("warn", "params.max_tilt_deg", f"at {p['max_tilt_deg']:g} deg the vehicle cannot hold altitude "
                                                                   f"(limit {max_hover_tilt:.0f} deg at this thrust)"))
        if p["arm_length"] < 0.5 * min(b["length"], b["width"]):
            issues.append(Issue("warn", "params.arm_length", "rotors sit inside the body envelope"))
        return issues

    @staticmethod
    def _twr(spec) -> float:
        return len(rotors(spec)) * spec.params["thrust_max"] / (spec.body["mass"] * G)

    def derived(self, spec):
        p, b = spec.params, spec.body
        n = len(rotors(spec))
        twr = self._twr(spec)
        hover_t = b["mass"] * G / n
        ixx = box_inertia(b["mass"], b["length"], b["width"], b["height"])[0]
        reserve = min(hover_t, p["thrust_max"] - hover_t)
        tau_roll = sum(abs(y) for _, y, _ in rotors(spec)) * max(reserve, 0.0)
        tilt = min(math.radians(p["max_tilt_deg"]), math.acos(1.0 / twr) if twr > 1 else 0.0)
        return [
            Fact("twr", "Thrust-to-weight", twr, ""),
            Fact("hover_throttle", "Hover thrust (of max)", 100.0 * hover_t / p["thrust_max"], "%"),
            Fact("hover_thrust", "Thrust per rotor in hover", hover_t, "N"),
            Fact("climb_accel", "Peak climb acceleration", G * (twr - 1.0), "m/s^2"),
            Fact("horizontal_accel", "Peak horizontal acceleration", G * math.tan(tilt), "m/s^2"),
            Fact("roll_accel", "Peak roll acceleration", tau_roll / ixx, "rad/s^2"),
            Fact("rotors", "Rotors", n, ""),
        ]


KINDS.add(Multirotor())
