"""Multirotors from parts: a motor + propeller thrust test, scaled to the battery you have, gives the spec the MuJoCo drone plant needs.

The test point (thrust and current at full throttle at one voltage) is scaled with the usual propeller laws: rotor speed follows the
voltage, thrust follows speed squared, power follows speed cubed, so at full throttle thrust and current both go as V^2. In between,
power goes as thrust^1.5 on top of an idle current. That curve was checked against the full table of the BrotherHobby R5 2306 test
(25/50/75/100 % throttle) in tests/test_parts_air.py.

The numbers the autopilot needs that no datasheet gives (maximum tilt, climb and descent speeds, yaw rate, drag, motor time constant)
come from simple rules of thumb, written below, and are conservative limits for the simulator's autopilot, not claims about what the
airframe could do on its limits. The result is rougher than the ground model: treat it as a good first guess.
"""
from __future__ import annotations

import math

from ...spec.params import Issue
from ...spec.vehicle import VehicleSpec
from ...vehicles.multirotor import rotor_layout
from ..build import Build
from .common import Derived, G, PFact, battery_current, pack_from, rail_of
from .ground import CAPACITY_DERATE, WIRING, _sensor_specs


def motor_tau(d_in: float) -> float:
    """Spin-up time constant by propeller size: bigger propellers are heavier to accelerate."""
    return 0.02 if d_in <= 3 else 0.03 if d_in <= 5.5 else 0.05 if d_in <= 8 else 0.08


def full_throttle(v_oc: float, r_pack: float, n: int, t_ref: float, i_ref: float, v_ref: float, i_elec: float) -> tuple[float, float, float]:
    """(loaded battery voltage, thrust per rotor, current per rotor) at full throttle, with the battery sagging under all rotors."""
    v = v_oc
    for _ in range(6):
        i_m = i_ref * (v / v_ref) ** 2
        v = v_oc - (n * i_m + i_elec) * r_pack
    return v, t_ref * (v / v_ref) ** 2, i_ref * (v / v_ref) ** 2


def motor_current(i_max: float, idle: float, thrust_fraction: float) -> float:
    """Current of one motor producing `thrust_fraction` of its maximum thrust."""
    return idle + (i_max - idle) * max(0.0, thrust_fraction) ** 1.5


def build_to_vehicle(b: Build) -> tuple[VehicleSpec | None, Derived]:
    r = b.resolve()
    f, p, esc = r.frame.params, r.propulsor.params, r.esc.params
    cal, issues = b.calibration, []
    assumptions = [f"the thrust test of {r.propulsor.name} ({r.propulsor.confidence}) is scaled with voltage: thrust and current go as V^2 at full throttle",
                   "power rises as thrust^1.5 on top of an idle current (checked against a published thrust table)",
                   "maximum tilt, climb, descent, yaw rate and drag are rules of thumb for the simulator's autopilot, not measurements",
                   f"wiring and fasteners add {WIRING:.0%} of the electronics' mass"]
    layout = rotor_layout(f["layout"], f["arm_length_m"])
    n = len(layout)
    pack = pack_from(r.battery, b.power["series"], b.power["parallel"])
    items = [("frame", 1, r.frame.mass_kg), ("extra mass", 1, b.chassis["extra_mass_kg"]), (r.propulsor.name, n, n * r.propulsor.mass_kg),
             (r.esc.name, n, n * r.esc.mass_kg), (r.battery.name, pack.series * pack.parallel, pack.mass_kg), (r.mcu.name, 1, r.mcu.mass_kg)]
    if r.regulator:
        items.append((r.regulator.name, 1, r.regulator.mass_kg))
    items += [(u.name, 1, part.mass_kg) for u, part in r.sensors]
    if r.lamp:
        items.append((r.lamp.name, r.lamp_count, r.lamp_count * r.lamp.mass_kg))
    items += [(e["name"], 1, e["mass_kg"]) for e in b.extras]
    electronics = r.esc.mass_kg * n + r.mcu.mass_kg + sum(part.mass_kg for _, part in r.sensors)
    items.append(("wiring allowance", 1, WIRING * electronics))
    mass_est = sum(kg for _, _, kg in items)
    mass = cal.get("mass_kg", mass_est)
    if "mass_kg" in cal:
        assumptions.append(f"mass {mass:g} kg is your measurement (the parts add up to {mass_est:.3f} kg)")

    loads = [("controller", r.mcu.params["current_a"])] + [(u.name, part.params["current_a"]) for u, part in r.sensors]
    if r.lamp:
        loads.append(("lamp", r.lamp.params["current_a"] * r.lamp_count))
    loads += [(e["name"], e["current_a"]) for e in b.extras if e["current_a"]]
    i_rail = sum(i for _, i in loads)
    rail = rail_of(pack, r.regulator)

    def elec(v_batt: float) -> float:
        return battery_current(i_rail, rail.v_nom, v_batt, r.regulator)

    v_ref, t_ref, i_ref, idle = p["ref_voltage"], p["thrust_max_n_at_ref"], p["current_max_a_at_ref"], p["idle_current_a"]
    states = {k: full_throttle(v, pack.r_pack, n, t_ref, i_ref, v_ref, elec(v)) for k, v in (("full", pack.v_full), ("nom", pack.v_nom), ("cut", pack.v_cut))}
    v_ft, t_max, i_max = states["nom"]
    twr = n * t_max / (mass * G)
    # hover: the battery sags a little under the hover current, which lowers the thrust available
    v = pack.v_nom
    for _ in range(4):
        t_av, i_av = t_ref * (v / v_ref) ** 2, i_ref * (v / v_ref) ** 2
        frac = min(1.0, (mass * G / n) / t_av)
        i_hover_motor = motor_current(i_av, idle, frac)
        v = pack.v_nom - (n * i_hover_motor + elec(pack.v_nom)) * pack.r_pack
    i_hover = n * i_hover_motor + elec(pack.v_nom)
    derate = cal.get("battery_derate", CAPACITY_DERATE)
    if "battery_derate" not in cal:
        assumptions.append(f"{derate:.0%} of the rated capacity is usable")
    flight_h = derate * pack.q_mah / 1000.0 / max(i_hover, 1e-6)
    peak_full = states["full"]
    i_peak = n * peak_full[2] + elec(pack.v_full)

    d_m = p["prop_diameter_in"] * 0.0254
    tilt = math.degrees(math.acos(1.0 / (0.85 * twr))) if 0.85 * twr > 1.0 else 5.0
    tilt = min(35.0, max(5.0, tilt))
    drag = 0.0666 * mass ** (2.0 / 3.0)
    v_pitch = p["prop_pitch_in"] * 0.0254 * (p["kv"] * pack.v_nom * 0.75 / 60.0)
    v_tilt = mass * G * math.tan(math.radians(tilt)) / max(drag, 1e-6)
    scale = cal.get("speed_scale", 1.0)
    climb = max(0.1, min(30.0, 2.7 * (twr - 1.0)))
    params = {"layout": f["layout"], "arm_length": f["arm_length_m"], "thrust_max": max(0.02, min(1000.0, t_max)),
              "torque_coef": max(0.002, min(0.2, 0.125 * d_m)), "motor_tau": motor_tau(p["prop_diameter_in"]), "drag": min(10.0, drag),
              "max_tilt_deg": tilt, "max_speed": max(0.2, min(100.0, 0.5 * min(v_pitch, v_tilt) * scale)), "max_climb": climb,
              "max_descent": max(0.1, 0.75 * climb), "max_yaw_rate": max(0.1, min(15.0, 2.5 + 0.4 * twr)),
              "light_intensity": min(50.0, r.lamp.params["light_intensity"] * r.lamp_count) if r.lamp else 0.0}
    body = {"mass": min(max(mass, 0.001), 5000.0), "length": f["length_m"], "width": f["width_m"], "height": f["height_m"]}
    sensors = _sensor_specs(b, r, pack, assumptions)
    desc = (b.description + "  " if b.description else "") + f"Built from parts: {n}x {r.propulsor.name}, {r.frame.name}, {r.battery.name} {pack.series}S{pack.parallel}P, {r.mcu.name}."

    facts = [PFact("top_speed", "Top speed (autopilot limit)", params["max_speed"], "m/s", note="half of the lower of the propeller-pitch speed and the tilt-limited speed"),
             PFact("twr", "Thrust-to-weight", twr, "", note="below about 2 is sluggish; below 1.3 will not fly"),
             PFact("hover_thrust", "Hover thrust (of maximum)", 100.0 * frac, "%", note="above about 70 % leaves little to manoeuvre with"),
             PFact("hover_current", "Battery current in hover", i_hover, "A"),
             PFact("runtime", "Flight time in hover", flight_h * 60.0, "min"),
             PFact("climb", "Climb rate limit", climb, "m/s"), PFact("tilt", "Largest tilt", tilt, "deg"),
             PFact("thrust_rotor", "Thrust per rotor (full throttle)", t_max, "N", lo=states["cut"][1], hi=states["full"][1], note="spent .. fresh battery"),
             PFact("peak_current", "Battery current at full throttle", i_peak, "A"),
             PFact("mass", "Mass", mass, "kg", note="measured" if "mass_kg" in cal else "sum of the parts"),
             PFact("pack_voltage", "Battery voltage", pack.v_nom, "V", pack.v_cut, pack.v_full, f"{pack.series}S")]
    budget = {"mass": [{"part": k, "qty": q, "kg": round(kg, 4)} for k, q, kg in items], "mass_total": mass_est,
              "current": {"loads": [{"name": k, "a": i} for k, i in loads], "rail_a": i_rail, "battery_elec_a": elec(pack.v_nom), "cruise_a": i_hover,
                          "stall_a": i_peak, "pack_max_a": pack.max_a, "per_motor_stall_a": peak_full[2]},
              "voltage": {"pack_full": pack.v_full, "pack_nom": pack.v_nom, "pack_cut": pack.v_cut, "rail_min": rail.v_min, "rail_max": rail.v_max, "rail_via": rail.via},
              "chain": f"{pack.series}x {r.battery.name} ({pack.v_cut:.1f}-{pack.v_full:.1f} V)" + (f" -> {r.regulator.name}" if r.regulator else " -> controller directly")
                       + f"; power: pack -> {n}x {r.esc.name} -> {n}x {r.propulsor.name}"}
    numbers = {"n": n, "twr": twr, "hover_frac": frac, "i_hover": i_hover, "i_peak": i_peak, "i_max_motor_full": peak_full[2], "pack": pack, "rail": rail,
               "i_rail": i_rail, "runtime_h": flight_h, "i_cruise": i_hover, "climb_deg": 90.0, "mass": mass, "v_top": params["max_speed"],
               "v_ft_loaded": v_ft, "loads": loads, "prop_d_in": p["prop_diameter_in"], "arm": f["arm_length_m"], "need_pins": n}
    derived = Derived("air", body, params, sensors, desc, facts, budget, assumptions, numbers, issues)
    spec, parse_issues = VehicleSpec.parse({"name": b.name, "kind": "multirotor", "description": desc, "body": body, "params": params, "sensors": sensors})
    derived.issues += parse_issues
    return spec, derived
