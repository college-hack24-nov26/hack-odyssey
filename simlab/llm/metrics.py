"""Checking what the model asked for. The model may add its own constraints on facts the Builder reports (runtime, mass, thrust-to-weight ...);
this computes each one from the evaluator's numbers, so a requirement is a measurement, not a claim."""
from __future__ import annotations

from . import menu


def check_requirements(reqs: list[dict], facts: dict) -> list[dict]:
    """[{id, text, ok, detail}] in the same shape the designer's own requirement checks use."""
    out = []
    for i, r in enumerate(reqs):
        fact, op, want, unit = r["fact"], r["op"], r["value"], menu.FACTS.get(r["fact"], "")
        have = facts.get(fact)
        if have is None:
            out.append({"id": f"ai_{i}", "text": r["text"], "ok": False, "detail": f"the Builder gives no {fact} for this machine, so this could not be checked"})
            continue
        ok = have >= want if op == ">=" else have <= want
        out.append({"id": f"ai_{i}", "text": r["text"], "ok": bool(ok),
                    "detail": f"{fact} is {have:.3g} {unit}; {'at least' if op == '>=' else 'at most'} {want:g} wanted" + (" (set by you)" if r.get("source") == "user" else " (the AI's inference)")})
    return out
