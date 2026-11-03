"""Ground vehicles from parts: a DC-motor + sagging-battery model that predicts what the simulator's GroundPlant needs.

Everything is closed form from the part datasheets (see docs/BUILDER.md). The model, in one paragraph:

* A brushed DC motor with a linear torque-speed line fixed by four datasheet numbers: at terminal voltage V the speed under
  torque t is  w = w_nl (V/V_r - t/t_stall),  and the current is  I = I_nl + k t  with  k = (I_stall - I_nl)/t_stall.
* The terminal voltage is what is left after the battery's internal resistance, the driver's drop and its on-resistance:
  V = A - I B  with  A = V_oc - drop - I_elec R_pack  and  B = n R_pack + r_on.  Substituting gives the torque available at
  any speed in closed form, so top speed, start torque, spin speed and the stall current need no iteration.
* Only the small air-drag term makes top speed implicit; it is iterated a few times.

The estimates are not measurements: datasheet numbers are optimistic and units vary by tens of percent. `calibrate` ties
the result to a vehicle you have actually measured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ...spec.params import Issue
from ...spec.vehicle import VehicleSpec
from ...vehicles.ground import GroundWheeled, track_width
from ..build import Build, Resolved
from ..schema import Part
from .common import Derived, G, Pack, PFact, battery_current, pack_from, rail_of

RHO_AIR = 1.2
D_TAU = 0.85                 # shaft torque that actually reaches the wheel / datasheet torque (gearbox losses, optimistic datasheets)
MU_TURN = 0.7                # lateral friction used for the skid-steer turning moment
CASTER_TURN_FACTOR = 0.25    # a two-wheel rover turns on its wheels; only the caster scrubs
WIRING = 0.10                # wiring / solder / fasteners as a fraction of the electronics' mass
CASTER_MASS = 0.010
CRUISE_FRACTION = 0.7
CAPACITY_DERATE = 0.8
YAW_GAIN_PRIOR = {"diff2wd": 0.87, "skid4wd": 0.67, "skid6wd": 0.66, "tracked": 0.67}    # measured in Webots (simulation), not on hardware
DRIVEN_FRACTION = {"diff2wd": 0.7, "skid4wd": 1.0, "skid6wd": 1.0, "tracked": 1.0}
WHEELS = {"diff2wd": 2, "skid4wd": 4, "skid6wd": 6, "tracked": 2}
START_MARGIN_MIN = 1.5       # start force / rolling resistance below this: the rover cannot reliably start
TURN_MARGIN_MIN = 1.3
CHEMISTRY_FOR_SENSOR = {"nimh": "nimh", "lipo": "lipo", "liion": "liion", "lifepo4": "liion", "lead": "liion"}
_SCHEMA = {p.name: p for p in GroundWheeled.schema}


# ---------------------------------------------------------------- the motor on a sagging supply
@dataclass
class MotorModel:
    v_r: float
    w_nl: float            # rad/s
    tau_s: float           # N*m
    i_s: float
    i_nl: float

    @classmethod
    def from_part(cls, part: Part) -> "MotorModel":
        p = part.params
        return cls(p["voltage_rated"], p["no_load_rpm"] * 2 * math.pi / 60.0, p["stall_torque_nm"], p["stall_current_a"], p["no_load_current_a"])

    @property
    def k(self) -> float:
        return (self.i_s - self.i_nl) / self.tau_s

    def v_required(self, w: float, tau: float) -> float:
        """Terminal voltage that makes the motor turn at w under torque tau."""
        return self.v_r * (w / self.w_nl + tau / self.tau_s)

    def current_at(self, tau: float) -> float:
        return self.i_nl + self.k * tau


@dataclass
class Supply:
    """What the motors are fed from: battery open-circuit voltage, its resistance, the driver's losses, `n` motors in all."""
    v_oc: float
    r_pack: float
    drop_v: float
    r_on: float
    i_elec: float
    n: int

    @property
    def _a(self) -> float:
        return self.v_oc - self.drop_v - self.i_elec * self.r_pack

    @property
    def _b(self) -> float:
        return self.n * self.r_pack + self.r_on

    def torque_at(self, m: MotorModel, w: float) -> float:
        """Torque one motor can deliver at speed w, with the supply sagging under the current of all `n` motors."""
        num = (self._a - m.i_nl * self._b) / m.v_r - w / m.w_nl
        den = m.k * self._b / m.v_r + 1.0 / m.tau_s
        return max(0.0, num / den)

    def speed_at(self, m: MotorModel, tau: float) -> float:
        """Speed of one motor delivering torque tau (0 if it cannot)."""
        v_m = self._a - m.current_at(tau) * self._b
        return max(0.0, m.w_nl * (v_m / m.v_r - tau / m.tau_s))

    def stall_current(self, m: MotorModel) -> float:
        """Current in each motor when all are stalled."""
        return m.i_s * (self.v_oc - self.drop_v) / (m.v_r + m.i_s * self._b)


def top_speed(m: MotorModel, s: Supply, r: float, mass: float, crr: float, cd_a: float, d_tau: float = D_TAU) -> tuple[float, float]:
    """(speed m/s, torque per motor at that speed). Drag makes it implicit; four passes are plenty."""
    v, tau = 0.0, 0.0
    for _ in range(4):
        force = crr * mass * G + 0.5 * RHO_AIR * cd_a * v * v
        tau = force * r / (s.n * d_tau)
        v = s.speed_at(m, tau) * r
    return v, tau


# ---------------------------------------------------------------- the build
def _clamp(name: str, value: float, issues: list[Issue], what: str) -> float:
    p = _SCHEMA[name]
    lo, hi = p.min, p.max
    if value < lo or value > hi:
        issues.append(Issue("warn", f"params.{name}", f"{what} ({value:.3g} {p.unit}) is outside the simulator's range {lo:g}..{hi:g}; clamped"))
    return min(max(value, lo), hi)


def _mass_items(b: Build, r: Resolved, pack: Pack) -> list[tuple[str, int, float]]:
    c, n = b.chassis, b.drive["motor_count"]
    nwheel = WHEELS[c["drive"]]
    items = [("frame", 1, c["frame_mass_kg"]), (r.wheel.name, nwheel, nwheel * r.wheel.mass_kg), (r.motor.name, n, n * r.motor.mass_kg),
             (r.driver.name, 1, r.driver.mass_kg), (r.battery.name, pack.series * pack.parallel, pack.mass_kg), (r.mcu.name, 1, r.mcu.mass_kg)]
    if c["drive"] == "diff2wd" and c["caster_count"]:
        items.append(("caster", c["caster_count"], c["caster_count"] * CASTER_MASS))
    if r.regulator:
        items.append((r.regulator.name, 1, r.regulator.mass_kg))
    for use, part in r.sensors:
        items.append((use.name, 1, part.mass_kg))
    if r.lamp:
        items.append((r.lamp.name, r.lamp_count, r.lamp_count * r.lamp.mass_kg))
    for e in b.extras:
        items.append((e["name"], 1, e["mass_kg"]))
    electronics = r.driver.mass_kg + r.mcu.mass_kg + sum(p.mass_kg for _, p in r.sensors) + (r.regulator.mass_kg if r.regulator else 0.0)
    items.append(("wiring allowance", 1, WIRING * electronics))
    return items


def _sensor_specs(b: Build, r: Resolved, pack: Pack, assumptions: list[str]) -> list[dict]:
    out = []
    for use, part in r.sensors:
        p, m = part.params, use.mount
        if part.kind == "sensor_camera":
            out.append({"type": "camera", "name": use.name, "forward": m["forward"], "height": m["height"], "tilt_deg": m["tilt_deg"],
                        "fov_deg": p["fov_deg"], "width_px": p["width_px"], "height_px": p["height_px"], "fps": p["fps_max"], "noise": p["noise"]})
        elif part.kind == "sensor_range":
            out.append({"type": "rangefinder", "name": use.name, "rays": p["rays"], "fov_deg": p["fov_deg"], "max_range": p["max_range_m"],
                        "direction": "forward", "noise": p["noise_m"], "rate_hz": p["rate_hz"]})
        elif part.kind == "sensor_imu":
            out.append({"type": "imu", "name": use.name, "rate_hz": p["rate_hz"], "gyro_noise": p["gyro_noise"], "accel_noise": p["accel_noise"]})
        elif part.kind == "sensor_gps":
            out.append({"type": "pose", "name": use.name, "rate_hz": p["rate_hz"], "pos_noise": p["pos_noise_m"], "yaw_noise": 0.0, "delay_s": 0.0})
    chem = CHEMISTRY_FOR_SENSOR[pack.chemistry]
    if chem != pack.chemistry:
        assumptions.append(f"battery chemistry {pack.chemistry} is simulated as {chem} (the simulator has nimh, lipo and liion)")
    out.append({"type": "battery", "name": "battery", "chemistry": chem, "cells": min(pack.series, 14), "capacity_mah": pack.q_mah})
    return out


def build_to_vehicle(b: Build) -> tuple[VehicleSpec | None, Derived]:
    r = b.resolve()
    c, d, cal = b.chassis, b.drive, b.calibration
    issues: list[Issue] = []
    assumptions = [f"torque derate {D_TAU} (datasheet torque is optimistic, gearboxes lose some)",
                   f"rolling resistance and wheel grip come from the wheel part ({r.wheel.name}: {r.wheel.confidence})",
                   f"wiring and fasteners add {WIRING:.0%} of the electronics' mass"]
    n = d["motor_count"]
    pack = pack_from(r.battery, b.power["series"], b.power["parallel"])
    motor = MotorModel.from_part(r.motor)
    wr, wheel = r.wheel.params["radius_m"], r.wheel.params
    items = _mass_items(b, r, pack)
    mass_est = sum(kg for _, _, kg in items)
    mass = cal.get("mass_kg", mass_est)
    if "mass_kg" in cal:
        assumptions.append(f"mass {mass:g} kg is your measurement (the parts add up to {mass_est:.3f} kg)")
    body_w, body_h = c["width"], c["height"]
    cd_a = 1.0 * body_w * body_h * 0.8
    track = track_width(body_w, wr)

    # electronics budget
    loads = [("controller", r.mcu.params["current_a"])] + [(u.name, p.params["current_a"]) for u, p in r.sensors]
    if r.lamp:
        loads.append(("lamp", r.lamp.params["current_a"] * r.lamp_count))
    loads += [(e["name"], e["current_a"]) for e in b.extras if e["current_a"]]
    i_rail = sum(i for _, i in loads)
    rail = rail_of(pack, r.regulator)
    i_elec = battery_current(i_rail, rail.v_nom, pack.v_nom, r.regulator)
    drv = r.driver.params

    def supply(v_oc: float) -> Supply:
        return Supply(v_oc, pack.r_pack, drv["drop_v"], drv["r_on_ohm"], battery_current(i_rail, rail.v_nom, v_oc, r.regulator), n)

    # top speed over the charge range
    speeds = {}
    for state, v_oc in (("full", pack.v_full), ("nom", pack.v_nom), ("cut", pack.v_cut)):
        speeds[state], _ = top_speed(motor, supply(v_oc), wr, mass, wheel["rolling_resistance"], cd_a)
    sup = supply(pack.v_nom)
    v_free, tau_top = top_speed(motor, sup, wr, mass, wheel["rolling_resistance"], cd_a)
    scale = cal.get("speed_scale", 1.0)
    v_top = v_free * scale
    limit = d["speed_limit_mps"]
    cap = limit or float("inf")
    v_cmd = min(v_top, cap)
    f_roll = wheel["rolling_resistance"] * mass * G

    # acceleration and climbing
    layout = c["drive"]
    mu, frac = wheel["friction"], DRIVEN_FRACTION[layout]
    tau0 = sup.torque_at(motor, 0.0)
    f_start = n * D_TAU * tau0 / wr
    f_trac = mu * mass * G * frac
    a_traction = mu * G * frac
    a_mean = max(0.0, min((f_start - f_roll) / (2 * mass), a_traction)) * cal.get("accel_scale", 1.0)
    climb = math.degrees(math.asin(max(-1.0, min(1.0, (min(f_start, f_trac) - f_roll) / (mass * G)))))
    i_start = sup.stall_current(motor)
    i_start_full = supply(pack.v_full).stall_current(motor)

    # turning in place
    turn_k = CASTER_TURN_FACTOR if layout == "diff2wd" else 1.0
    m_resist = MU_TURN * mass * G * c["length"] / 4.0 * turn_k
    tau_turn = 2 * m_resist * wr / (n * D_TAU * track)
    turn_margin = tau0 / max(tau_turn, 1e-9)
    w_spin = sup.speed_at(motor, tau_turn)
    v_side = w_spin * wr * scale
    if limit:
        v_side = min(v_side, limit)
    yaw_gain = cal.get("yaw_gain", YAW_GAIN_PRIOR[layout])
    if "yaw_gain" not in cal:
        assumptions.append(f"turn-rate gain {yaw_gain} for {layout}: measured in the Webots simulation, not on hardware")
    yaw_rate = yaw_gain * 2 * v_side / track

    # runtime
    v_c = CRUISE_FRACTION * v_cmd
    tau_c = (f_roll + 0.5 * RHO_AIR * cd_a * v_c ** 2) * wr / (n * D_TAU)
    i_m = motor.current_at(tau_c)
    v_req = motor.v_required(v_c / wr / max(scale, 1e-9), tau_c)
    duty = min(1.0, v_req / pack.v_nom)
    i_cruise = n * i_m * duty + i_elec
    derate = cal.get("battery_derate", CAPACITY_DERATE)
    if "battery_derate" not in cal:
        assumptions.append(f"{derate:.0%} of the rated capacity is usable")
    runtime_h = derate * pack.q_mah / 1000.0 / max(i_cruise, 1e-6)
    i_stall_batt = n * i_start_full + battery_current(i_rail, rail.v_nom, pack.v_full, r.regulator)

    # spec numbers, clamped into the simulator's ranges
    params = {"drive": layout, "wheel_radius": wr,
              "max_speed": _clamp("max_speed", v_cmd, issues, "top speed"), "max_accel": _clamp("max_accel", a_mean, issues, "acceleration"),
              "max_yaw_rate": _clamp("max_yaw_rate", yaw_rate, issues, "turn rate"), "yaw_gain": _clamp("yaw_gain", yaw_gain, issues, "yaw gain"),
              "light_intensity": min(50.0, r.lamp.params["light_intensity"] * r.lamp_count) if r.lamp else 0.0}
    body = {"mass": min(max(mass, 0.001), 5000.0), "length": c["length"], "width": c["width"], "height": c["height"]}
    sensors = _sensor_specs(b, r, pack, assumptions)
    names = ", ".join(f"{k}x {v}" for k, v in ((r.motor.name, n), (r.wheel.name, WHEELS[layout]), (r.battery.name, pack.series * pack.parallel)))
    desc = (b.description + "  " if b.description else "") + f"Built from parts: {names}, {r.driver.name}, {r.mcu.name}."

    facts = [
        PFact("top_speed", "Top speed", v_cmd, "m/s", min(speeds["cut"] * scale, cap), min(speeds["full"] * scale, cap),
              "capped by the firmware speed limit" if limit and v_top > limit else "spent .. fresh battery"),
        PFact("accel", "Acceleration", a_mean, "m/s^2", note="average from standstill to top speed" + ("; limited by tyre grip" if (f_start - f_roll) / (2 * mass) > a_traction else "")),
        PFact("time_to_top", "Time to top speed", v_cmd / max(a_mean, 1e-9), "s"),
        PFact("turn_rate", "Turn rate in place", yaw_rate, "rad/s", note=("cannot turn in place" if turn_margin < TURN_MARGIN_MIN else "")),
        PFact("climb", "Steepest slope", climb, "deg", note="limited by motor torque or tyre grip, whichever is lower"),
        PFact("mass", "Mass", mass, "kg", note="measured" if "mass_kg" in cal else "sum of the parts"),
        PFact("runtime", "Runtime at cruise", runtime_h * 60.0, "min", note=f"cruise = {CRUISE_FRACTION:.0%} of top speed"),
        PFact("cruise_current", "Battery current at cruise", i_cruise, "A"),
        PFact("stall_current", "Current per motor when stalled", i_start, "A", hi=i_start_full, note="fresh battery is the high end"),
        PFact("wheel_rpm", "Wheel speed at top speed", v_cmd / (2 * math.pi * wr) * 60.0, "rpm"),
        PFact("pack_voltage", "Battery voltage", pack.v_nom, "V", pack.v_cut, pack.v_full, f"{pack.series} in series"),
    ]
    budget = {
        "mass": [{"part": k, "qty": q, "kg": round(kg, 4)} for k, q, kg in items], "mass_total": mass_est,
        "current": {"loads": [{"name": k, "a": i} for k, i in loads], "rail_a": i_rail, "battery_elec_a": i_elec, "cruise_a": i_cruise,
                    "stall_a": i_stall_batt, "pack_max_a": pack.max_a, "per_motor_stall_a": i_start_full},
        "voltage": {"pack_full": pack.v_full, "pack_nom": pack.v_nom, "pack_cut": pack.v_cut, "rail_min": rail.v_min, "rail_max": rail.v_max,
                    "rail_via": rail.via},
        "chain": f"{pack.series}x {r.battery.name} ({pack.v_cut:.1f}-{pack.v_full:.1f} V)"
                 + (f" -> {r.regulator.name} ({rail.v_min:.1f}-{rail.v_max:.1f} V rail)" if r.regulator else " -> controller directly")
                 + f"; motors: pack -> {r.driver.name} -> {n}x {r.motor.name}",
    }
    numbers = {"n_motors": n, "per_channel_motors": n / 2, "v_pack": {"full": pack.v_full, "nom": pack.v_nom, "cut": pack.v_cut}, "r_pack": pack.r_pack,
               "i_rail": i_rail, "rail": rail, "i_start_nom": i_start, "i_start_full": i_start_full, "i_stall_batt": i_stall_batt,
               "i_cruise": i_cruise, "v_top": v_top, "v_top_free": v_free, "speeds": speeds, "accel": a_mean, "f_start": f_start, "f_roll": f_roll,
               "start_margin": f_start / max(f_roll, 1e-9), "turn_margin": turn_margin, "climb_deg": climb, "runtime_h": runtime_h, "mass": mass,
               "track": track, "pack": pack, "tau_turn": tau_turn, "tau0": tau0, "tau_top": tau_top, "loads": loads, "v_req_cruise": v_req,
               "v_side": v_side, "accel_uncal": a_mean / cal.get("accel_scale", 1.0), "v_cmd": v_cmd, "limit": limit}
    derived = Derived("ground", body, params, sensors, desc, facts, budget, assumptions, numbers, issues)

    spec, parse_issues = VehicleSpec.parse({"name": b.name, "kind": "ground_wheeled", "description": desc, "body": body, "params": params,
                                            "sensors": sensors})
    derived.issues += [i for i in parse_issues]
    if spec is None:
        return None, derived
    return spec, derived
