"""Turn a validated stress plan into the catalogue dict that run_sweep() runs. Code, not the model, writes the parts that keep the report honest:
the threshold, the controller note, the not-tested list and the statement."""
from __future__ import annotations

from ..llm import menu


def synthesize(plan: dict, platform: str) -> tuple[dict, dict]:
    """(catalogue, asked): `catalogue` for run_sweep(cat), `asked` the values the sweep compares each hazard's breaking point with."""
    m = menu.load(platform)
    asked, hazards = {}, []
    wanted = plan["stress"]["hazards"] or [{"id": h, "levels": m["hazards"][h]["levels"], "ask": None} for h in list(m["hazards"])[:3]]
    for h in wanted:
        base = dict(m["hazards"][h["id"]])
        base["levels"] = list(h["levels"])
        base.pop("range", None)
        value = h.get("ask")
        if value is None and base.get("ask"):
            value = plan.get("params", {}).get(base["ask"])
        if value is not None:
            key = f"ask_{base['id']}"
            asked[key] = value
            base["ask"] = key
        else:
            base.pop("ask", None)
        hazards.append(base)
    fault_ids = plan["stress"]["faults"] or list(m["faults"])[:2]
    faults = [m["faults"][f] for f in fault_ids if f in m["faults"]]
    skipped = [f"The AI thought of this but the simulator cannot test it: {s['name']}" + (f" ({s['why']})" if s["why"] else "")
               for s in plan.get("scenarios", []) if not s["hazard"]]
    task = plan.get("task") or {"preset": m["task"], "params": {}}
    cat = {"id": "plan", "platform": platform, "task": task["preset"], "task_params": dict(task.get("params", {})), "threshold": m["threshold"],
           "baseline": m["baseline"], "hazards": hazards, "faults": faults, "not_tested": skipped + m["not_tested"]}
    return cat, asked
