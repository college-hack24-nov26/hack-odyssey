"""The closed menu a model may choose from: which hazards and faults exist per platform, what ranges they accept, which facts it may constrain,
which numbers describe a job, and which task presets suit a platform. Anything outside this menu is dropped by the validator, so the model can
decide *among* what the simulator can really do, and cannot invent a tool.
"""
from __future__ import annotations

import yaml

from ..hazards.sweep import CATALOGUE

PLATFORMS = ("ground", "air", "water")
CATALOGUE_OF = {"ground": "sewer", "air": "air", "water": "water"}
KIND_OF = {"ground": "ground_wheeled", "air": "multirotor", "water": "boat"}

# physical range each knob accepts (the plants do not clamp, so we do)
RANGES = {"grip_mu": (0.02, 0.9), "drag_ms2": (0.0, 0.97), "cmd_delay_s": (0.0, 2.0), "cmd_dropout": (0.0, 0.9), "wind_ms": (0.0, 25.0),
          "wave_hs": (0.0, 1.5), "current_ms": (0.0, 4.0), "debris": (0.0, 60.0)}

# the numbers that describe a job (the deterministic requirement formulas in goals/design.py read these keys) and their allowed range
PARAMS = {"pipe_d": (0.1, 3.0, "m", "narrowest pipe, gap or row width the machine must fit through (ground)"),
          "distance_m": (1.0, 5000.0, "m", "how far one way it must go (ground)"),
          "wet": (0.0, 1.0, "0/1", "1 if the floor is wet or slippery (ground)"),
          "minutes": (1.0, 600.0, "min", "how long it must stay up or out (air, water)"),
          "wind_ms": (0.0, 30.0, "m/s", "worst wind it must handle (air, water)"),
          "wave_hs": (0.0, 2.0, "m", "worst significant wave height it must handle (water)"),
          "range_m": (5.0, 5000.0, "m", "how far out, one way (water)"),
          "payload_kg": (0.0, 20.0, "kg", "extra load it must carry (air, water)")}
PARAMS_OF = {"ground": ("pipe_d", "distance_m", "wet"), "air": ("minutes", "wind_ms", "payload_kg"),
             "water": ("range_m", "minutes", "wave_hs", "wind_ms", "payload_kg")}

# facts the Builder's evaluator reports, which a requirement may constrain
FACTS = {"top_speed": "m/s", "accel": "m/s^2", "turn_rate": "rad/s", "climb": "deg", "runtime": "min", "mass": "kg", "twr": "ratio",
         "thrust": "N", "freeboard": "m", "draft": "m", "tilt": "deg", "bollard": "ratio", "cruise_current": "A", "stall_current": "A",
         "peak_current": "A", "pack_voltage": "V", "hover_thrust": "%"}

# task presets worth offering, per platform, and which of their parameters a plan may set
TASK_PARAMS = {"water_rescue": {"range_m": (5.0, 5000.0), "debris": (0.0, 60.0)}}


def task_presets(platform: str) -> list[str]:
    from ..registry import TASKS
    from ..tasks import get_task_spec, task_names
    out = []
    for n in task_names():
        cls = TASKS.get(get_task_spec(n).task)
        if (not cls.kinds or KIND_OF[platform] in cls.kinds) and "webots" not in cls.backends[:1] and n not in ("sewer_straight", "sewer_star"):
            out.append(n)
    return out


def load(platform: str) -> dict:
    """The hazards, faults and not-tested notes the catalogue defines for a platform, with ranges added."""
    d = yaml.safe_load((CATALOGUE / f"{CATALOGUE_OF[platform]}.yaml").read_text(encoding="utf-8"))
    hazards = {}
    for h in d.get("hazards", []):
        knob = h.get("knob") or h.get("task_param")
        lo, hi = RANGES.get(knob, (min(h["levels"]), max(h["levels"])))
        hazards[h["id"]] = {**h, "range": (lo, hi)}
    faults = {f["id"]: f for f in d.get("faults", [])}
    return {"platform": platform, "task": d["task"], "hazards": hazards, "faults": faults, "not_tested": list(d.get("not_tested", [])),
            "threshold": float(d.get("threshold", 0.8)), "baseline": d.get("baseline", "")}
