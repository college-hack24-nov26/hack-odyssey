"""Stress test: push a machine through each hazard from mild to severe and report where it starts to fail.

Every level of a hazard is run on the same scenes (the same seeds), so the only thing that changes between levels is the hazard.
"Starts to fail" means the share of successful runs falls below the catalogue's threshold. The machine is driven by the task's
simple built-in controller: a better controller may do better, a worse one worse. This tests what a catalogue lists, nothing more:
the report always carries the list of what was not tested, and it never says "safe".
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import yaml

from ..control import make_policy
from ..envs import Sim
from ..spec.task import TaskSpec
from ..tasks import get_task_spec
from ..train.evaluate import run_episode, wilson

CATALOGUE = Path(__file__).parent / "catalogue"
STATEMENT = ("Simulator results with a simple built-in controller. They show where this design starts to fail under the conditions "
             "listed above. This is not a guarantee: it says nothing about other conditions, and nothing here has been checked on real hardware.")


def load_catalogue(name: str) -> dict:
    f = CATALOGUE / f"{name}.yaml"
    if not f.is_file():
        raise KeyError(f"no hazard catalogue {name!r}; known: {sorted(p.stem for p in CATALOGUE.glob('*.yaml'))}")
    return yaml.safe_load(f.read_text(encoding="utf-8"))


def _task(cat: dict, overrides: dict) -> TaskSpec:
    d = get_task_spec(cat["task"]).to_dict()
    d["params"] = {**d.get("params", {}), **cat.get("task_params", {}), **overrides}
    return TaskSpec.from_dict(d)


def conditions(cat: dict, ctx: dict) -> list[dict]:
    """Every (hazard, level) and every fault as one runnable condition: the hazards dict for the plant and task parameters."""
    out = []
    for h in cat.get("hazards", []):
        for v in h["levels"]:
            hz, tp = {}, {}
            if "knob" in h:
                hz[h["knob"]] = v * ctx.get(h["scale"], 1.0) if "scale" in h else v
            if "task_param" in h:
                tp[h["task_param"]] = int(v)
            out.append({"kind": "hazard", "id": h["id"], "value": v, "hazards": hz, "task": tp})
    for f in cat.get("faults", []):
        hz = {k: (0.5 if ctx.get("thrusters", 1) >= 2 else 0.0) if v == "auto" else v for k, v in f["hazards"].items()}
        out.append({"kind": "fault", "id": f["id"], "value": None, "hazards": hz, "task": {}})
    return out


def _run_condition(spec, cat: dict, cond: dict, episodes: int, sims: dict, per_seed: bool = False) -> dict:
    key = tuple(sorted(cond["task"].items()))
    if key not in sims:
        sims[key] = Sim(spec, _task(cat, cond["task"]))
    sim = sims[key]
    sim.set_hazards(cond["hazards"])
    pol = make_policy("baseline", sim)
    wins, crashes, timeouts, times, seeds = 0, {}, 0, [], []
    for seed in range(episodes):
        ep = run_episode(sim, pol, seed)
        wins += ep["success"]
        timeouts += ep["timeout"]
        if ep["success"]:
            times.append(ep["t"])
        if ep["crash"]:
            crashes[ep["crash"]] = crashes.get(ep["crash"], 0) + 1
        if per_seed:
            seeds.append({"seed": seed, "success": bool(ep["success"]), "t": round(float(ep["t"]), 2), "crash": ep["crash"], "timeout": bool(ep["timeout"])})
    lo, hi = wilson(wins, episodes)
    out = {"success": wins / episodes, "ci": [lo, hi], "n": episodes, "crashes": crashes, "timeouts": timeouts,
           "mean_time": sum(times) / len(times) if times else None}
    if per_seed:                                                   # only when asked, so the default result is exactly what it always was
        out["per_seed"] = seeds
    return out


def condition_key(c: dict) -> tuple:
    """(kind, id, value) with the value rounded, so a level typed as 0.6 matches the 0.6 in the catalogue."""
    v = c.get("value")
    return (c["kind"], c["id"], None if v is None else round(float(v), 6))


def _wanted(c: dict, subset: set) -> bool:
    k = condition_key(c)
    return k in subset or (k[0], k[1], None) in subset              # (kind, id, None) means every level of that hazard


def run_sweep(spec, cat: dict, ctx: dict, asked: dict | None = None, episodes: int = 5, progress=None, cancel: threading.Event | None = None,
              on_result=None, per_seed: bool = False, subset=None) -> dict:
    """`spec`: the machine's VehicleSpec. `ctx`: numbers the catalogue scales by (max_accel, thrusters). `asked`: what the user asked for
    (wave_hs, wind_ms ...), to compare each hazard's breaking point with. progress(done, total, label) is called after each condition.

    For a chat that shows results as they arrive (defaults give exactly the result above):
    `on_result(cond, result, done, total)` fires once per finished condition, in order, after `progress`; `cond` is the condition dict
    (kind, id, value, hazards, task). `per_seed=True` adds each try's outcome to every result as `per_seed`. `subset` runs only the
    conditions whose (kind, id, value) it lists ((kind, id, None) = every level): the numbers equal those of a full run, because every
    condition starts from the same seeds. Stopping is checked between conditions."""
    t0 = time.time()
    asked = asked or {}
    conds = conditions(cat, ctx)
    if subset is not None:
        wanted = {(k, i, None if v is None else round(float(v), 6)) for k, i, v in subset}
        conds = [c for c in conds if _wanted(c, wanted)]
    sims: dict = {}
    results: dict[tuple, dict] = {}
    partial = False
    try:
        for i, c in enumerate(conds):
            if cancel is not None and cancel.is_set():
                partial = True
                break
            try:
                r = _run_condition(spec, cat, c, episodes, sims, per_seed)
            except Exception as e:                                  # one broken condition must not lose the rest of the sweep
                r = {"success": 0.0, "ci": [0.0, 0.0], "n": 0, "crashes": {}, "timeouts": 0, "mean_time": None, "error": f"{type(e).__name__}: {e}"}
            results[(c["kind"], c["id"], c["value"])] = r
            if progress:
                progress(i + 1, len(conds), f"{c['id']} {c['value'] if c['value'] is not None else ''}".strip())
            if on_result:
                on_result(c, r, i + 1, len(conds))
    finally:
        for s in sims.values():
            s.close()
    thr = float(cat.get("threshold", 0.8))
    hazards = [_hazard(h, results, thr, asked) for h in cat.get("hazards", [])]
    faults = []
    for f in cat.get("faults", []):
        r = results.get(("fault", f["id"], None))
        if r:
            faults.append({"id": f["id"], "title": f["title"], "plain": f["plain"], "fix": f.get("fix", ""), **r, "ok": r["success"] >= thr})
    return {"platform": cat["platform"], "task": cat["task"], "threshold": thr, "episodes": episodes, "baseline": cat.get("baseline", ""),
            "hazards": hazards, "faults": faults, "weakest": _weakest(hazards, faults), "not_tested": list(cat.get("not_tested", [])),
            "partial": partial, "seconds": round(time.time() - t0, 1), "statement": STATEMENT}


def _hazard(h: dict, results: dict, thr: float, asked: dict) -> dict:
    levels = []
    for v in h["levels"]:
        r = results.get(("hazard", h["id"], v))
        if r:
            levels.append({"value": v, **r})
    out = {"id": h["id"], "title": h["title"], "plain": h["plain"], "unit": h.get("unit", ""), "source": h.get("source", ""),
           "fix": h.get("fix", ""), "levels": levels, "breaks_at": None, "last_ok": None, "asked": None, "meets_ask": None}
    if not levels:
        out["summary"] = "Not run."
        return out
    if levels[0]["success"] < thr:
        out["summary"] = f"Fails even with none of this ({levels[0]['success']:.0%} success), so this test says nothing about it."
        out["reference_fails"] = True
        return out
    for i, lv in enumerate(levels):
        if lv["success"] < thr:
            out["breaks_at"], out["last_ok"] = lv["value"], levels[i - 1]["value"]
            break
    unit = h.get("unit", "")
    want = asked.get(h.get("ask", ""))
    if want is not None:
        above = [lv["value"] for lv in levels if lv["value"] >= want]
        upto = min(above) if above else levels[-1]["value"]
        out["asked"] = want
        out["meets_ask"] = all(lv["success"] >= thr for lv in levels if lv["value"] <= upto)
    if out["breaks_at"] is None:
        out["summary"] = f"Handled every level tested, up to {levels[-1]['value']:g} {unit}.".replace("  ", " ")
    else:
        out["summary"] = f"Fine up to {out['last_ok']:g} {unit}, starts to fail at {out['breaks_at']:g} {unit}.".replace("  ", " ")
    if out["meets_ask"] is not None:
        out["summary"] += f" You asked for {want:g} {unit}: " + ("covered." if out["meets_ask"] else "NOT covered.")
    return out


def _weakest(hazards: list[dict], faults: list[dict]) -> dict | None:
    missed = [h for h in hazards if h.get("meets_ask") is False]
    if missed:
        h = min(missed, key=lambda h: (h["breaks_at"] or 0.0) / max(h["asked"] or 1e-9, 1e-9))
        return {"id": h["id"], "title": h["title"], "why": h["summary"], "fix": h["fix"]}
    bad = [f for f in faults if not f["ok"]]
    if bad:
        f = min(bad, key=lambda f: f["success"])
        return {"id": f["id"], "title": f["title"], "why": f"{f['title']}: the job succeeds in {f['success']:.0%} of runs.", "fix": f["fix"]}
    breaking = [h for h in hazards if h["breaks_at"] is not None]
    if breaking:
        h = min(breaking, key=lambda h: h["breaks_at"] / max(h["levels"][-1]["value"], 1e-9))
        return {"id": h["id"], "title": h["title"], "why": h["summary"], "fix": h["fix"]}
    return None
