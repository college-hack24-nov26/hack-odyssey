"""One call that answers everything the Builder shows about a build: the vehicle, predictions, budgets and findings."""
from __future__ import annotations

from typing import Mapping

from ..spec.params import Issue
from . import fit, recommend, rules
from .build import Build
from .derive import build_to_vehicle
from .library import load_inventory

EMPTY = {"ok": False, "task_ok": True, "build": None, "vehicle": None, "facts": [], "budget": {}, "findings": [], "assumptions": [], "owned": {}, "fit": None}


def _task_requirements(spec, task: str):
    """(task title, requirements) for `task` driving `spec`, or (title, None, reason) when the task cannot drive it."""
    from ..registry import TASKS
    from ..spec.vehicle import SpecError
    from ..tasks import get_task_spec
    ts = get_task_spec(task)
    cls = TASKS.get(ts.task)
    try:
        return cls.title or ts.name, cls(ts, spec).requirements(), ""
    except SpecError as e:
        return cls.title or ts.name, None, str(e)


def _findings_or_none(build: dict, task: str | None) -> list[dict] | None:
    r = evaluate(build, task, suggest=False)
    return None if r["vehicle"] is None else r["findings"]


def evaluate(build: Build | Mapping, task: str | None = None, suggest: bool = True) -> dict:
    """Never raises. `ok`: a valid vehicle came out and none of the machine's own findings is an error. `task_ok`: the same for the
    chosen task's requirements (a machine can be fine and still not suit a task)."""
    issues = []
    if not isinstance(build, Build):
        if not isinstance(build, Mapping):
            build, issues = None, [Issue("error", "build", "must be a mapping")]
        else:
            raw = dict(build)
            build, issues = Build.parse(raw)
    if build is None:
        return {**EMPTY, "issues": [i.to_dict() for i in issues]}
    spec, dv = build_to_vehicle(build)
    findings = rules.run(build, dv, spec)
    fit_info = None
    if task and spec is not None:
        try:
            title, reqs, why = _task_requirements(spec, task)
        except KeyError as e:
            title, reqs, why = task, None, str(e).strip("'\"")
        if reqs is None:
            fit_info = {"task": task, "title": title, "items": [{"kind": "task", "title": "This task cannot drive this vehicle", "status": "fail", "detail": why}]}
        else:
            items, task_findings = fit.check(build, dv, reqs)
            findings += task_findings
            fit_info = {"task": task, "title": title, "items": items}
    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (f.scope == "task", order[f.severity]))
    ok = spec is not None and not any(f.severity == "error" and f.scope == "build" for f in findings)
    task_ok = not any(f.severity == "error" and f.scope == "task" for f in findings) and not (fit_info and any(i["status"] == "fail" for i in fit_info["items"]))
    out = {"ok": ok, "task_ok": task_ok, "build": build.to_dict(), "issues": [i.to_dict() for i in issues],
           "vehicle": spec.to_dict() if spec is not None else None, "facts": [f.to_dict() for f in dv.facts], "budget": dv.budget,
           "findings": [f.to_dict() for f in findings], "assumptions": dv.assumptions, "owned": load_inventory(), "fit": fit_info}
    if suggest and spec is not None:
        raw = build.to_dict()
        for f in out["findings"]:
            if f["severity"] in ("error", "warn") and f.get("fix"):
                f["fix"] = {**f["fix"], "candidates": recommend.candidates(raw, f, lambda b: _findings_or_none(b, task))}
    return out
