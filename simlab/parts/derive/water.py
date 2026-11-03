"""Boats from parts: a hull and a thruster, scaled to the battery you have, give the spec of the boat plug-in (simlab/plugins/boat.py).

The hull's displacement, draft and freeboard come from the boat kind itself (it refuses a hull that would sink). The thruster's bollard
thrust and current at its rated voltage scale with voltage squared, as for a propeller drive. The boat's top speed is the plug-in's own
estimate. This is the roughest of the three derivations; treat it as a first guess.
"""
from __future__ import annotations

from ...spec.vehicle import VehicleSpec
from ..build import Build
from .air import motor_current
from .common import Derived, G, PFact, battery_current, pack_from, rail_of
from .ground import CAPACITY_DERATE, WIRING, _sensor_specs

CRUISE_THRUST = 0.36          # fraction of full thrust used for the runtime estimate (about 60 % of full speed)


def build_to_vehicle(b: Build) -> tuple[VehicleSpec | None, Derived]:
    r = b.resolve()
    h, t = r.hull.params, r.thruster.params
    cal, issues = b.calibration, []
    count = b.drive["count"]
    assumptions = [f"the thruster's bollard thrust ({r.thruster.confidence}) is scaled with voltage: thrust and current go as V^2",
                   "the rudder angle and hull drag come from the boat model's defaults",
                   f"wiring and fasteners add {WIRING:.0%} of the electronics' mass"]
    pack = pack_from(r.battery, b.power["series"], b.power["parallel"])
    items = [("hull", 1, r.hull.mass_kg), ("ballast", 1, b.chassis["ballast_kg"]), (r.thruster.name, count, count * r.thruster.mass_kg),
             (r.battery.name, pack.series * pack.parallel, pack.mass_kg), (r.mcu.name, 1, r.mcu.mass_kg)]
    if r.regulator:
        items.append((r.regulator.name, 1, r.regulator.mass_kg))
    items += [(u.name, 1, part.mass_kg) for u, part in r.sensors]
    if r.lamp:
        items.append((r.lamp.name, r.lamp_count, r.lamp_count * r.lamp.mass_kg))
    items += [(e["name"], 1, e["mass_kg"]) for e in b.extras]
    items.append(("wiring allowance", 1, WIRING * (r.mcu.mass_kg + sum(part.mass_kg for _, part in r.sensors))))
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

    def full(v_oc: float):
        v = v_oc
        for _ in range(6):
            i_m = t["current_a"] * (v / t["rated_v"]) ** 2
            v = v_oc - (count * i_m + elec(v_oc)) * pack.r_pack
        return v, count * t["thrust_max_n"] * (v / t["rated_v"]) ** 2, t["current_a"] * (v / t["rated_v"]) ** 2

    v_ft, thrust, i_full = full(pack.v_nom)
    i_peak = count * full(pack.v_full)[2] + elec(pack.v_full)
    i_cruise = count * motor_current(i_full, t["idle_current_a"], CRUISE_THRUST) + elec(pack.v_nom)
    derate = cal.get("battery_derate", CAPACITY_DERATE)
    if "battery_derate" not in cal:
        assumptions.append(f"{derate:.0%} of the rated capacity is usable")
    runtime_h = derate * pack.q_mah / 1000.0 / max(i_cruise, 1e-6)
    params = {"thrust_max": max(1.0, min(20000.0, thrust)), "rudder_max_deg": 35.0, "drag_scale": 1.0}
    body = {"mass": min(max(mass, 0.001), 5000.0), "length": h["length_m"], "width": h["width_m"], "height": h["height_m"]}
    sensors = _sensor_specs(b, r, pack, assumptions)
    desc = (b.description + "  " if b.description else "") + f"Built from parts: {r.hull.name}, {count}x {r.thruster.name}, {r.battery.name} {pack.series}S{pack.parallel}P, {r.mcu.name}."
    spec, parse_issues = VehicleSpec.parse({"name": b.name, "kind": "boat", "description": desc, "body": body, "params": params, "sensors": sensors})
    issues += parse_issues
    facts = [PFact("mass", "Mass", mass, "kg", note="measured" if "mass_kg" in cal else "sum of the parts"), PFact("thrust", "Thrust at full throttle", thrust, "N"),
             PFact("bollard", "Thrust / weight", thrust / (mass * G), ""),
             PFact("runtime", "Runtime at about 60 % speed", runtime_h * 60.0, "min"), PFact("peak_current", "Battery current at full throttle", i_peak, "A"),
             PFact("pack_voltage", "Battery voltage", pack.v_nom, "V", pack.v_cut, pack.v_full, f"{pack.series} in series")]
    if spec is not None:
        from_model = [PFact(f.key, f.label, f.value, f.unit) for f in spec.derived() if f.key in ("top_speed", "draft", "freeboard")]
        facts = from_model + facts                              # draft, freeboard and the estimated top speed come from the boat model itself
    budget = {"mass": [{"part": k, "qty": q, "kg": round(kg, 4)} for k, q, kg in items], "mass_total": mass_est,
              "current": {"loads": [{"name": k, "a": i} for k, i in loads], "rail_a": i_rail, "battery_elec_a": elec(pack.v_nom), "cruise_a": i_cruise,
                          "stall_a": i_peak, "pack_max_a": pack.max_a, "per_motor_stall_a": i_full},
              "voltage": {"pack_full": pack.v_full, "pack_nom": pack.v_nom, "pack_cut": pack.v_cut, "rail_min": rail.v_min, "rail_max": rail.v_max, "rail_via": rail.via},
              "chain": f"{pack.series}x {r.battery.name} ({pack.v_cut:.1f}-{pack.v_full:.1f} V)" + (f" -> {r.regulator.name}" if r.regulator else " -> controller directly")
                       + f"; thrust: pack -> {count}x {r.thruster.name}"}
    numbers = {"pack": pack, "rail": rail, "i_rail": i_rail, "runtime_h": runtime_h, "i_cruise": i_cruise, "i_peak": i_peak, "climb_deg": 0.0, "mass": mass,
               "v_top": next((f.value for f in facts if f.key == "top_speed"), 0.0), "thrust": thrust, "count": count, "i_full": i_full, "loads": loads,
               "rated_v": t["rated_v"], "need_pins": 2}
    return spec, Derived("water", body, params, sensors, desc, facts, budget, assumptions, numbers, issues)
