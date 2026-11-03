"""The cards, as plain dicts the page renders without calling anything else (except the live 3D view, which uses /api/lab/scene, /api/lab/state and /ws/lab).

Every builder here is a pure function of the chat's state, so the same state always gives the same card and a patch is just the difference between two builds.
Honesty is built in: a results card carries the code-owned statement and the not-tested list; a drone's cards say it only observes; the parts card has links, never a
drive, arm or checklist action.
"""
from __future__ import annotations

from ..hazards.sweep import STATEMENT
from ..llm import menu
from . import cases as C
from . import words

KEY_FACTS = {"ground": ("runtime", "top_speed", "climb", "mass"), "water": ("runtime", "top_speed", "freeboard", "thrust"), "air": ("runtime", "twr", "mass", "hover_thrust")}


def act(type_: str, label: str, args: dict | None = None, *, enabled: bool = True, reason: str | None = None, primary: bool = False) -> dict:
    return {"type": type_, "label": label, "args": args or {}, "enabled": enabled, "reason": reason, "primary": primary}


def chip(cid: str, label: str, action: dict, *, kind: str = "secondary", enabled: bool = True, reason: str | None = None, tag: str | None = None) -> dict:
    return {"id": cid, "label": label, "action": action, "kind": kind, "enabled": enabled, "reason": reason, "tag": tag}


def _card(cid: str, type_: str, state: str, summary: str, **fields) -> dict:
    return {"id": cid, "type": type_, "title": words.TITLE[type_], "state": state, "summary": summary, **fields}


def n_not_tested(book) -> int:
    return len(book.not_tested_items()) if book is not None else 0


# ---------------------------------------------------------------- understanding
def understanding(s: dict, book, requirements: list[str] | None = None) -> dict:
    platform = s["platform"]
    unit_of = {k: (menu.PARAMS[k][2] if menu.PARAMS[k][2] != "0/1" else "") for k in menu.PARAMS}
    stated = s.get("stated", {})
    numbers, assumptions = [], []
    for k in menu.PARAMS_OF[platform]:
        lo, hi, unit, _ = menu.PARAMS[k]
        v = book.params.get(k)
        if v is None:
            continue
        label = words.PARAM_LABEL.get(k, k)
        touched = k in book.touched_params
        numbers.append({"key": k, "label": label, "value": v, "unit": unit_of[k], "min": lo, "max": hi, "touched": touched, "stated": k in stated,
                        "action": {"type": "set_job_number", "args": {"name": k}}})
        if k not in stated and not touched:
            assumptions.append({"id": f"a_{k}", "text": words.assumed(label, v, unit_of[k]), "param": k})
    assumptions.append({"id": "a_autopilot", "text": words.assumed_autopilot(), "param": None})
    src = s.get("source") or "draft"
    return _card("understanding", "understanding", "ready", words.understood(platform, s.get("summary", "")), platform=platform, platform_label=words.PLATFORM[platform],
                 job=s.get("text", ""), badge={"source": src, "label": "AI version" if src == "ai" else "Quick draft"}, numbers=numbers, requirements=list(requirements or []),
                 assumptions=assumptions, observation_only=platform == "air", observation_note=words.OBSERVATION_ONLY if platform == "air" else None,
                 not_tested=[{"id": c["id"], "title": c["title"], "reason": c["untestable_reason"]} for c in book.untestable()], actions=[])


# ---------------------------------------------------------------- design
def proposal_card_view(p: dict) -> dict:
    """A stored proposal without the parts the page never needs (the vehicle spec stays on the server)."""
    return {k: v for k, v in p.items() if k not in ("vehicle", "findings_plain")}


def design(s: dict, preview: dict | None = None) -> dict:
    props = s.get("proposals") or []
    pick = s.get("pick", 0)
    info = s.get("design", {})
    if not props:
        return _card("design", "design", "unavailable", words.design_not_found(info.get("note", "")), proposals=[], pick=0, actions=[], what_this_means=words.ESTIMATE,
                     observation_only=s["platform"] == "air", preview={"state": "unavailable", "reason": "no design"})
    p = props[pick]
    return _card("design", "design", "ready", p["verdict"]["headline"], proposals=[proposal_card_view(x) for x in props], pick=pick, verdict=p["verdict"], headline=p["verdict"]["headline"],
                 pill=p.get("pill", "Estimate"), key_facts=p.get("key_facts", []), what_this_means=words.ESTIMATE, scanned=info.get("scanned"), seconds=info.get("seconds"),
                 meets_goal=info.get("meets_goal"), note=info.get("note", ""), observation_only=s["platform"] == "air",
                 observation_note=words.OBSERVATION_ONLY if s["platform"] == "air" else None, preview=preview or {"state": "pending"},
                 actions=[act("pick_design", x["label"], {"index": i}, primary=i == pick) for i, x in enumerate(props)] if len(props) > 1 else [])


# ---------------------------------------------------------------- watch
def conditions_menu(platform: str) -> list[dict]:
    """The tough-conditions sliders for this kind of machine: the knobs the live view can set, with their ranges. Values are the knob's own unit, except `drag`
    (mud), which is a share of the machine's pulling force; the server converts it."""
    out = []
    for hid, h in menu.load(platform)["hazards"].items():
        if h.get("knob") in C.LIVE_KNOBS:
            lo, hi = h["range"]
            out.append({"hazard": hid, "knob": h["knob"], "title": h["title"], "unit": h.get("unit", ""), "min": lo, "max": hi, "mild": h["levels"][0],
                        "levels": list(h["levels"]), "descending": h["levels"][0] > h["levels"][-1], "plain": h.get("plain", "")})
    return out


def watch(s: dict, mujoco: bool, busy: bool = False) -> dict:
    runs = s.get("runs", [])
    cur = runs[-1] if runs else None
    if not mujoco:
        state, summary = "unavailable", words.watch_no_mujoco()
    elif cur is None:
        state, summary = "idle", words.watch_busy_auto() if busy else words.watch_label("calm conditions")
    else:
        state, summary = cur["state"], cur["label"]
    return _card("watch", "watch", state, summary, runs=runs, current=cur, conditions=conditions_menu(s["platform"]), mujoco=mujoco, demo_note=words.WATCH_DEMO,
                 similar_note=words.WATCH_SIMILAR, scene_url="/api/lab/scene", state_url="/api/lab/state", ws="/ws/lab", camera_url="/api/lab/video",
                 actions=[act("watch", "Run again", {}, enabled=mujoco, reason=None if mujoco else words.watch_no_mujoco()),
                          act("stop", "Stop", {"job": "live"}, enabled=bool(cur and cur["state"] == "running"))])


# ---------------------------------------------------------------- tests
def tests(s: dict, book, overlay: dict | None = None, running: bool = False) -> dict:
    view = book.view(overlay)
    summ = book.summary()
    top = []
    for c in view:
        if c["status"] == "untestable":
            continue
        if c["kind"] == "sweep":
            sw = c["sweep"]
            top.append({"id": c["id"], "title": c["title"], "line": f"{words.num(sw['levels'][0])} to {words.num(sw['levels'][-1])} {sw['unit']}, {words.plural(c['tries']['n'], 'try', 'tries')} each".replace("  ", " ")})
        else:
            top.append({"id": c["id"], "title": c["title"], "line": f"one condition, {words.plural(c['tries']['n'], 'try', 'tries')}"})
    state = "running" if running else ("stale" if summ["stale"] else "ready" if summ["tests"] else "empty")
    line = f"{words.plural(summ['tests'], 'test')}, {words.plural(summ['conditions'], 'condition')}, {words.plural(summ['tries'], 'try', 'tries')} in all"
    if summ["untestable"]:
        line += f"; {summ['untestable']} cannot be tested here"
    return _card("tests", "tests", state, line, counts=summ, rows=view, top=top[:3], untestable=[{"id": c["id"], "title": c["title"], "reason": c["untestable_reason"]} for c in book.untestable()],
                 bar_text=C.results_matrix(book)["bar_text"], cost_line=line, can_undo=book.can_undo(), can_redo=book.can_redo(), threshold_owner=_bar_owner(book),
                 actions=[act("run_tests", "Run all", {"cases": "all"}, primary=True, enabled=summ["tests"] > 0), act("undo", "Undo", {}, enabled=book.can_undo()),
                          act("redo", "Redo", {}, enabled=book.can_redo()), act("open_details", "Open details", {"tab": "tests"})])


def _bar_owner(book) -> str:
    return "user" if any(c["pass"]["owner"] == "user" for c in book.testable()) else "code"


# ---------------------------------------------------------------- results
def results(cid: str, run: dict, book, overlay: dict | None, headline: str, weakest: dict | None, report: dict | None, state: str, progress: dict, chips: list | None = None) -> dict:
    matrix = C.results_matrix(book, overlay, ids=run["case_ids"])
    n_nt = n_not_tested(book)
    return _card(cid, "results", state, headline, run_id=run["id"], started=run["started"], finished=run.get("finished"), seconds=run.get("seconds"), progress=progress, matrix=matrix,
                 headline=headline, weakest=weakest, bar_text=matrix["bar_text"], statement=STATEMENT, not_tested=book.not_tested_items(), limit_line=words.limit_line(n_nt),
                 report=report, stale=any((book.get(i) or {}).get("stale") for i in run["case_ids"]), case_ids=run["case_ids"], what_this_means=words.answer_what_means(matrix["bar_text"]),
                 next_note=words.results_next(), chips=chips or [], actions=[act("stop", "Stop", {"job": "tests"}, enabled=state == "running"),
                                                                            act("watch", "Watch the weakest test", {"weakest": True}, enabled=bool(weakest)),
                                                                            act("sheet", "Get the build sheet", {"format": "json"})])


# ---------------------------------------------------------------- parts
def parts(s: dict, book, sheet: dict, rover: dict, export: dict | None = None) -> dict:
    p = (s.get("proposals") or [{}])[s.get("pick", 0)]
    facts = {f["key"]: f for f in p.get("facts", [])} if p else {}
    power = None
    if "pack_voltage" in facts:
        power = f"{words.num(facts['pack_voltage']['value'])} V battery pack" + (f", about {words.num(facts['runtime']['value'])} min of running (an estimate)" if "runtime" in facts else "")
    rows = [{**r, "to_buy": max(0, r["qty"] - r["have"]), "have_all": r["have"] >= r["qty"]} for r in sheet["parts"]]
    n_buy = sum(1 for r in rows if not r["have_all"])
    wiring = sheet["wiring"]
    return _card("parts", "parts", "ready", words.sheet_ready(len(rows), n_buy), parts=rows, to_buy=[r["name"] for r in rows if not r["have_all"]], power_note=power,
                 firmware=wiring.get("firmware") or None, wiring={"generated": wiring["generated"], "tables": wiring["tables"], "note": None if wiring["generated"] else words.NO_WIRING,
                                                                   "reason": wiring.get("reason", "")}, steps=sheet["steps"], safety=sheet["safety"], caveat=sheet["caveat"],
                 hardware_line=words.HARDWARE_LINE, rover=rover, export=export, observation_only=s["platform"] == "air",
                 actions=[act("open_details", "Open build sheet", {"tab": "parts"}), act("sheet", "Export HTML", {"format": "html"})])


def rover_link(is_rover: bool) -> dict:
    """The one way from the chat to the real rover: a link to the Hardware screen, enabled only for the machine its firmware was built for."""
    return {"label": words.ROVER_ENABLED_LABEL, "href": "/#hw", "enabled": is_rover, "reason": None if is_rover else words.ROVER_DISABLED_REASON, "leaves_chat": True}


# ---------------------------------------------------------------- improve (the AI step)
def improve(ai: dict) -> dict:
    st = ai.get("state", "idle")
    summary = {"queued": "Waiting for another AI plan to finish", "running": ai.get("phase") or "Planning", "done": "The AI's plan is ready", "applied": "Using the AI's plan",
               "kept": "Kept your draft", "failed": "The AI step failed", "unavailable": "The AI planner was not available", "stopped": "Stopped",
               "unsupported": "The AI says this cannot be built here", "interrupted": "Interrupted by a restart", "idle": ""}.get(st, st)
    diff = ai.get("diff")
    actions = []
    if st == "running" or st == "queued":
        actions.append(act("stop", "Stop", {"job": "plan"}, primary=True))
    if st == "done" and diff:
        actions = [act("use_ai", words.CHIP["use_ai"], primary=True), act("keep_mine", words.CHIP["keep_mine"]), act("use_ai_keep_edits", words.CHIP["use_ai_keep_edits"])]
    return _card("improve", "improve", st, summary, phase=ai.get("phase"), seconds=ai.get("seconds", 0), chars=ai.get("chars", 0), tail=ai.get("tail", ""), model=ai.get("model"),
                 diff=diff, reason=ai.get("reason"), closest=ai.get("closest"), draft_note="Your draft stays usable meanwhile." if st == "running" else None,
                 requests_note="Requests already sent still count today." if st in ("stopped", "failed") else None, calls=ai.get("calls"), actions=actions)


# ---------------------------------------------------------------- limits, answers, refusals
def limits(book, platform: str | None) -> dict:
    items = []
    if book is not None:
        items = [{"text": t, "kind": "untestable"} for t in (c["not_tested_text"] for c in book.untestable())] + [{"text": t, "kind": "catalogue"} for t in book.m["not_tested"]]
        bars = [f"{c['title']}: {words.bar_text(c['pass']['value'], 'user')}" for c in book.testable() if c["pass"]["owner"] == "user"]
    else:
        bars = []
    return _card("limits", "limits", "ready", words.answer_limits(), items=items, count=len(items), statement=STATEMENT, line=words.limit_line(len(items)),
                 bullets=[f"Simulation only, with a {words.AUTOPILOT}.", "Ground rovers, boats and observation-only drones.", words.EMPTY_LIMITS], user_bars=bars,
                 observation_only=platform == "air", actions=[act("open_details", "Back to my job", {"tab": "tests"})])


def answer(cid: str, topic: str, question: str, text: str, details: list[str], n_nt: int, with_limit: bool = True) -> dict:
    card = _card(cid, "answer", "ready", text, topic=topic, question=question, text=text, details=details, actions=[])
    if with_limit:
        card["limit_line"] = words.limit_line(n_nt)
    return card


def blocked(cid: str, reason: str, *, state: str = "blocked", nearest: dict | None = None, chips: list | None = None, link: dict | None = None, ticket: dict | None = None) -> dict:
    return _card(cid, "blocked", state, reason, reason=reason, nearest=nearest, chips=chips or [], link=link, ticket=ticket, actions=[])
