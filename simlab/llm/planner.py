"""The planner: a sentence in, a verified plan out. The model proposes (what the job is, what can go wrong, what it needs, which parts, what to stress);
the Builder and the simulator check. At most MAX_CALLS requests per plan (the free tier allows 50 a day), and every failure falls back to the offline
planner (three fixed jobs, keyword matching) so the tab always answers.
"""
from __future__ import annotations

import threading
from typing import Callable

from ..goals.catalogue import Goal
from ..goals.design import evaluate_designs, finish_plan, propose, search_alternatives
from ..goals.match import match
from . import menu, prompts, settings
from .client import LLMError, OpenRouter
from .schema import PlanError, extract_json, validate_designs, validate_plan

MAX_CALLS = 3
PLAN_TOKENS, REVISE_TOKENS = 8000, 5000          # a reasoning model spends part of this thinking; too small a cap cuts the JSON off and wastes one of the 3 calls
PHASES = (('"understanding"', "Understanding the job"), ('"params"', "Working out the numbers"), ('"questions"', "Deciding what to ask you"),
          ('"scenarios"', "Thinking through what can go wrong"), ('"requirements"', "Setting requirements"), ('"task"', "Choosing the test"),
          ('"stress"', "Planning the stress test"), ('"designs"', "Choosing parts"))


def phase_of(text: str) -> str:
    """Which part of the plan the model is writing, judged by the last section key it has started."""
    best, where = "Thinking", -1
    for key, name in PHASES:
        i = text.rfind(key)
        if i > where:
            best, where = name, i
    return best


def public_plan(plan: dict) -> dict:
    """The part of a plan the screen shows: the AI's reasoning, with each scenario marked simulated or not."""
    return {"understanding": plan["understanding"], "params": plan["params"], "questions": plan["questions"], "task": plan["task"],
            "scenarios": [{**s, "simulated": bool(s["hazard"])} for s in plan["scenarios"]], "requirements": plan["requirements"],
            "stress": plan["stress"], "issues": plan["issues"]}


def watch_setup(plan: dict) -> dict:
    """What 'Watch it work' should run: the plan's task, and the worst conditions the person asked for (waves, wind, grip ...) as fixed Lab conditions."""
    from ..tasks import get_task_spec
    platform = plan["understanding"]["platform"]
    m = menu.load(platform)
    task = get_task_spec(plan["task"]["preset"]).to_dict()
    task["params"] = {**task.get("params", {}), **plan["task"]["params"]}
    conditions = {}
    for h in plan["stress"]["hazards"]:
        base = m["hazards"][h["id"]]
        if h.get("ask") is not None and base.get("knob") and not base.get("scale"):
            conditions[base["knob"]] = h["ask"]
    return {"task": task, "conditions": conditions}


def goal_for(plan: dict) -> Goal:
    u = plan["understanding"]
    return Goal("plan", u["summary"] or "Your job", u["platform"], plan["task"]["preset"] if plan["task"] else "sandbox",
                {"title": "Your job", "blurb": u["summary"]}, [], [], "")


def offline_result(text: str, owned: dict, reason: str) -> dict:
    """What the tab can still do without the model: the three fixed jobs, matched by keywords."""
    hits = match(text)
    if not hits:
        return {"source": "offline", "reason": reason, "unmatched": True, "proposals": [], "meets_goal": False, "plan": None, "calls": 0,
                "note": "The AI planner is not available and the offline planner only knows three jobs (inspect pipes, rescue on the water, watch from the air). "
                        "Pick one of the cards, or fix the AI settings."}
    r = propose(hits[0]["goal"], None, owned=owned)
    r.update(source="offline", reason=reason, plan=None, calls=0)
    r["note"] = f"The AI planner was not used ({reason}); this is the offline planner's answer to the closest fixed job. " + r.get("note", "")
    return r


def _emit(cb, kind, data=None):
    if cb:
        cb(kind, data)


def finish_from_plan(raw_plan: dict, owned: dict | None = None, *, revise: Callable[[list[str]], list[dict] | None] | None = None, emit: Callable | None = None) -> dict:
    """The part of `run_plan` that needs no model: check the plan's designs against the physics, search the parts library for a verified machine when they
    fail, and pick up to three proposals. `raw_plan` is a validated plan (validate_plan's output).

    `revise(problems)` is the one optional model call: when no design meets the job it is given what was wrong and may return better designs (already
    validated) or None. Pass nothing and this makes no request at all, which is how the chat re-finishes a plan after an edit.
    Returns goal, params, plan (public part), raw_plan, watch (what 'watch it work' runs) and finish_plan's proposals / scanned / meets_goal / note."""
    owned = owned or {}
    goal = goal_for(raw_plan)
    params = raw_plan["params"]
    _emit(emit, "phase", "Checking the designs against the physics")
    ai, problems = evaluate_designs(goal, params, raw_plan["designs"], raw_plan["requirements"], owned)
    if revise is not None and not any(not e["unmet"] for e in ai) and problems:
        better = revise(problems)
        if better is not None:
            more, _ = evaluate_designs(goal, params, better, raw_plan["requirements"], owned)
            ai += more
    grid, scanned = [], 0
    if sum(1 for e in ai if not e["unmet"]) < 2:
        _emit(emit, "phase", "Searching the parts library for a verified alternative")
        grid, scanned = search_alternatives(goal, params, raw_plan["requirements"], owned)
    out = finish_plan(goal, params, ai, grid, scanned)
    return {"goal": {**goal.to_dict(), "id": "plan"}, "params": params, "plan": public_plan(raw_plan), "raw_plan": raw_plan, "watch": watch_setup(raw_plan), **out}


def run_plan(text: str, answers: dict | None = None, owned: dict | None = None, *, client: OpenRouter | None = None, cancel: threading.Event | None = None,
             emit: Callable | None = None, final: bool = False) -> dict:
    """The whole flow. Returns a result dict ('state': 'done' | 'questions' | 'unsupported'). Raises LLMError('cancelled') only when stopped."""
    owned = owned or {}
    client = client or OpenRouter()
    if not settings.get_key() and not getattr(client, "_key", None):
        return {**offline_result(text, owned, "no OpenRouter key is set"), "state": "done"}
    calls, buf = 0, []

    def on_delta(kind, piece):
        if kind == "text":
            buf.append(piece)
            joined = "".join(buf)
            _emit(emit, "phase", phase_of(joined))
            _emit(emit, "text", joined)
        else:
            _emit(emit, "phase", "Thinking it through")

    try:
        models = client.choose_models(settings.get_model())
        _emit(emit, "phase", "Asking the AI planner")
        msgs = prompts.plan_messages(text, answers, owned, final)
        resp = client.chat_with_fallback(msgs, models, json_mode=True, on_delta=on_delta, cancel=cancel, max_tokens=PLAN_TOKENS)
        calls += 1
        repaired = None
        try:
            plan = validate_plan(extract_json(resp["text"]))
        except PlanError as e:
            if calls >= MAX_CALLS:
                raise
            cut = resp.get("finish") == "length"
            repaired = "the first answer was cut off before it finished" if cut else f"the first answer could not be used ({e})"
            _emit(emit, "phase", "Asking the AI to fix its answer")
            buf.clear()
            ask = ("Your answer was cut off because it was too long. Return the COMPLETE JSON object again, shorter: fewer words in every string, nothing else."
                   if cut else f"Your answer could not be used ({e}). Return ONLY the JSON object described above, nothing else.")
            fix = msgs + [{"role": "assistant", "content": resp["text"][:3000]}, {"role": "user", "content": ask}]
            resp = client.chat_with_fallback(fix, models, json_mode=True, on_delta=on_delta, cancel=cancel, max_tokens=PLAN_TOKENS)
            calls += 1
            plan = validate_plan(extract_json(resp["text"]))
        if repaired:
            plan["issues"].insert(0, f"{repaired}; the AI was asked once to fix it")
        used_model = resp.get("model") or models[0]
        if plan["understanding"]["platform"] == "unsupported":
            return {"state": "unsupported", "source": "ai", "model": used_model, "calls": calls, "plan": public_plan(plan), "proposals": [], "meets_goal": False,
                    "goal": None, "note": "The AI says no machine in this simulator (ground rover, drone or boat) can do this job: " + (plan["understanding"]["why"] or plan["understanding"]["summary"])}
        if plan["questions"] and not final:
            return {"state": "questions", "source": "ai", "model": used_model, "calls": calls, "plan": public_plan(plan), "raw_plan": plan}
        revise = None
        if calls < MAX_CALLS:                                       # the same test the revision used to sit behind, evaluated at the same moment
            def revise(problems: list[str]):
                nonlocal calls
                _emit(emit, "phase", "Asking the AI to revise the designs")
                buf.clear()
                resp2 = client.chat_with_fallback(prompts.revision_messages(msgs, resp["text"], problems), models, json_mode=True, on_delta=on_delta, cancel=cancel, max_tokens=REVISE_TOKENS)
                calls += 1
                try:
                    better, _ = validate_designs(extract_json(resp2["text"]), plan["understanding"]["platform"])
                except PlanError:
                    return None                                     # a bad revision changes nothing: the search still finds a verified machine
                return better
        done = finish_from_plan(plan, owned, revise=revise, emit=emit)
        return {"state": "done", "source": "ai", "model": used_model, "calls": calls, "goal": done["goal"], "params": done["params"], "answers": answers or {},
                "plan": done["plan"], "raw_plan": done["raw_plan"], "watch": done["watch"], **{k: done[k] for k in ("proposals", "scanned", "meets_goal", "note")}}
    except LLMError as e:
        if e.kind == "cancelled":
            raise
        return {**offline_result(text, owned, f"{e.kind}: {e}"), "state": "done"}
    except PlanError as e:
        return {**offline_result(text, owned, f"the AI's answer could not be used ({e})"), "state": "done"}
