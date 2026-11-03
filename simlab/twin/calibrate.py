"""Tie the twin to the measurements: the fitted motor strengths, start duties and yaw gain, and a predicted-vs-measured table.

`fit_twin` returns a TwinModel whose predictions reproduce what the real rover did (so the simulator's `side_gain` hazard and the planner's
runs describe THIS rover, not an ideal one). The table is what the app shows as "simulation said X, the rover did Y".
"""
from __future__ import annotations

import math

from .diagnose import Diagnosis
from .model import TwinModel
from .protocol import Dataset


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def fit_twin(ds: Dataset, diag: Diagnosis, base: TwinModel | None = None) -> tuple[TwinModel, list[dict]]:
    base = base or TwinModel()
    cfg = ds.meta.get("cfg", {}) or {}
    trim = (float(cfg.get("trimL", 1.0)), float(cfg.get("trimR", 1.0)))
    level = float(ds.meta.get("level", 180))
    ideal = TwinModel(track_m=base.track_m, mps_per_duty=base.mps_per_duty, yaw_gain=1.0, trim=trim)

    # yaw gain: how much faster/slower than the ideal model the real rover spins (side strengths are unity in `ideal`)
    sl = [float(r["yaw"]) / float(r["secs"]) for r in ds.runs.get("spin_l", []) if r.get("yaw") is not None]
    sr = [float(r["yaw"]) / float(r["secs"]) for r in ds.runs.get("spin_r", []) if r.get("yaw") is not None]
    yaw_gain = base.yaw_gain
    if sl and sr:
        # the strengths are only known relative to each other (the stronger side = 1.0), so yaw_gain absorbs the overall scale:
        # it is whatever makes the model spin as fast as the real rover did, with the fitted side strengths and the trim that was active
        sides = diag.side_gain if diag.usable else (1.0, 1.0)
        ref = abs(TwinModel(track_m=base.track_m, mps_per_duty=base.mps_per_duty, yaw_gain=1.0, side_gain=sides, trim=trim).turn_rate_dps(-level, level))
        yaw_gain = (abs(_mean(sl)) + abs(_mean(sr))) / 2.0 / max(ref, 1e-6)
    twin = TwinModel(track_m=base.track_m, mps_per_duty=base.mps_per_duty, yaw_gain=round(yaw_gain, 3), side_gain=diag.side_gain, start_duty=diag.start_duty, trim=trim)

    table: list[dict] = []
    f = [float(r["yaw"]) / float(r["secs"]) for r in ds.runs.get("straight_f", []) if r.get("yaw") is not None]
    if f:
        table.append({"what": "straight run: turn rate", "unit": "deg/s", "ideal_simulation": round(ideal.turn_rate_dps(level, level), 2),
                      "measured": round(_mean(f), 2), "after_fit": round(twin.turn_rate_dps(level, level), 2)})
    if sl and sr:
        table.append({"what": "spin left: turn rate", "unit": "deg/s", "ideal_simulation": round(ideal.turn_rate_dps(-level, level), 1),
                      "measured": round(_mean(sl), 1), "after_fit": round(twin.turn_rate_dps(-level, level), 1)})
        table.append({"what": "spin right: turn rate", "unit": "deg/s", "ideal_simulation": round(ideal.turn_rate_dps(level, -level), 1),
                      "measured": round(_mean(sr), 1), "after_fit": round(twin.turn_rate_dps(level, -level), 1)})
    return twin, table


def apply_to_hardware(hw, ds: Dataset, diag: Diagnosis) -> dict:
    """Store the measured spin rate in the app's rover profile (HardwareManager.calibrate_yaw): the plans then use the real turn rate.
    `hw` is a sewerbot.brain.hardware.HardwareManager; returns what it changed ({} if there were no spins)."""
    sl = [r for r in ds.runs.get("spin_l", []) if r.get("yaw") is not None]
    sr = [r for r in ds.runs.get("spin_r", []) if r.get("yaw") is not None]
    if not (sl and sr):
        return {}
    rate = (abs(_mean([r["yaw"] / r["secs"] for r in sl])) + abs(_mean([r["yaw"] / r["secs"] for r in sr]))) / 2.0
    if not math.isfinite(rate) or rate <= 0:
        return {}
    return hw.calibrate_yaw(1.5, 1.0, rate)
