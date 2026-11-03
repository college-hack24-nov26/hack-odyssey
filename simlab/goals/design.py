"""Goal -> machine: search the verified parts library for builds that meet what the answers ask for, and explain the result.

Not a language model and not a guess: every candidate is a complete build that the Builder's evaluator has checked (electrical,
mechanical, thrust, fit) and that is then tested against the goal's own requirements. A staged search keeps it fast (a few
hundred evaluations, about 10 ms each). When nothing in the library meets the goal, the answer says so and shows the closest design.
"""
from __future__ import annotations

import copy
import math
from itertools import product

from ..parts.evaluate import evaluate
from ..parts.library import load_inventory, parts_by_kind
from ..parts.templates import example_builds
from .catalogue import Goal, get_goal
from .plain import finding_line, verdict

LABELS = ("Best match", "Longest time out", "Lightest")


# ---------------------------------------------------------------- what the answers mean, per platform
def _ground_build(base: dict, params: dict, owned: dict) -> dict:
    b = copy.deepcopy(base)
    d = float(params.get("pipe_d", 0.9))
    width = max(0.07, min(0.14, 0.42 * d))
    b["chassis"].update(width=round(width, 3), length=round(1.3 * width, 3), height=round(min(0.06, 0.35 * d), 3),
                        frame_mass_kg=round(0.12 * width / 0.12, 3))
    cams = [p.name for p in parts_by_kind().get("sensor_camera", []) if owned.get(p.name)]
    if cams and b.get("sensors"):
        b["sensors"][0]["part"] = cams[0]                      # the camera you already own
    b["name"] = "proposal"
    return b


def _variants_ground(params: dict, owned: dict):
    base = _ground_build(example_builds()["esp32_pipe_rover"], params, owned)
    motors = [p.name for p in parts_by_kind().get("motor_dc", [])]
    wheels = ["wheel_43mm", "wheel_65mm_tt", "wheel_70mm_pololu"]
    drivers = [p.name for p in parts_by_kind().get("driver_dc", [])]
    batts = [("aa_nimh_2000", 4, 1), ("aa_nimh_2000", 4, 2)]
    for m, w, dr, (bt, s, pa) in product(motors, wheels, drivers, batts):
        b = copy.deepcopy(base)
        b["drive"].update(motor=m, wheel=w, driver=dr)
        b["power"].update(battery=bt, series=s, parallel=pa)
        yield b


def _variants_air(params: dict, owned: dict):
    base = copy.deepcopy(example_builds()["quad_5in_racer"])
    base["name"] = "proposal"
    base["chassis"]["extra_mass_kg"] = float(params.get("payload_kg", 0.0))
    frames = [p.name for p in parts_by_kind().get("frame", [])]
    props = [p.name for p in parts_by_kind().get("propulsor_air", [])]
    batts = [("lipo_cell_1500_75c", 4, 1), ("lipo_cell_2200_30c", 3, 1), ("lipo_cell_2200_30c", 4, 1), ("lipo_cell_1000_25c", 4, 1),
             ("lipo_cell_2200_30c", 3, 2), ("lipo_cell_2200_30c", 4, 2), ("lipo_cell_2200_30c", 4, 3), ("lipo_cell_2200_30c", 4, 4)]
    for f, pr, (bt, s, pa), esc in product(frames, props, batts, ("esc_30a", "esc_40a", "esc_60a")):
        b = copy.deepcopy(base)
        b["chassis"]["frame"] = f
        b["drive"].update(propulsor=pr, esc=esc)
        b["power"].update(battery=bt, series=s, parallel=pa)
        yield b


def _variants_water(params: dict, owned: dict):
    base = copy.deepcopy(example_builds()["small_boat"])
    base["name"] = "proposal"
    base["chassis"]["ballast_kg"] = 4.0 + float(params.get("payload_kg", 0.0))
    for h, t, n, par in product(("hull_box_1m2", "hull_box_3m"), ("thruster_small_12v",), (1, 2), (1, 2, 4)):
        b = copy.deepcopy(base)
        b["chassis"]["hull"] = h
        b["drive"].update(thruster=t, count=n)
        b["power"].update(series=1, parallel=par)
        yield b


VARIANTS = {"ground": _variants_ground, "air": _variants_air, "water": _variants_water}


# ---------------------------------------------------------------- requirements: what "does the job" means, in numbers
def _check(rid: str, text: str, ok: bool, detail: str) -> dict:
    return {"id": rid, "text": text, "ok": bool(ok), "detail": detail}


def requirements(goal: Goal, params: dict, build: dict, r: dict, facts: dict) -> list[dict]:
    """The physics checks for a job. A check applies only when the job has the number it needs (a farm rover has no pipe width)."""
    out = []
    speed, runtime = facts.get("top_speed", 0.0), facts.get("runtime", 0.0)           # m/s, minutes
    if goal.platform == "ground":
        if "pipe_d" in params:
            from ..spec.vehicle import VehicleSpec
            from sewerbot.core.geometry import Section
            from sewerbot.core.profiles import profile_from_spec
            d = float(params["pipe_d"])
            try:
                fit = profile_from_spec(VehicleSpec.from_dict(r["vehicle"])).fits(Section.make("circular", d))
            except ValueError as e:                              # e.g. faster than the pipe pipeline allows
                fit = {"fits": False, "can_turn": False, "reason": str(e)}
            note = "" if not fit["fits"] else ("" if fit["can_turn"] else "; it cannot turn round inside the pipe, so it backs out")
            out.append(_check("fit", f"Fits {d * 100:.0f} cm pipes", fit["fits"],
                              f"it is {build['chassis']['width'] * 100:.0f} cm wide" + (f": {fit['reason']}" if not fit["fits"] else note)))
        if "distance_m" in params:
            dist = float(params["distance_m"])
            need = dist / max(0.6 * speed, 1e-6) / 60.0 * 1.5 + 1.0
            out.append(_check("range", f"Goes {dist:.0f} m and back on one battery", runtime >= need and speed >= 0.05,
                              f"about {runtime:.0f} min of driving at {speed:.2f} m/s top speed; {need:.0f} min needed with a margin"))
        if params.get("wet"):
            out.append(_check("sludge", "Enough pulling power for wet, slippery ground", facts.get("climb", 0.0) >= 20.0,
                              f"it can climb about {facts.get('climb', 0.0):.0f} degrees; 20 needed as a margin"))
    elif goal.platform == "air":
        if "minutes" in params:
            mins = float(params["minutes"])
            out.append(_check("time", f"Stays up about {mins:.0f} minutes", runtime >= 1.2 * mins,
                              f"about {runtime:.0f} min of flight; {1.2 * mins:.0f} needed with a margin"))
        if "wind_ms" in params:
            wind = float(params["wind_ms"])
            body = r["vehicle"]["body"]
            area = (body["length"] + 0.1) * (body["height"] + 0.04)
            tilt = math.atan(0.5 * 1.2 * area * wind ** 2 / max(body["mass"] * 9.81, 1e-6))
            twr_need = 1.3 / math.cos(tilt)
            out.append(_check("wind", f"Holds its place in about {wind:.0f} m/s wind", facts.get("twr", 0.0) >= twr_need,
                              f"thrust is {facts.get('twr', 0.0):.1f}x its weight; about {twr_need:.1f}x needed (it must lean {math.degrees(tilt):.0f} degrees)"))
    else:
        parts = build["drive"].get("count", 1)
        hs = float(params.get("wave_hs", 0.0))
        if "minutes" in params:
            mins = float(params["minutes"])
            out.append(_check("time", f"Stays out about {mins:.0f} minutes", runtime >= mins, f"about {runtime:.0f} min at full power, more at working speed"))
        if "range_m" in params:
            rng = float(params["range_m"])
            go_back = 2 * rng / max(0.7 * speed, 1e-6) / 60.0
            out.append(_check("range", f"Reaches {rng:.0f} m and comes back", speed >= 1.0 and go_back <= 0.8 * runtime,
                              f"top speed {speed:.1f} m/s; the round trip takes about {go_back:.0f} min"))
        if "wave_hs" in params:
            out.append(_check("waves", f"Stays above water in {hs:.1f} m waves", facts.get("freeboard", 0.0) >= 0.7 * hs,
                              f"{facts.get('freeboard', 0.0) * 100:.0f} cm of hull above the water; about {0.7 * hs * 100:.0f} cm advised (a loose rule of thumb: the stress test checks it)"))
        if "wind_ms" in params:
            wind = float(params["wind_ms"])
            area = r["vehicle"]["body"]["length"] * max(facts.get("freeboard", 0.0), 0.05) * 1.3                 # the part of the hull the wind pushes on
            push = 0.5 * 1.2 * area * wind ** 2
            out.append(_check("wind", f"Can push against {wind:.0f} m/s wind", facts.get("thrust", 0.0) >= 1.3 * push,
                              f"the wind pushes with about {push:.0f} N on this hull; the thruster gives {facts.get('thrust', 0.0):.0f} N (1.3x needed)"))
        if hs >= 0.6:
            out.append(_check("spare", "A second thruster, in case one fails", parts >= 2,
                              "rescue-boat rules ask for two independent propulsion systems on boats that go offshore"))
    return out


# ---------------------------------------------------------------- the search
def part_names(b: dict) -> list[str]:
    """Every library part a build uses, once each."""
    names = []
    for sec in ("chassis", "drive", "power"):
        for k, v in b.get(sec, {}).items():
            if isinstance(v, str) and k in ("frame", "hull", "motor", "wheel", "driver", "propulsor", "esc", "thruster", "battery", "regulator"):
                names.append(v)
    names.append(b.get("controller", ""))
    names += [s.get("part", "") for s in b.get("sensors", [])]
    if b.get("lamp", {}).get("part"):
        names.append(b["lamp"]["part"])
    return [n for n in dict.fromkeys(names) if n]


def _facts(r: dict) -> dict:
    return {f["key"]: f["value"] for f in r["facts"] if isinstance(f["value"], (int, float))}


def _entry(goal: Goal, params: dict, b: dict, r: dict, owned: dict, task_r: dict | None) -> dict:
    facts = _facts(r)
    reqs = requirements(goal, params, b, r, facts)
    res = task_r or r
    warns = [f for f in res["findings"] if f["severity"] == "warn"]
    unmet = [c for c in reqs if not c["ok"]]
    names = part_names(b)
    have = [n for n in names if owned.get(n)]
    return {"build": b, "facts": facts, "checks": reqs, "unmet": len(unmet), "warns": len(warns), "task_ok": bool(res["task_ok"]),
            "findings": [finding_line(f) for f in res["findings"] if f["severity"] in ("error", "warn")][:6],
            "parts": names, "owned": have, "to_buy": [n for n in names if n not in have], "mass": facts.get("mass", 0.0),
            "runtime": facts.get("runtime", 0.0), "vehicle": r["vehicle"]}


def propose(goal_id: str, answers: dict | None = None, owned: dict | None = None, limit: int = 3, params: dict | None = None) -> dict:
    """The goal's proposals for these answers: up to `limit` different, fully checked builds, best first.
    `params` replaces the answers with numbers the caller already has (the chat edits job numbers freely, not as choices)."""
    goal = get_goal(goal_id)
    params = dict(params) if params is not None else goal.params(answers)
    owned = load_inventory() if owned is None else owned
    scanned, feasible = 0, []
    closest: list[dict] = []
    for b in VARIANTS[goal.platform](params, owned):
        scanned += 1
        r = evaluate(b, None, suggest=False)
        if r["vehicle"] is None or not r["ok"]:
            continue
        e = _entry(goal, params, b, r, owned, None)
        (feasible if not e["unmet"] else closest).append(e)
    pool = feasible
    if pool:
        pool.sort(key=lambda e: (-len(e["owned"]), e["warns"], e["mass"]))
        shortlist = pool[:8] + sorted(pool, key=lambda e: -e["runtime"])[:3] + sorted(pool, key=lambda e: e["mass"])[:3]
        seen, finals = set(), []
        for e in shortlist:                                                         # re-check the finalists against the goal's own task
            key = repr(e["build"])
            if key in seen:
                continue
            seen.add(key)
            tr = evaluate(e["build"], goal.task, suggest=False)
            finals.append(_entry(goal, params, e["build"], evaluate(e["build"], None, suggest=False), owned, tr))
        finals = [e for e in finals if e["task_ok"] and not e["unmet"]] or finals
        best = sorted(finals, key=lambda e: (e["unmet"], -len(e["owned"]), e["warns"], e["mass"]))[0]
        picks: list[tuple[str, dict]] = [(LABELS[0], best)]
        longest = max(finals, key=lambda e: e["runtime"])
        if longest["runtime"] > 1.15 * best["runtime"] and not longest["unmet"]:         # only offered when it is clearly different
            picks.append((LABELS[1], longest))
        light = min(finals, key=lambda e: e["mass"])
        if light["mass"] < 0.85 * best["mass"] and not light["unmet"] and all(light["build"] != p["build"] for _, p in picks):
            picks.append((LABELS[2], light))
        picks = picks[:limit]
    else:
        closest.sort(key=lambda e: (e["unmet"], -e["runtime"], -len(e["owned"]), e["warns"]))
        picks = [("Closest we found", e) for e in closest[:1]]
    proposals = []
    for label, e in picks:
        tr = evaluate(e["build"], goal.task, suggest=False)
        e = {**e, "label": label, "verdict": verdict(e["checks"], tr["findings"], goal)}
        proposals.append(e)
    return {"goal": goal.to_dict(), "answers": dict(answers or {}), "params": params, "proposals": proposals,
            "scanned": scanned, "meets_goal": bool(feasible),
            "note": ("" if feasible else "Nothing in the parts library meets all of this. The closest design is shown with what it misses: "
                     "add the part you would use (Builder > Parts library) or ask for less.")}


# ---------------------------------------------------------------- planning from a model's designs
def evaluate_designs(goal: Goal, params: dict, designs: list[dict], extra: list[dict], owned: dict) -> tuple[list[dict], list[str]]:
    """Check each design a model proposed with the Builder and the job's requirements. Returns (entries, problems): `problems` is what a second
    call to the model should be told so it can fix the design."""
    from ..llm.metrics import check_requirements
    entries, problems = [], []
    for d in designs:
        b = d["build"]
        r = evaluate(b, None, suggest=False)
        label = d.get("label", "design")
        if r["vehicle"] is None or not r["ok"]:
            errs = [f["title"] + ": " + f["detail"][:140] for f in r["findings"] if f["severity"] == "error" and f["scope"] == "build"][:4]
            problems.append(f"{label}: the Builder refuses it: " + ("; ".join(errs) or "it is not a valid build"))
            continue
        tr = evaluate(b, goal.task, suggest=False)
        e = _entry(goal, params, b, r, owned, tr)
        e["checks"] = e["checks"] + check_requirements(extra, e["facts"])
        e["unmet"] = sum(1 for c in e["checks"] if not c["ok"])
        e["label"], e["why"], e["from_ai"] = label, d.get("why", ""), True
        if e["unmet"]:
            problems.append(f"{label}: does not meet: " + "; ".join(f"{c['text']} ({c['detail']})" for c in e["checks"] if not c["ok"])[:500])
        entries.append(e)
    return entries, problems


def search_alternatives(goal: Goal, params: dict, extra: list[dict], owned: dict) -> tuple[list[dict], int]:
    """The deterministic grid search under the same requirements: guarantees a verified machine when the model's designs fail."""
    from ..llm.metrics import check_requirements
    scanned, feasible, closest = 0, [], []
    for b in VARIANTS[goal.platform](params, owned):
        scanned += 1
        r = evaluate(b, None, suggest=False)
        if r["vehicle"] is None or not r["ok"]:
            continue
        e = _entry(goal, params, b, r, owned, None)
        e["checks"] = e["checks"] + check_requirements(extra, e["facts"])
        e["unmet"] = sum(1 for c in e["checks"] if not c["ok"])
        (feasible if not e["unmet"] else closest).append(e)
    if feasible:
        feasible.sort(key=lambda e: (-len(e["owned"]), e["warns"], e["mass"]))
        return feasible[:3], scanned
    closest.sort(key=lambda e: (e["unmet"], -e["runtime"], -len(e["owned"]), e["warns"]))
    return closest[:1], scanned


def finish_plan(goal: Goal, params: dict, ai: list[dict], grid: list[dict], scanned: int) -> dict:
    """Pick up to three entries, label them, add verdicts. AI designs that meet the job come first; the grid's best is added when it is different."""
    good = [e for e in ai if not e["unmet"]]
    picks: list[tuple[str, dict]] = [(f"{e['label']} (AI)", e) for e in sorted(good, key=lambda e: (-len(e["owned"]), e["warns"], e["mass"]))][:2]
    for e in grid:
        if len(picks) >= 3 or any(e["build"] == p["build"] for _, p in picks):
            continue
        picks.append(("Verified alternative" if (good or not e["unmet"]) else "Closest we found", e))
    if not picks and ai:
        best = sorted(ai, key=lambda e: (e["unmet"], e["warns"]))[0]
        picks = [("Closest we found", best)]
    proposals = []
    for label, e in picks:
        tr = evaluate(e["build"], goal.task, suggest=False)
        proposals.append({**e, "label": label, "verdict": verdict(e["checks"], tr["findings"], goal)})
    meets = any(not p["unmet"] for p in proposals)
    return {"proposals": proposals, "scanned": scanned, "meets_goal": meets,
            "note": "" if meets else "Nothing in the parts library meets all of this. The closest design is shown with what it misses: add the part you would use (Builder > Parts library) or ask for less."}
