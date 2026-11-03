"""Validation of what a model returns. The model's output is data, never instructions: everything is checked against the menu, clamped, truncated
and cleaned, and every correction is recorded in `plan['issues']` so the screen can show what was dropped and why.
"""
from __future__ import annotations

import json
import math
import re

from ..parts.build import Build
from . import menu

MAX_TEXT = 220
OPS = (">=", "<=")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class PlanError(ValueError):
    """The answer cannot be used at all (no JSON, or no platform)."""


# ---------------------------------------------------------------- JSON out of free text
def _object_end(s: str, start: int) -> int | None:
    """Index of the brace that closes the object opened at `start` (braces inside strings do not count), or None if it never closes."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            esc = (c == "\\") and not esc
            if c == '"' and not esc:
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_json(text: str) -> dict:
    """The largest complete JSON object in `text`, tolerating code fences, prose around it and trailing commas.
    Largest, not first: a reasoning model may think out loud with a small example object before it writes the real answer."""
    if not isinstance(text, str) or not text.strip():
        raise PlanError("the model returned nothing")
    s = re.sub(r"```(?:json)?", "", text)
    best: tuple[int, dict] | None = None
    start = s.find("{")
    while start != -1:
        end = _object_end(s, start)
        obj = None
        if end is not None:
            chunk = s[start:end + 1]
            for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
                try:
                    parsed = json.loads(candidate)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    obj = parsed
                    break
        if obj is not None:
            if best is None or end + 1 - start > best[0]:
                best = (end + 1 - start, obj)
            start = s.find("{", end + 1)                                  # the objects nested inside this one are part of it
        else:
            start = s.find("{", start + 1)
    if best is None:
        raise PlanError("no readable JSON object in the model's answer")
    return best[1]


# ---------------------------------------------------------------- small helpers
def _text(v, limit=MAX_TEXT) -> str:
    s = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"          # an ellipsis, so a cut sentence does not read as the model's own ending


def _num(v, lo, hi, default=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return max(lo, min(hi, x))


def _seq(v, n):
    return v[:n] if isinstance(v, list) else []


# ---------------------------------------------------------------- the plan
def validate_plan(obj: dict) -> dict:
    """A cleaned plan. Raises PlanError only when there is nothing to build on; everything else is corrected and noted."""
    if not isinstance(obj, dict):
        raise PlanError("the plan is not an object")
    issues: list[str] = []
    u = obj.get("understanding") if isinstance(obj.get("understanding"), dict) else {}
    platform = _text(u.get("platform"), 20).lower()
    if platform not in menu.PLATFORMS and platform != "unsupported":
        raise PlanError(f"unknown platform {platform!r}: it must be one of {', '.join(menu.PLATFORMS)} or unsupported")
    out = {"understanding": {"summary": _text(u.get("summary")), "platform": platform, "why": _text(u.get("why")),
                             "assumptions": [_text(a) for a in _seq(u.get("assumptions"), 6) if _text(a)]},
           "questions": [], "scenarios": [], "params": {}, "requirements": [], "task": None, "stress": {"hazards": [], "faults": []},
           "designs": [], "issues": issues}
    if platform == "unsupported":
        return out
    m = menu.load(platform)

    # numbers that describe the job
    raw_params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
    for k, v in raw_params.items():
        if k not in menu.PARAMS_OF[platform]:
            if k in menu.PARAMS:
                issues.append(f"ignored job number {k!r}: it does not apply to {platform} machines")
            else:
                issues.append(f"ignored unknown job number {k!r}")
            continue
        lo, hi = menu.PARAMS[k][:2]
        x = _num(v, lo, hi)
        if x is None:
            issues.append(f"ignored job number {k!r}: {v!r} is not a number")
        else:
            if x != float(v):
                issues.append(f"clamped job number {k!r} from {v} to {x:g}")
            out["params"][k] = x

    # questions the model needs answered (at most 4)
    seen = set()
    for q in _seq(obj.get("questions"), 4):
        if not isinstance(q, dict) or not _text(q.get("text")):
            continue
        qid = _text(q.get("id"), 32).lower()
        qid = qid if ID_RE.match(qid) and qid not in seen else f"q{len(out['questions']) + 1}"
        seen.add(qid)
        choices = [_text(c, 80) for c in _seq(q.get("choices"), 6) if _text(c, 80)]
        num = q.get("number") if isinstance(q.get("number"), dict) else None
        entry = {"id": qid, "text": _text(q["text"]), "why": _text(q.get("why")), "choices": choices, "number": None,
                 "affects": q.get("affects") if q.get("affects") in menu.PARAMS_OF[platform] else None}
        if num and not choices:
            lo, hi = menu.PARAMS[entry["affects"]][:2] if entry["affects"] else (-1e9, 1e9)      # a question about a job number can only offer what the simulator supports
            d = _num(num.get("default"), lo, hi, None)
            entry["number"] = {"unit": _text(num.get("unit"), 12), "default": d, "min": lo if entry["affects"] else None, "max": hi if entry["affects"] else None}
        if choices or entry["number"]:
            out["questions"].append(entry)

    # scenarios: the brainstorm, each tied to something the simulator can run or marked as not testable
    for s in _seq(obj.get("scenarios"), 8):
        if not isinstance(s, dict) or not _text(s.get("name")):
            continue
        hid = s.get("hazard")
        known = isinstance(hid, str) and (hid in m["hazards"] or hid in m["faults"])
        if isinstance(hid, str) and hid and not known and hid.lower() != "null":
            issues.append(f"scenario {_text(s['name'], 60)!r}: hazard {hid!r} is not in the simulator's menu, so it is listed as not testable")
        out["scenarios"].append({"name": _text(s["name"], 80), "why": _text(s.get("why")), "hazard": hid if known else None})

    # extra constraints on facts the evaluator reports
    for r in _seq(obj.get("requirements"), 8):
        if not isinstance(r, dict):
            continue
        fact, op = r.get("fact"), r.get("op")
        if fact not in menu.FACTS or op not in OPS:
            issues.append(f"dropped requirement {_text(r.get('text'), 60)!r}: {fact!r} {op!r} is not something the Builder reports")
            continue
        val = _num(r.get("value"), -1e9, 1e9)
        if val is None:
            issues.append(f"dropped requirement on {fact}: the value is not a number")
            continue
        out["requirements"].append({"fact": fact, "op": op, "value": val, "unit": menu.FACTS[fact], "text": _text(r.get("text")) or f"{fact} {op} {val:g}",
                                    "source": "user" if r.get("source") == "user" else "inferred"})

    # task: a preset that suits the platform
    presets = menu.task_presets(platform)
    t = obj.get("task") if isinstance(obj.get("task"), dict) else {}
    preset = t.get("preset") if t.get("preset") in presets else m["task"]
    if t.get("preset") and t.get("preset") not in presets:
        issues.append(f"task {t.get('preset')!r} cannot drive a {platform} machine: using {preset!r}")
    params = {}
    for k, v in (t.get("params") if isinstance(t.get("params"), dict) else {}).items():
        rng = menu.TASK_PARAMS.get(preset, {}).get(k)
        x = _num(v, *rng) if rng else None
        if x is None:
            issues.append(f"ignored task parameter {k!r} for {preset}")
        else:
            params[k] = int(x) if k == "debris" else x
    out["task"] = {"preset": preset, "params": params}

    # stress plan: only hazards and faults from the menu, levels clamped, always starting from 'none'
    st = obj.get("stress") if isinstance(obj.get("stress"), dict) else {}
    for h in _seq(st.get("hazards"), 6):
        hid = h.get("id") if isinstance(h, dict) else h
        if hid not in m["hazards"]:
            issues.append(f"dropped hazard {_text(hid, 40)!r}: it is not in the simulator's menu")
            continue
        base = m["hazards"][hid]
        lo, hi = base["range"]
        desc = base["levels"][0] > base["levels"][-1]                 # grip: the mild end is the high number
        picked = sorted({round(x, 4) for x in (_num(v, lo, hi) for v in _seq(h.get("levels") if isinstance(h, dict) else None, 8)) if x is not None},
                        reverse=desc)
        mild = base["levels"][0]
        levels = [mild] + [x for x in picked if x != mild and (x < mild if desc else x > mild)]
        if len(levels) < 3:
            levels = list(base["levels"])
        ask = _num(h.get("ask"), lo, hi) if isinstance(h, dict) else None
        out["stress"]["hazards"].append({"id": hid, "levels": levels[:8], "ask": ask})
    for f in _seq(st.get("faults"), 6):
        fid = f.get("id") if isinstance(f, dict) else f
        if fid in m["faults"] and fid not in out["stress"]["faults"]:
            out["stress"]["faults"].append(fid)
        else:
            issues.append(f"dropped fault {_text(fid, 40)!r}: it is not in the simulator's menu")

    out["designs"] = clean_designs(obj.get("designs"), platform, issues)
    return out


def clean_designs(raw, platform: str, issues: list[str], limit: int = 3) -> list[dict]:
    """Candidate machines, kept only if the Builder can parse them (so every part named exists and every field fits)."""
    out = []
    for d in _seq(raw, limit):
        if not isinstance(d, dict) or not isinstance(d.get("build"), dict):
            continue
        b = dict(d["build"])
        b["platform"] = platform
        b["name"] = "proposal"
        built, bi = Build.parse(b)
        if built is None:
            issues.append(f"dropped a design ({_text(d.get('label'), 40) or 'unnamed'}): " + "; ".join(f"{i.field}: {i.msg}" for i in bi[:3]))
            continue
        out.append({"label": _text(d.get("label"), 40) or f"Design {len(out) + 1}", "why": _text(d.get("why"), 400), "build": built.to_dict()})
    return out


def validate_designs(obj: dict, platform: str) -> tuple[list[dict], list[str]]:
    """The answer to a revision request: only the designs."""
    if not isinstance(obj, dict):
        raise PlanError("the revision is not an object")
    issues: list[str] = []
    return clean_designs(obj.get("designs"), platform, issues), issues
