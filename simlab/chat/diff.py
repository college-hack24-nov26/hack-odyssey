"""Draft versus AI: what differs between the plan the person has (the quick draft with their edits on top) and the one the AI made.

Both sides are reduced to a plain snapshot (job numbers, tests, the picked design, assumptions) and compared field by field. A difference the person made themselves is
marked `yours`, so the choice "Use AI, keep my edits" is an informed one.
"""
from __future__ import annotations

from ..llm import menu
from . import words


def snapshot(book, proposals: list[dict], pick: int, summary: str, assumptions: list[str]) -> dict:
    cases = {}
    for c in book.cases:
        if c["kind"] == "sweep":
            cases[c["id"]] = {"title": c["sweep"]["title"], "kind": "sweep", "levels": list(c["sweep"]["levels"]), "ask": c["sweep"]["ask"], "unit": c["sweep"]["unit"],
                              "touched": list(c["touched"])}
        elif c["kind"] == "fixed":
            cases[c["id"]] = {"title": c["title"], "kind": "fixed", "conditions": dict(c["fixed"]["conditions"]), "touched": list(c["touched"])}
        else:
            cases[c["id"]] = {"title": c["title"], "kind": "untestable", "touched": list(c["touched"])}
    p = proposals[pick] if proposals and 0 <= pick < len(proposals) else None
    design = None
    if p:
        design = {"label": p.get("label", ""), "headline": (p.get("verdict") or {}).get("headline", ""), "light": (p.get("verdict") or {}).get("light", ""),
                  "runtime": round(p.get("runtime", 0.0), 1), "mass": round(p.get("mass", 0.0), 2), "parts": list(p.get("parts", []))}
    return {"summary": summary, "params": dict(book.params), "touched_params": sorted(book.touched_params), "cases": cases, "design": design, "assumptions": list(assumptions)}


def _levels(v: dict) -> str:
    return ", ".join(words.num(x) for x in v.get("levels", [])) + (f" {v['unit']}" if v.get("unit") and v["unit"] != "grip" else "")


def _case_text(v: dict) -> str:
    if v["kind"] == "sweep":
        return f"levels {_levels(v)}" + (f", level to cover {words.num(v['ask'])}" if v.get("ask") is not None else "")
    if v["kind"] == "fixed":
        return ", ".join(f"{k} {words.num(x)}" for k, x in v["conditions"].items())
    return "cannot be tested"


def compare(cur: dict, ai: dict) -> dict:
    """{'changes': [{id, area, kind, label, draft, ai, yours}], 'count', 'yours'}. `cur` is the person's plan, `ai` the AI's."""
    changes: list[dict] = []

    def add(area, kind, label, draft, aiv, yours=False):
        changes.append({"id": f"c{len(changes) + 1}", "area": area, "kind": kind, "label": label, "draft": draft, "ai": aiv, "yours": bool(yours)})

    if cur["summary"].strip() != ai["summary"].strip() and ai["summary"].strip():
        add("understanding", "changed", "How the job is described", cur["summary"], ai["summary"])
    for k in sorted(set(cur["params"]) | set(ai["params"])):
        a, b = cur["params"].get(k), ai["params"].get(k)
        if a != b and not (a is None and b is None):
            unit = menu.PARAMS.get(k, (0, 0, "", ""))[2]
            unit = "" if unit == "0/1" else unit
            add("job", "added" if a is None else "removed" if b is None else "changed", words.PARAM_LABEL.get(k, k),
                words.unit_text(a, unit) if a is not None else "not set", words.unit_text(b, unit) if b is not None else "not set", k in cur["touched_params"])
    for cid in list(cur["cases"]) + [c for c in ai["cases"] if c not in cur["cases"]]:
        a, b = cur["cases"].get(cid), ai["cases"].get(cid)
        if a and not b:
            add("tests", "removed", a["title"], _case_text(a), "not in the AI plan", "created" in a["touched"] or bool(a["touched"]))
        elif b and not a:
            add("tests", "added", b["title"], "not in your draft", _case_text(b))
        elif a and b and _case_text(a) != _case_text(b):
            add("tests", "changed", a["title"], _case_text(a), _case_text(b), bool({"levels", "ask"} & set(a["touched"])))
    a, b = cur["design"], ai["design"]
    if a and b:
        if (a["label"], a["headline"]) != (b["label"], b["headline"]) or a["parts"] != b["parts"]:
            add("design", "changed", "The machine", f"{a['label']}: {a['headline']} ({words.num(a['runtime'])} min, {words.num(a['mass'])} kg)",
                f"{b['label']}: {b['headline']} ({words.num(b['runtime'])} min, {words.num(b['mass'])} kg)")
    elif b and not a:
        add("design", "added", "The machine", "none", f"{b['label']}: {b['headline']}")
    for t in cur["assumptions"]:
        if t not in ai["assumptions"]:
            add("assumptions", "removed", t, "assumed", "not assumed")
    for t in ai["assumptions"]:
        if t not in cur["assumptions"]:
            add("assumptions", "added", t, "not assumed", "assumed")
    return {"changes": changes, "count": len(changes), "yours": sum(1 for c in changes if c["yours"])}
