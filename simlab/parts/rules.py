"""Checks on a build: electrical (E), mechanical (M). Each returns Findings that say what is wrong, why, and what would fix it.

A finding's `fix` is a hint the recommender (`recommend.py`) turns into concrete part suggestions:
    {"action": "add_part" | "change_part" | "change_value", "kind": <part kind>, "constraints": {...}, "why": "..."}
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..spec.vehicle import VehicleSpec
from .build import Build
from .derive.common import Derived, G
from .derive.ground import START_MARGIN_MIN, TURN_MARGIN_MIN

# interface -> the capability the controller must list; None = needs nothing but a pin
INTERFACE_NEEDS = {"gpio_trig_echo": None, "gpio_digital": None, "i2c": "i2c", "spi": "spi", "uart": "uart", "analog": "adc", "wifi_stream": None, "csi": "csi", "usb": "usb"}
PINS = {"gpio_trig_echo": 2, "gpio_digital": 1, "analog": 1, "uart": 2}          # i2c is shared (2 pins however many devices); csi / usb / wifi need none


@dataclass
class Finding:
    id: str
    severity: str                  # error | warn | info
    title: str
    detail: str = ""
    fix: dict | None = None
    scope: str = "build"           # "build": true of the machine itself; "task": true only for the chosen task

    def to_dict(self) -> dict:
        return {"id": self.id, "severity": self.severity, "title": self.title, "detail": self.detail, "fix": self.fix, "scope": self.scope}


def _sensor_pins(res) -> int:
    n, i2c = 0, False
    for _, part in res.sensors:
        iface = part.params["interface"]
        if iface in ("i2c", "spi"):
            i2c = i2c or iface == "i2c"
            n += 0 if iface == "i2c" else 4
        else:
            n += PINS.get(iface, 0)
    return n + (2 if i2c else 0)


def _gpio_needed(b: Build, res) -> int:
    d = res.driver.params
    return 2 * d["gpio_per_channel"] + d["extra_gpio"] + _sensor_pins(res)          # one driver channel per side


def supply_findings(res, nums, v_stall_loaded: float, load_label: str = "the motors") -> list[Finding]:
    """E05: is the controller (and everything on its rail) fed a voltage it accepts, over the whole charge range and under load?"""
    mcu, pack, rail, out = res.mcu.params, nums["pack"], nums["rail"], []
    reg = res.regulator
    if reg is None:
        low = min(pack.v_cut, v_stall_loaded)
        if low < mcu["vin_min"]:
            out.append(Finding("E05", "error", f"The {res.mcu.name} will brown out on this battery",
                               f"Wired straight to the pack it sees {pack.v_cut:.1f} V when the pack is empty and about {v_stall_loaded:.1f} V when {load_label} load it; "
                               f"it needs at least {mcu['vin_min']:g} V (the on-board linear regulator drops about 1.1-1.3 V). "
                               "Expect resets whenever the motors start. Power the boards through a boost regulator (see docs/HARDWARE.md).",
                               {"action": "add_part", "kind": "regulator", "constraints": {"topology": "boost", "v_out": [mcu["vin_min"] + 0.3, mcu["vin_max"]],
                                                                                              "i_out_max_a": [1.2 * nums["i_rail"], None], "v_in_min": [None, pack.v_cut]},
                                "why": f"a boost regulator giving the controller at least {mcu['vin_min'] + 0.3:.1f} V"}))
        if pack.v_full > mcu["vin_max"]:
            out.append(Finding("E05", "error", f"Battery voltage is too high for the {res.mcu.name}",
                               f"A fresh pack is {pack.v_full:.1f} V; the board takes up to {mcu['vin_max']:g} V.",
                               {"action": "add_part", "kind": "regulator", "constraints": {"topology": "buck", "v_out": [mcu["vin_min"] + 0.3, mcu["vin_max"]], "v_in_max": [pack.v_full, None]},
                                "why": "a step-down regulator for the electronics"}))
    else:
        r = reg.params
        if pack.v_full > r["v_in_max"]:
            out.append(Finding("E05", "error", f"Battery voltage exceeds the {reg.name}'s input range",
                               f"A fresh pack is {pack.v_full:.1f} V; the regulator takes up to {r['v_in_max']:g} V."))
        if v_stall_loaded < max(r["v_in_min"], r["v_out"] + r["dropout_v"] if r["topology"] != "boost" else 0.0):
            out.append(Finding("E05", "warn", f"The {reg.name} may drop out when {load_label} load the battery",
                               f"Under stall the pack sags to about {v_stall_loaded:.1f} V; the regulator needs "
                               f"{max(r['v_in_min'], r['v_out'] + r['dropout_v'] if r['topology'] != 'boost' else 0.0):.1f} V in."))
        if 1.2 * nums["i_rail"] > r["i_out_max_a"]:
            out.append(Finding("E05", "error", f"The {reg.name} cannot supply the electronics",
                               f"The boards, sensors and lamp draw {nums['i_rail']:.2f} A; the regulator is rated {r['i_out_max_a']:g} A (and should run at about 80 % of that).",
                               {"action": "change_part", "kind": "regulator", "constraints": {"i_out_max_a": [1.2 * nums["i_rail"], None], "v_out": [r["v_out"] - 0.3, r["v_out"] + 0.3]},
                                "why": f"a regulator rated above {1.2 * nums['i_rail']:.1f} A"}))
        if rail.v_min < mcu["vin_min"]:
            out.append(Finding("E05", "error", f"The {res.mcu.name} is under-powered from the {reg.name}",
                               f"The rail is {rail.v_min:.1f} V at its lowest; the board needs {mcu['vin_min']:g} V.",
                               {"action": "change_part", "kind": "regulator", "constraints": {"v_out": [mcu["vin_min"] + 0.3, mcu["vin_max"]]}, "why": "a higher output voltage"}))
        if rail.v_max > mcu["vin_max"]:
            out.append(Finding("E05", "warn", f"The {res.mcu.name} could see too high a voltage",
                               f"The rail reaches {rail.v_max:.1f} V (a boost converter passes a fresh pack straight through); the board takes up to {mcu['vin_max']:g} V."))
    # every load on the rail must accept the rail's whole range (3.3 V parts are fed from the controller's own 3.3 V pin)
    for use, part in res.sensors:
        p = part.params
        if p["supply_v"] <= mcu["logic_v"] + 0.2:
            continue
        lo, hi = p["supply_v"] * (1 - p["supply_tol"]), p["supply_v"] * (1 + p["supply_tol"])
        if rail.v_max > hi + 1e-9:
            out.append(Finding("E05", "warn", f"{use.name} may see too high a voltage",
                               f"It accepts {lo:.2f}-{hi:.2f} V; the rail reaches {rail.v_max:.2f} V" + (" (a boost converter passes a fresh pack through above its set output)" if reg and reg.params["topology"] == "boost" else "") + "."))
        if rail.v_min < lo - 1e-9:
            out.append(Finding("E05", "warn", f"{use.name} may be under-powered",
                               f"It needs at least {lo:.2f} V; the rail falls to {rail.v_min:.2f} V."))
    if res.lamp:
        lp = res.lamp.params
        lo, hi = lp["supply_v"] * (1 - lp["supply_tol"]), lp["supply_v"] * (1 + lp["supply_tol"])
        if rail.v_max > hi + 1e-9 or rail.v_min < lo - 1e-9:
            out.append(Finding("E05", "info", "The lamp's brightness will follow the rail",
                               f"It is designed for {lo:.2f}-{hi:.2f} V; the rail runs {rail.v_min:.2f}-{rail.v_max:.2f} V."))
    return out


def controller_findings(res, need: int, who: str = "the driver and sensors") -> list[Finding]:
    """E06 (sensor logic levels), E07 (pins), E08 (interfaces), E09 (a camera board as the only brain)."""
    mcu, out = res.mcu.params, []
    for use, part in res.sensors:
        p = part.params
        if "logic_v" in p and p["logic_v"] > mcu["logic_v"] + 0.2 and p["interface"] in ("gpio_trig_echo", "gpio_digital", "analog", "uart", "i2c", "spi"):
            out.append(Finding("E06", "warn", f"{use.name} outputs {p['logic_v']:g} V into {mcu['logic_v']:g} V pins",
                               "Put a level shifter or a resistor divider (for example 10 k / 20 k) on its output pin(s), or the controller's input can be damaged."))

    # E07 pins
    if need > mcu["gpio_free"]:
        out.append(Finding("E07", "error", f"Not enough pins on the {res.mcu.name}",
                           f"{who.capitalize()} need {need} pins; the board has {mcu['gpio_free']} free.",
                           {"action": "change_part", "kind": "mcu", "constraints": {"gpio_free": [need, None]}, "why": f"a controller with at least {need} free pins"}))

    # E08 interfaces
    for use, part in res.sensors:
        p = part.params
        iface = p["interface"]
        if part.kind == "sensor_camera" and iface == "integrated":
            if p["integrated_tag"] not in mcu["integrated"]:
                out.append(Finding("E08", "error", f"{use.name} is built onto a different board",
                                   f"It is the camera of a board that lists '{p['integrated_tag']}'; the controller {res.mcu.name} does not have it."))
            continue
        need_cap = INTERFACE_NEEDS.get(iface)
        if need_cap and need_cap not in mcu["interfaces"]:
            out.append(Finding("E08", "error", f"The {res.mcu.name} cannot talk to {use.name}",
                               f"It needs {need_cap}; the controller offers {mcu['interfaces']}."))

    # E09 a camera board as the only brain
    if mcu["integrated"]:
        out.append(Finding("E09", "warn", f"The {res.mcu.name} is a camera board with few free pins",
                           "Camera and Wi-Fi already keep it busy; driving motors from it is fragile. docs/HARDWARE.md uses a separate controller board and the camera board only for video."))
    return out


def electrical(b: Build, dv: Derived) -> list[Finding]:
    res, nums = b.resolve(), dv.numbers
    pack, rail = nums["pack"], nums["rail"]
    m, drv, mcu = res.motor.params, res.driver.params, res.mcu.params
    out: list[Finding] = []
    n, per_side = nums["n_motors"], nums["n_motors"] / 2

    # E01 pack voltage against the motor's rating
    if pack.v_nom > 1.25 * m["voltage_rated"]:
        out.append(Finding("E01", "warn", "Battery voltage is well above the motor's rating",
                           f"{pack.v_nom:.1f} V nominal against motors rated {m['voltage_rated']:g} V: they run faster and hotter and wear sooner. "
                           f"Limit the PWM duty in the firmware to about {100 * 1.1 * m['voltage_rated'] / pack.v_nom:.0f} %, or use motors rated for {pack.v_nom:.0f} V.",
                           {"action": "change_part", "kind": "motor_dc", "constraints": {"voltage_rated": [pack.v_nom * 0.8, pack.v_nom * 1.25]},
                            "why": f"motors rated near {pack.v_nom:.0f} V"}))
    elif pack.v_nom < 0.5 * m["voltage_rated"]:
        out.append(Finding("E01", "warn", "Battery voltage is far below the motor's rating",
                           f"{pack.v_nom:.1f} V nominal against motors rated {m['voltage_rated']:g} V: expect weak torque and low speed.",
                           {"action": "change_part", "kind": "motor_dc", "constraints": {"voltage_rated": [pack.v_nom * 0.8, pack.v_nom * 1.25]},
                            "why": f"motors rated near {pack.v_nom:.0f} V"}))

    # E02 driver supply range
    if pack.v_full > drv["voltage_max"]:
        out.append(Finding("E02", "error", "Battery voltage exceeds the driver's supply range",
                           f"A fresh pack is {pack.v_full:.1f} V; the {res.driver.name} accepts up to {drv['voltage_max']:g} V.",
                           {"action": "change_part", "kind": "driver_dc", "constraints": {"voltage_max": [pack.v_full, None]}, "why": f"driver that takes {pack.v_full:.1f} V"}))
    elif pack.v_cut < drv["voltage_min"]:
        out.append(Finding("E02", "warn", "The driver stops working before the battery is empty",
                           f"Below {drv['voltage_min']:g} V the {res.driver.name} drops out; the pack reaches {pack.v_cut:.1f} V when empty."))
    if drv["channels"] < 2:
        out.append(Finding("E02", "error", "The driver has one channel",
                           "A rover steers by driving its two sides separately, so it needs a two-channel driver (or two drivers).",
                           {"action": "change_part", "kind": "driver_dc", "constraints": {"channels": [2, None]}, "why": "a two-channel driver"}))

    # E03 stall current through each driver channel (a side's motors share a channel)
    i_ch = nums["i_start_full"] * per_side
    if i_ch > drv["peak_a_per_channel"]:
        out.append(Finding("E03", "error", "Stalled motors would trip the driver",
                           f"With a fresh battery each channel carries {i_ch:.2f} A when the wheels are blocked ({per_side:g} motor(s) per side); "
                           f"the {res.driver.name} trips at {drv['peak_a_per_channel']:g} A.",
                           {"action": "change_part", "kind": "driver_dc", "constraints": {"peak_a_per_channel": [i_ch * 1.2, None]}, "why": f"driver rated above {i_ch * 1.2:.1f} A per channel"}))
    elif i_ch > drv["continuous_a_per_channel"]:
        out.append(Finding("E03", "warn", "A stalled motor would overheat the driver",
                           f"A blocked wheel draws {i_ch:.2f} A per channel against {drv['continuous_a_per_channel']:g} A continuous for the {res.driver.name}. "
                           "Short stalls are survivable; a rover that jams against a wall is not.",
                           {"action": "change_part", "kind": "driver_dc", "constraints": {"continuous_a_per_channel": [i_ch * 1.1, None]}, "why": f"driver rated above {i_ch * 1.1:.1f} A continuous"}))

    # E04 battery current at stall
    if nums["i_stall_batt"] > pack.max_a:
        out.append(Finding("E04", "error", "The battery cannot supply the stall current",
                           f"All motors blocked draw {nums['i_stall_batt']:.1f} A; the pack is rated {pack.max_a:g} A continuous.",
                           {"action": "change_part", "kind": "battery", "constraints": {"max_continuous_a": [nums["i_stall_batt"] / pack.parallel, None]},
                            "why": "cells with a higher discharge rating (or more in parallel)"}))


    out += supply_findings(res, nums, pack.v_nom - (n * nums["i_start_nom"] + nums["i_rail"]) * nums["r_pack"])

    if drv["logic_high_min_v"] > mcu["logic_v"]:
        out.append(Finding("E06", "error", "The controller's output is too low for the driver",
                           f"The {res.mcu.name} drives {mcu['logic_v']:g} V; the {res.driver.name} needs {drv['logic_high_min_v']:g} V to read HIGH."))
    out += controller_findings(res, _gpio_needed(b, res))

    # E10 discharge rate
    c_rate = nums["i_cruise"] / (pack.q_mah / 1000.0)
    if pack.chemistry == "nimh" and c_rate > 1.0:
        out.append(Finding("E10", "info", "NiMH cells lose capacity at this discharge rate",
                           f"Cruise draws {nums['i_cruise']:.2f} A = {c_rate:.1f} C; the runtime estimate may be optimistic."))
    return out


def mechanical(b: Build, dv: Derived, spec: VehicleSpec | None) -> list[Finding]:
    nums, out = dv.numbers, []
    v = dv.params["max_speed"]
    if v < 0.05:
        out.append(Finding("M01", "error", "The vehicle can barely move",
                           f"Predicted top speed is {nums['v_top']:.3f} m/s. Use a higher-voltage battery, a lower gear ratio or lighter parts.",
                           {"action": "change_part", "kind": "motor_dc", "constraints": {}, "why": "motors with more speed or torque"}))
    if nums["start_margin"] < START_MARGIN_MIN:
        out.append(Finding("M02", "error", "The motors cannot reliably start the vehicle",
                           f"Start force is {nums['f_start']:.2f} N against {nums['f_roll']:.2f} N of rolling resistance (needs at least {START_MARGIN_MIN}x). "
                           "Use more torque (a higher gear ratio), more motors, or lose weight.",
                           {"action": "change_part", "kind": "motor_dc", "constraints": {}, "why": "motors with more stall torque"}))
    if nums["turn_margin"] < TURN_MARGIN_MIN:
        out.append(Finding("M03", "warn", "It will not turn in place",
                           f"Turning needs {nums['tau_turn']:.3f} N*m per motor and the motors give {nums['tau0']:.3f} N*m at start (margin {nums['turn_margin']:.2f}, "
                           f"needs {TURN_MARGIN_MIN}). In a pipe it would need a chamber to turn around."))
    if dv.params["max_accel"] < 0.1:
        out.append(Finding("M05", "warn", "Very sluggish acceleration", f"{dv.params['max_accel']:.2f} m/s^2: it takes {v / max(dv.params['max_accel'], 1e-9):.0f} s to reach top speed."))
    for i in (spec.validate() if spec is not None else []):
        if i.level == "error":
            out.append(Finding("M06", "error", f"Dimensions: {i.msg}", i.field))
    return out


def _kind_errors(spec: VehicleSpec | None, fid: str) -> list[Finding]:
    """What the vehicle kind itself refuses (a hull that would sink, a layout that cannot be controlled ...)."""
    if spec is None:
        return []
    return [Finding(fid, i.level, f"The simulator's model: {i.msg}", i.field) for i in spec.validate() if i.level in ("error", "warn")]


def air(b: Build, dv: Derived, spec: VehicleSpec | None) -> list[Finding]:
    r, n, pack = b.resolve(), dv.numbers, dv.numbers["pack"]
    f, p, esc, mcu = r.frame.params, r.propulsor.params, r.esc.params, r.mcu.params
    out: list[Finding] = []
    twr = n["twr"]
    more_thrust = {"action": "change_part", "kind": "propulsor_air", "constraints": {"thrust_max_n_at_ref": [p["thrust_max_n_at_ref"] * 1.2, None]},
                   "why": "motors and propellers with more thrust"}
    if twr < 1.3:
        out.append(Finding("A01", "error", "It will not fly", f"Thrust-to-weight is {twr:.2f}; it needs at least 1.3 to leave the ground with any control.", more_thrust))
    elif twr < 2.0:
        out.append(Finding("A01", "warn", "Sluggish: little thrust to spare", f"Thrust-to-weight is {twr:.2f}. Below about 2 it is slow to climb and cannot recover from a gust.", more_thrust))
    if n["hover_frac"] > 0.7 and twr >= 1.3:
        out.append(Finding("A02", "warn", "Hovering needs most of the thrust",
                           f"Hover takes {100 * n['hover_frac']:.0f} % of the maximum thrust: little is left to manoeuvre with, and the battery sags as it empties.", more_thrust))
    if n["runtime_h"] * 60 < 3.0:
        out.append(Finding("A03", "warn", "Very short flight time", f"About {n['runtime_h'] * 60:.1f} min of hover on this battery.",
                           {"action": "change_part", "kind": "battery", "constraints": {"capacity_mah": [pack.q_mah * 2 / b.power["parallel"], None]}, "why": "a larger battery"}))
    i_max = n["i_max_motor_full"]
    if esc["max_continuous_a"] < 1.1 * i_max:
        out.append(Finding("A04", "error", "The ESCs cannot carry the motors' current",
                           f"Each motor draws up to {i_max:.1f} A at full throttle on a fresh battery; the {r.esc.name} is rated {esc['max_continuous_a']:g} A.",
                           {"action": "change_part", "kind": "esc", "constraints": {"max_continuous_a": [1.1 * i_max, None], "v_max": [pack.v_full, None]},
                            "why": f"ESCs rated above {1.1 * i_max:.0f} A"}))
    if pack.v_full > esc["v_max"]:
        out.append(Finding("A04", "error", "Battery voltage is too high for the ESCs", f"A fresh pack is {pack.v_full:.1f} V; the {r.esc.name} takes up to {esc['v_max']:g} V.",
                           {"action": "change_part", "kind": "esc", "constraints": {"v_max": [pack.v_full, None], "max_continuous_a": [1.1 * i_max, None]},
                            "why": f"ESCs for {pack.v_full:.1f} V"}))
    elif pack.v_cut < esc["v_min"]:
        out.append(Finding("A04", "warn", "The ESCs cut out before the battery is empty",
                           f"Below {esc['v_min']:g} V the {r.esc.name} stops; the pack reaches {pack.v_cut:.1f} V when empty."))
    if n["i_peak"] > pack.max_a:
        out.append(Finding("A05", "error", "The battery cannot supply full throttle",
                           f"All rotors at full throttle draw {n['i_peak']:.0f} A; the pack is rated {pack.max_a:g} A continuous.",
                           {"action": "change_part", "kind": "battery", "constraints": {"max_continuous_a": [n["i_peak"] / b.power["parallel"], None]},
                            "why": "cells with a higher discharge rating"}))
    if p["prop_diameter_in"] > f["max_prop_in"]:
        out.append(Finding("A06", "error", "The propeller is too big for the frame", f"{p['prop_diameter_in']:g} inch propellers on a frame that takes up to {f['max_prop_in']:g}.",
                           {"action": "change_part", "kind": "frame", "constraints": {"max_prop_in": [p["prop_diameter_in"], None]}, "why": "a frame for bigger propellers"}))
    else:
        chord_in = 2 * f["arm_length_m"] * math.sin(math.pi / n["n"]) / 0.0254
        if p["prop_diameter_in"] > 0.95 * chord_in:
            out.append(Finding("A06", "warn", "Neighbouring propellers nearly touch", f"The rotors are {chord_in:.1f} in apart; the propellers are {p['prop_diameter_in']:g} in across."))
    if "pwm" not in mcu["interfaces"]:
        out.append(Finding("E08", "error", f"The {r.mcu.name} has no PWM outputs for the ESCs", f"It lists {mcu['interfaces']}."))
    out += _kind_errors(spec, "A00")
    out += supply_findings(r, n, n["v_ft_loaded"], "the rotors at full throttle")
    out += controller_findings(r, n["n"] + _sensor_pins(r), "the ESC signals and sensors")
    return out


def water(b: Build, dv: Derived, spec: VehicleSpec | None) -> list[Finding]:
    r, n, pack = b.resolve(), dv.numbers, dv.numbers["pack"]
    t, mcu = r.thruster.params, r.mcu.params
    out: list[Finding] = []
    for f in _kind_errors(spec, "W01"):
        if "sink" not in f.title:
            f.id = "W02"                                              # the model's other message is about freeboard
        out.append(f)
    ratio = n["thrust"] / (n["mass"] * G)
    if ratio < 0.15:
        out.append(Finding("W03", "warn", "Little thrust for this weight",
                           f"Thrust is {ratio:.2f} of the boat's weight: expect it to be slow to get going and hard to steer against wind.",
                           {"action": "change_part", "kind": "thruster_water", "constraints": {"thrust_max_n": [t["thrust_max_n"] * 1.5, None]}, "why": "a stronger thruster"}))
    if pack.v_nom > 1.25 * t["rated_v"] or pack.v_nom < 0.6 * t["rated_v"]:
        out.append(Finding("W04", "warn", "Battery voltage does not match the thruster",
                           f"{pack.v_nom:.1f} V nominal against a thruster rated {t['rated_v']:g} V: thrust and current scale with voltage squared.",
                           {"action": "change_part", "kind": "thruster_water", "constraints": {"rated_v": [pack.v_nom * 0.8, pack.v_nom * 1.25]},
                            "why": f"a thruster rated near {pack.v_nom:.0f} V"}))
    if n["i_peak"] > pack.max_a:
        out.append(Finding("E04", "error", "The battery cannot supply full thrust", f"Full thrust draws {n['i_peak']:.0f} A; the pack is rated {pack.max_a:g} A continuous.",
                           {"action": "change_part", "kind": "battery", "constraints": {"max_continuous_a": [n["i_peak"] / b.power["parallel"], None]},
                            "why": "cells with a higher discharge rating"}))
    if "pwm" not in mcu["interfaces"]:
        out.append(Finding("E08", "error", f"The {r.mcu.name} has no PWM outputs for the thruster and rudder", f"It lists {mcu['interfaces']}."))
    out += supply_findings(r, n, pack.v_nom - n["i_peak"] * pack.r_pack, "the thruster at full throttle")
    out += controller_findings(r, 2 + _sensor_pins(r), "the thruster, rudder and sensors")
    return out


def run(b: Build, dv: Derived, spec: VehicleSpec | None) -> list[Finding]:
    """All checks, errors first."""
    findings = [Finding("D01", i.level, f"Derived value: {i.msg}", i.field) for i in dv.issues]
    if b.platform == "ground":
        findings += electrical(b, dv) + mechanical(b, dv, spec)
    elif b.platform == "air":
        findings += air(b, dv, spec)
    else:
        findings += water(b, dv, spec)
    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(findings, key=lambda f: order[f.severity])
