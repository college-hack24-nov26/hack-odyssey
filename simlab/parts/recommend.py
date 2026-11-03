"""Turn a finding's fix hint into concrete part suggestions, each one tried on the build before it is offered.

A candidate is kept only if applying it makes the finding go away (fewer findings of that id at that severity or worse) and does
not introduce a new error. Candidates are ranked: parts you own first, then fewest remaining errors and warnings.
"""
from __future__ import annotations

import copy
from typing import Callable

from .library import all_parts, load_inventory
from .schema import Part

PATH_FOR_KIND = {"regulator": "power.regulator", "motor_dc": "drive.motor", "wheel": "drive.wheel", "driver_dc": "drive.driver",
                 "battery": "power.battery", "mcu": "controller", "frame": "chassis.frame", "propulsor_air": "drive.propulsor", "esc": "drive.esc",
                 "hull": "chassis.hull", "thruster_water": "drive.thruster"}
RANK = {"error": 0, "warn": 1, "info": 2}


def _set(d: dict, path: str, value) -> None:
    *head, last = path.split(".")
    for k in head:
        d = d[k]
    d[last] = value


def _mount(kind: str, b: dict) -> dict:
    h, length, wr = b["chassis"]["height"], b["chassis"]["length"], 0.03
    if kind == "sensor_camera":
        return {"forward": round(0.4 * length, 3), "height": round(wr + 0.8 * h, 3), "tilt_deg": -10.0}
    if kind == "sensor_range":
        return {"forward": round(0.45 * length, 3), "height": round(wr + 0.5 * h, 3), "tilt_deg": 0.0}
    return {"forward": 0.0, "height": round(wr + h, 3), "tilt_deg": 0.0}


def make_patch(build: dict, part: Part) -> dict | None:
    """How to add or swap in `part`: {"set": path, "value": v} or {"append": "sensors", "value": {...}}."""
    if part.kind in PATH_FOR_KIND:
        return {"set": PATH_FOR_KIND[part.kind], "value": part.name}
    if part.kind == "lamp":
        return {"set": "lamp", "value": {"part": part.name, "count": 1}}
    if part.kind.startswith("sensor_"):
        taken = {s["name"] for s in build.get("sensors", [])}
        stem = part.kind.split("_", 1)[1]
        n = 1
        while f"{stem}{n}" in taken:
            n += 1
        return {"append": "sensors", "value": {"part": part.name, "name": f"{stem}{n}", "mount": _mount(part.kind, build)}}
    return None


def apply_patch(build: dict, patch: dict) -> dict:
    out = copy.deepcopy(build)
    if "set" in patch:
        _set(out, patch["set"], copy.deepcopy(patch["value"]))
    else:
        out.setdefault(patch["append"], []).append(copy.deepcopy(patch["value"]))
    return out


def _matches(part: Part, constraints: dict) -> bool:
    for key, want in (constraints or {}).items():
        have = part.params.get(key)
        if have is None:
            return False
        if isinstance(want, (list, tuple)):
            lo, hi = (list(want) + [None, None])[:2]
            if (lo is not None and have < lo) or (hi is not None and have > hi):
                return False
        elif have != want:
            return False
    return True


def _count(findings: list[dict], fid: str, worst: int) -> int:
    return sum(1 for f in findings if f["id"] == fid and RANK[f["severity"]] <= worst)


def candidates(build: dict, finding: dict, evaluate_fn: Callable[[dict], list[dict]], limit: int = 4) -> list[dict]:
    """Suggestions for one finding. `evaluate_fn(build_dict)` returns that build's findings as dicts (including task findings),
    or None when the build does not make a valid vehicle."""
    fix = finding.get("fix") or {}
    kind = fix.get("kind")
    if fix.get("action") not in ("add_part", "change_part") or not kind:
        return []
    owned = load_inventory()
    before = evaluate_fn(build) or []
    worst = RANK[finding["severity"]]
    n_before = _count(before, finding["id"], worst)
    errors_before = {f["id"] for f in before if f["severity"] == "error"}
    out = []
    for part in all_parts().values():
        if part.kind != kind or not _matches(part, fix.get("constraints")):
            continue
        patch = make_patch(build, part)
        if patch is None:
            continue
        if "set" in patch and _current(build, patch) == part.name:
            continue                                                                       # it is already in the build
        after = evaluate_fn(apply_patch(build, patch))
        if after is None:                                                                 # (an empty list is a clean build, the best outcome)
            continue
        new_errors = {f["id"] for f in after if f["severity"] == "error"} - errors_before
        if _count(after, finding["id"], worst) >= n_before or new_errors:
            continue
        out.append({"part": part.name, "title": part.title, "confidence": part.confidence, "owned": owned.get(part.name, 0), "patch": patch,
                    "errors": sum(f["severity"] == "error" for f in after), "warnings": sum(f["severity"] == "warn" for f in after)})
    out.sort(key=lambda c: (c["owned"] == 0, c["errors"], c["warnings"], c["part"]))
    return out[:limit]


def _current(build: dict, patch: dict):
    cur = build
    for k in patch["set"].split("."):
        cur = cur.get(k) if isinstance(cur, dict) else None
    return cur.get("part") if isinstance(cur, dict) else cur
