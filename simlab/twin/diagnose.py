"""From the recorded motions to plain-words findings with evidence and a confidence, and the trim that would fix the pull.

The maths (differential drive, wheel strengths gL and gR, a = duty x speed-per-duty x yaw_gain / track):
    straight, both wheels at the same duty      turn rate  w_s  = a (gR - gL)
    spin on the spot (either way)               turn rate       = a (gR + gL)
    one wheel alone (left / right)              turn rate       = - a gL   /   + a gR
so   d = w_s / spin_rate = (gR - gL) / (gR + gL)   and   gL / gR = (1 - d) / (1 + d).   Three independent measurements (straight, spin, one-wheel
ratio) must agree: when they do the confidence is high; when they do not, the finding says so and asks for the ruler.
Positive yaw = turning left. Nothing here needs speed or distance: the gyro alone gives the imbalance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .protocol import THRESH_DUTIES, Dataset

THRESH_DUTIES_MAX = max(THRESH_DUTIES)
LEVELS = ("ok", "info", "warn", "fix", "fail")
TRIM_CAP = 1.3
MIN_MOVE_DEG = 2.0            # a motion smaller than this is indistinguishable from gyro noise on these short runs


@dataclass
class Finding:
    id: str
    level: str
    text: str
    evidence: dict = field(default_factory=dict)
    confidence: float = 1.0
    action: dict | None = None            # e.g. {"cfg": {"trimL": 1.07}}: applied only after the person confirms

    def to_dict(self) -> dict:
        return {"id": self.id, "level": self.level, "text": self.text, "evidence": self.evidence,
                "confidence": round(self.confidence, 2), "action": self.action}


@dataclass
class Diagnosis:
    findings: list[Finding] = field(default_factory=list)
    estimates: dict = field(default_factory=dict)
    trim: tuple[float, float] = (1.0, 1.0)         # the multipliers to set (already including any trim that was active during the test)
    side_gain: tuple[float, float] = (1.0, 1.0)    # the twin's motor strengths (left, right), the stronger one = 1.0
    start_duty: tuple[float, float] = (0.0, 0.0)
    yaw_rate_spin_dps: float = 0.0
    usable: bool = False                           # False = too little data: nothing to correct yet

    def get(self, fid: str) -> Finding | None:
        return next((f for f in self.findings if f.id == fid), None)

    @property
    def worst(self) -> str:
        return max((f.level for f in self.findings), key=LEVELS.index, default="ok")

    def to_dict(self) -> dict:
        return {"findings": [f.to_dict() for f in self.findings], "estimates": self.estimates, "trim": list(self.trim),
                "side_gain": list(self.side_gain), "start_duty": list(self.start_duty), "yaw_rate_spin_dps": self.yaw_rate_spin_dps,
                "usable": self.usable, "worst": self.worst}


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _rates(ds: Dataset, name: str) -> list[float]:
    return [float(r["yaw"]) / max(float(r.get("secs") or 0.0), 1e-3) for r in ds.runs.get(name, []) if r.get("yaw") is not None]


def _first_duty(ds: Dataset, name: str, sign: int) -> float | None:
    """The lowest duty at which the wheel really turned (moved the body by >= MIN_MOVE_DEG in the expected direction)."""
    for r in sorted(ds.runs.get(name, []), key=lambda r: r["duty"]):
        if r.get("yaw") is not None and sign * float(r["yaw"]) >= MIN_MOVE_DEG:
            return float(r["duty"])
    return None


def ratio_from_ruler(offset_cm: float, distance_cm: float, track_cm: float = 12.0) -> float:
    """The camera-free fallback: after driving `distance_cm` straight the rover ended `offset_cm` to the LEFT (negative = right) of the line.
    Constant speed and a small angle give offset = d x distance^2 / track, so d = offset x track / distance^2 (d as in the module doc)."""
    return offset_cm * track_cm / max(distance_cm, 1.0) ** 2


def diagnose(ds: Dataset, ruler: dict | None = None) -> Diagnosis:
    out = Diagnosis()
    cfg = ds.meta.get("cfg", {}) or {}
    tl, tr = float(cfg.get("trimL", 1.0)), float(cfg.get("trimR", 1.0))
    est = out.estimates
    add = out.findings.append

    f, b = _rates(ds, "straight_f"), _rates(ds, "straight_b")
    sl, sr = _rates(ds, "spin_l"), _rates(ds, "spin_r")
    single_l, single_r = ds.yaws("single_l"), ds.yaws("single_r")
    rest = ds.yaws("rest")
    noise_deg = abs(_mean(rest)) if rest else 0.0
    est["rest_drift_deg"] = round(noise_deg, 2)

    # ------------------------------------------------------------------ power: did the board reset during the tests?
    resets = int(ds.meta.get("brownouts_end", 0) or 0) - int(ds.meta.get("brownouts_start", 0) or 0)
    if resets > 0:
        add(Finding("power_sag", "fix", f"The board reset {resets} time(s) while the motors started: the supply dips under load. Charge the pack, check the buck output "
                    "while driving (it must stay above 5 V), shorten and thicken the power wires, and keep the camera off the same rail while testing.",
                    {"resets": resets}, 0.9))

    # ------------------------------------------------------------------ is each wheel alive, and the right way round?
    for side, ys, want in (("left", single_l, -1), ("right", single_r, +1)):
        if not ys:
            continue
        m = _mean(ys)
        est[f"single_{side}_deg"] = round(m, 1)
        if abs(m) < max(MIN_MOVE_DEG, 3 * noise_deg):
            add(Finding(f"dead_{side}", "fail", f"The {side} wheel does not move the rover when driven alone (turned {m:+.1f} deg). Check its wires and the driver; "
                        "it may also be too weak to start at this power.", {"deg": round(m, 1)}, 0.9))
        elif m * want < 0:
            add(Finding(f"inverted_{side}", "fix", f"The {side} wheel turns the wrong way (forward made it turn the body {m:+.0f} deg the opposite way). "
                        f"Swap that motor's two wires, or set inv{side[0].upper()}=-1 in the rover's settings.", {"deg": round(m, 1)}, 0.9,
                        {"cfg": {f"inv{side[0].upper()}": -1}}))

    # ------------------------------------------------------------------ do the motors start late (hum)?
    sd = []
    for side, sign in (("l", -1), ("r", +1)):
        d = _first_duty(ds, f"thresh_{side}", sign)
        sd.append(d)
        if ds.runs.get(f"thresh_{side}"):
            est[f"start_duty_{side}"] = d
            name = "left" if side == "l" else "right"
            if d is None:
                add(Finding(f"hum_{name}", "fix", f"The {name} motor never turned the rover up to duty {THRESH_DUTIES_MAX}: it hums or is blocked. Check the supply voltage "
                            "under load, the gearbox and the wiring.", {"start_duty": None}, 0.8))
            elif d > 115:
                add(Finding(f"hum_{name}", "warn", f"The {name} motor only starts at duty {d:.0f} (the firmware's minimum is 90): it needs more push than it gets. "
                            "Check the supply under load.", {"start_duty": d}, 0.8, {"cfg": {"minduty": int(min(d + 10, 160))}}))
    out.start_duty = (sd[0] or 0.0, sd[1] or 0.0) if sd else (0.0, 0.0)

    # ------------------------------------------------------------------ left/right imbalance
    delta = None
    conf = 0.0
    if f and sl and sr:
        spin_rate = (abs(_mean(sl)) + abs(_mean(sr))) / 2.0
        out.yaw_rate_spin_dps = round(spin_rate, 1)
        est["spin_rate_dps"] = round(spin_rate, 1)
        if _mean(sl) < 0 or _mean(sr) > 0:
            add(Finding("gyro_direction", "fix", "A left spin read as a right turn (or the other way round): the gyro sign is reversed, or one motor is wired backwards. "
                        "Run the first-contact turn check, or set gyrosign=-1.", {"spin_l": round(_mean(sl), 1), "spin_r": round(_mean(sr), 1)}, 0.8,
                        {"cfg": {"gyrosign": -1}}))
        mf = _mean(f)
        delta = mf / spin_rate if spin_rate > 1e-6 else 0.0
        est.update(straight_rate_dps=round(mf, 2), straight_std_dps=round(_std(f), 2), delta=round(delta, 4))
        # confidence from three independent checks
        cv = _std(f) / abs(mf) if abs(mf) > 1e-6 else 0.0
        c1 = 1.0 if cv < 0.15 or abs(mf) * 1.0 < 0.5 else (0.7 if cv < 0.35 else 0.4)
        ratio_eff = (1 - delta) / (1 + delta)
        c2 = 0.7
        if single_l and single_r and abs(_mean(single_r)) > 1e-6:
            rho = abs(_mean(single_l)) / abs(_mean(single_r))
            est["single_ratio"] = round(rho, 3)
            err = abs(rho - ratio_eff) / ratio_eff
            c2 = 1.0 if err < 0.08 else (0.7 if err < 0.2 else 0.4)
        c3 = 0.7
        if b:
            mb = _mean(b)
            c3 = 1.0 if (mb * mf <= 0 and abs(abs(mb) - abs(mf)) <= 0.3 * max(abs(mf), 0.5)) or abs(mf) < 0.5 else 0.5
        conf = 0.4 * c1 + 0.4 * c2 + 0.2 * c3
        est["imbalance_confidence_parts"] = {"repeat": round(c1, 2), "one_wheel": round(c2, 2), "backward": round(c3, 2)}
        out.usable = True
    elif ruler:
        delta = ratio_from_ruler(float(ruler["offset_cm"]), float(ruler["distance_cm"]), float(ruler.get("track_cm", 12.0)))
        conf = 0.5
        est.update(delta=round(delta, 4), source="ruler")
        out.usable = True

    if delta is not None:
        ratio_eff = (1 - delta) / (1 + delta)                      # (gL tL) / (gR tR) as it was during the test
        out.side_gain = _normalise((ratio_eff * tr / tl, 1.0))
        boost = (1 + delta) / (1 - delta) if delta > 0 else (1 - delta) / (1 + delta)
        capped = boost > TRIM_CAP
        boost = min(boost, TRIM_CAP)
        new = (tl * boost, tr) if delta > 0 else (tl, tr * boost)
        new = (round(min(new[0], TRIM_CAP), 3), round(min(new[1], TRIM_CAP), 3))
        weak = "left" if delta > 0 else "right"
        pct = abs(delta) * 2 * 100 / (1 + abs(delta))               # how much weaker the weak side is, in per cent of the strong side
        sig = abs(est.get("straight_rate_dps", 0.0)) * (ds.meta.get("move_ms", 1500) / 1000.0) >= MIN_MOVE_DEG or est.get("source") == "ruler"
        if abs(delta) < 0.01 or not sig:
            add(Finding("straight", "ok", "It drives straight within what these tests can measure: no trim is needed.", {"delta": round(delta, 4)}, conf))
            out.trim = (tl, tr)
        else:
            lvl = "fix" if abs(delta) >= 0.03 else "warn"
            text = (f"The {weak} motor delivers about {pct:.0f} % less than the other, so the rover pulls to the {weak}. "
                    f"Trim: give the {weak} motor x{boost:.3f} more duty (settings trimL={new[0]:.3f}, trimR={new[1]:.3f}).")
            if capped:
                text += " That is more than the trim can give (x1.3): check the motor, its gearbox and its wiring first."
                lvl = "fix"
            if conf < 0.6:
                text += " The three measurements do not agree well: repeat on a smoother floor, or measure with a ruler (drive 1 m along a tape line and tell me how far left or right it ended)."
            add(Finding("imbalance", lvl, text, {"delta": round(delta, 4), "weak_side": weak, "weak_pct": round(pct, 1), "straight_deg_s": est.get("straight_rate_dps")},
                        conf, {"cfg": {"trimL": new[0], "trimR": new[1]}}))
            out.trim = new

        if sl and sr and abs(_mean(sl)) + abs(_mean(sr)) > 1e-6:
            asym = abs(abs(_mean(sl)) - abs(_mean(sr))) / ((abs(_mean(sl)) + abs(_mean(sr))) / 2)
            est["spin_asymmetry"] = round(asym, 3)
            if asym > 0.15:
                add(Finding("asymmetric_turns", "warn", f"It turns {'left' if abs(_mean(sl)) > abs(_mean(sr)) else 'right'} {asym * 100:.0f} % faster than the other way: "
                            "usually one tyre grips less, or the floor is uneven. Missions should not assume equal turns.", {"asymmetry": round(asym, 3)}, 0.7))
    if not out.usable:
        add(Finding("not_enough_data", "warn", "There are not enough straight runs and spins to judge the pull yet: run the full test, or measure with a ruler.", {}, 1.0))
    return out


def _normalise(g: tuple[float, float]) -> tuple[float, float]:
    m = max(g) or 1.0
    return (round(g[0] / m, 4), round(g[1] / m, 4))


def explain(d: Diagnosis) -> list[str]:
    """One plain sentence per finding, most serious first."""
    order = sorted(d.findings, key=lambda f: -LEVELS.index(f.level))
    return [f"{f.level.upper()}: {f.text}" + (f" (confidence {f.confidence:.0%})" if f.level in ("warn", "fix") else "") for f in order] or ["No findings."]
