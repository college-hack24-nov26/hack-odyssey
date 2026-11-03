"""The stage tracker: requirements -> blueprint -> parts -> environment -> simulate -> connect -> test -> gaps -> fix -> deploy.

Stages that leave a data trail are DERIVED from the project (a blueprint exists and the Builder accepts it, parts are confirmed, an environment is named, a simulated run is
recorded, a real run on the hardware is recorded). The ones that do not (connect, gaps, fix, deploy) are marked explicitly, and only when every
earlier stage is done, so the tracker cannot be ticked out of order. A real run recorded on the synthetic rover does not count as a test.
A non-moving device (moving=False) skips connect, test, gaps and fix: it goes requirements to simulate, then deploy.
"""
from __future__ import annotations

import time

STAGES = ("requirements", "blueprint", "parts", "environment", "simulate", "connect", "test", "gaps", "fix", "deploy")
MARKABLE = ("connect", "gaps", "fix", "deploy")                 # no data trail of their own
MOVING_ONLY = ("connect", "test", "gaps", "fix")


class StageError(ValueError):
    pass


def _has_run(runs: list[dict], kind: str, hardware_only: bool = False) -> bool:
    return any(r.get("kind") == kind and not (hardware_only and r.get("link") == "synthetic") for r in runs)


def done_map(p: dict, runs: list[dict]) -> dict[str, bool]:
    marks, req = p.get("marks", {}), p.get("requirements") or {}
    return {"requirements": bool(req.get("text") or req.get("items")), "blueprint": bool((p.get("blueprint") or {}).get("check", {}).get("ok")), "parts": bool(p.get("parts_confirmed")),
            "environment": bool(p.get("environment")), "simulate": _has_run(runs, "sim"), "connect": "connect" in marks,
            "test": _has_run(runs, "real", hardware_only=True), "gaps": "gaps" in marks, "fix": "fix" in marks, "deploy": "deploy" in marks}


def tracker(p: dict, runs: list[dict]) -> dict:
    """{"stages": [{id, state, at?, note?}], "current": id or None}. state: done | current | todo | skipped."""
    done, marks = done_map(p, runs), p.get("marks", {})
    skipped = set() if p.get("moving", True) else set(MOVING_ONLY)
    out, current = [], None
    for s in STAGES:
        if s in skipped:
            st = "skipped"
        elif done[s]:
            st = "done"
        elif current is None:
            st, current = "current", s
        else:
            st = "todo"
        row = {"id": s, "state": st}
        if s in marks:
            row.update(at=marks[s].get("at", 0), note=marks[s].get("note", ""))
        out.append(row)
    return {"stages": out, "current": current}


def mark(p: dict, runs: list[dict], stage: str, note: str = "", now: float | None = None) -> None:
    """Mark connect / gaps / fix / deploy as done. Refused for any other stage, for a stage skipped by this project, and while an earlier stage is not done."""
    if stage not in MARKABLE:
        raise StageError(f"{stage!r} cannot be marked by hand; it is set by the work itself (markable: {', '.join(MARKABLE)})")
    if not p.get("moving", True) and stage in MOVING_ONLY:
        raise StageError(f"{stage} does not apply to a device that does not move")
    done = done_map(p, runs)
    skipped = set() if p.get("moving", True) else set(MOVING_ONLY)
    for s in STAGES[:STAGES.index(stage)]:
        if s not in skipped and not done[s]:
            raise StageError(f"finish {s} before marking {stage}")
    p.setdefault("marks", {})[stage] = {"at": time.time() if now is None else now, "note": str(note)[:300]}
