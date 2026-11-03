"""Tie the predictions to your real vehicle: you measure a few numbers, the build remembers them, and the prediction becomes the measurement.

Measurements (all optional; procedure in docs/BUILDER.md, about two minutes with a ruler, a stopwatch and a kitchen scale):

    mass_g                       the assembled vehicle on a scale
    straight_cm, straight_s      full speed in a straight line: ignore the first second, then the distance covered in `straight_s` seconds
    spin_deg, spin_s             full-speed spin on the spot: the angle turned in `spin_s` seconds
    t90_s                        seconds from standstill to 90 % of top speed (a video helps)
    runtime_min                  minutes of driving at cruise on a full battery until it cuts out

Each one replaces one assumption: mass, the speed scale, the turn-rate gain, the acceleration scale and the usable battery fraction. They are
fitted in that order because later ones depend on earlier ones (the spin speed depends on the calibrated speed, and so on).
"""
from __future__ import annotations

import copy
import datetime
import math
from typing import Mapping

from .build import Build, CALIBRATION_KEYS
from .derive import build_to_vehicle
from .evaluate import evaluate

FIELDS = ("mass_g", "straight_cm", "straight_s", "spin_deg", "spin_s", "t90_s", "runtime_min")


def _derive(d: dict):
    b, issues = Build.parse(d)
    if b is None:
        raise ValueError("; ".join(str(i) for i in issues if i.level == "error"))
    return build_to_vehicle(b)[1]


def _clamp(key: str, value: float, notes: list[str]) -> float:
    p = CALIBRATION_KEYS[key]
    v = min(max(value, p.min), p.max)
    if v != value:
        notes.append(f"{key} {value:.3g} is outside {p.min:g}..{p.max:g}: clamped to {v:.3g}. Re-check the measurement.")
    return v


def _num(m: Mapping, key: str) -> float | None:
    v = m.get(key)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def fit(build: Build | Mapping, measurements: Mapping | None = None, clear: bool = False) -> dict:
    """{"build": the build with its calibration block, "table": predicted vs measured, "notes": [...], "result": evaluate(...)}.

    Raises ValueError for a build that does not parse (the API turns that into a 422)."""
    b = copy.deepcopy(build.to_dict() if isinstance(build, Build) else dict(build))
    if b.get("platform", "ground") != "ground":
        raise ValueError("calibration is available for ground builds only: the air and water models are first guesses, compare them with a flight or a float test yourself")
    if clear:
        b["calibration"] = {}
        return {"build": b, "table": [], "notes": ["calibration cleared"], "result": evaluate(b, suggest=False)}
    m = dict(measurements or {})
    cal = dict(b.get("calibration") or {})
    table, notes = [], []

    def predicted(drop: str):
        return _derive({**b, "calibration": {k: v for k, v in cal.items() if k != drop}})

    mass, dist, secs = _num(m, "mass_g"), _num(m, "straight_cm"), _num(m, "straight_s")
    spin, spin_s, t90, run = _num(m, "spin_deg"), _num(m, "spin_s"), _num(m, "t90_s"), _num(m, "runtime_min")
    if mass:
        dv = predicted("mass_kg")
        cal["mass_kg"] = _clamp("mass_kg", mass / 1000.0, notes)
        table.append({"what": "mass", "unit": "kg", "predicted": dv.numbers["mass"], "measured": mass / 1000.0})
    if dist and secs:
        dv = predicted("speed_scale")
        v_meas = dist / 100.0 / secs
        cal["speed_scale"] = _clamp("speed_scale", v_meas / dv.numbers["v_top_free"], notes)
        table.append({"what": "top speed", "unit": "m/s", "predicted": dv.numbers["v_top_free"], "measured": v_meas})
    elif dist or secs:
        notes.append("the straight run needs both a distance and a time")
    if spin and spin_s:
        dv = predicted("yaw_gain")
        w_meas = math.radians(spin) / spin_s
        w_pred = dv.params["max_yaw_rate"]
        cal["yaw_gain"] = _clamp("yaw_gain", w_meas * dv.numbers["track"] / (2.0 * dv.numbers["v_side"]), notes)
        table.append({"what": "turn rate", "unit": "rad/s", "predicted": w_pred, "measured": w_meas})
    elif spin or spin_s:
        notes.append("the spin needs both an angle and a time")
    if t90:
        dv = predicted("accel_scale")
        v_cmd = dv.params["max_speed"]
        a_meas = 0.9 * v_cmd / t90
        cal["accel_scale"] = _clamp("accel_scale", a_meas / max(dv.numbers["accel_uncal"], 1e-9), notes)
        table.append({"what": "acceleration", "unit": "m/s^2", "predicted": dv.numbers["accel_uncal"], "measured": a_meas})
    if run:
        dv = predicted("battery_derate")
        n = dv.numbers
        cal["battery_derate"] = _clamp("battery_derate", (run / 60.0) * n["i_cruise"] / (n["pack"].q_mah / 1000.0), notes)
        table.append({"what": "runtime", "unit": "min", "predicted": n["runtime_h"] * 60.0, "measured": run})
    if not table:
        notes.append("nothing to fit: enter at least one measurement")
        return {"build": b, "table": [], "notes": notes, "result": evaluate(b, suggest=False)}
    cal["measured"] = {**(cal.get("measured") or {}), **{k: float(m[k]) for k in FIELDS if _num(m, k)}}
    cal["date"] = datetime.date.today().isoformat()
    b["calibration"] = cal
    result = evaluate(b, suggest=False)
    after = {f["key"]: f["value"] for f in result["facts"]}
    key = {"mass": "mass", "top speed": "top_speed", "turn rate": "turn_rate", "acceleration": "accel", "runtime": "runtime"}
    for row in table:
        row["error_pct"] = round(100.0 * (row["predicted"] / row["measured"] - 1.0), 1)
        row["after"] = after.get(key[row["what"]])
    if any(abs(r["error_pct"]) > 30 for r in table):
        notes.append("a prediction was more than 30 % off: usually a motor or battery entry that does not match what you have. Check those first.")
    return {"build": b, "table": table, "notes": notes, "result": result}
