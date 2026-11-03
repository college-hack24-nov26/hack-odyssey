"""Every sentence the chat assistant says lives here, so one test (tests/test_chat_honesty.py) can read all of them.

Rules the test enforces: none of the words safe, safely, guarantee, proven or certified; "pass" only next to "in simulation"; wherever results are
shown the not-tested line is there. The one exception is the code-owned statement `hazards.sweep.STATEMENT`, which cards carry verbatim in their own
`statement` field and which no sentence in this file repeats.

Plain words on purpose: "tests" not cases, "tough conditions" not hazards, "tries" not episodes, "simple built-in autopilot" not baseline controller.
"""
from __future__ import annotations

AUTOPILOT = "simple built-in autopilot"
HARDWARE_LINE = "Driving a real machine happens on the Hardware screen, with its checklist and STOP."
OBSERVATION_ONLY = "Observation only: this tool plans drones that look, not ones that carry or drop things."
NO_WIRING = "No wiring table for this machine; wire it manually from the parts' datasheets."
ESTIMATE = "These numbers are predictions from datasheets; none were measured on a real build."
WATCH_DEMO = "This is a demonstration, not a test."
WATCH_SIMILAR = "A similar run at this condition (same settings and try number), not the exact failed try."
NOT_WATCHABLE = "Faults and fixed obstacles cannot be watched yet. The test itself can still be run."
EMPTY_HEADLINE = "What do you want to build and test?"
EMPTY_LINE = "Describe a job. I will design a machine, test it in simulation, and tell you what I could not test."
EMPTY_PLACEHOLDER = "e.g. a boat that reaches a person 150 m away in 1 m waves"
EMPTY_LIMITS = "Simulates ground rovers, boats and observing drones. A good result here does not show a machine works in the real world."
RESETS = "Resets at 00:00 UTC"

# the plain names of the three machine kinds
PLATFORM = {"ground": "Ground rover", "air": "Observing drone", "water": "Boat"}

# jobs the quick draft can do without the AI: the empty-state chips (text is what the chip sends)
EXAMPLES = [
    {"id": "flood_boat", "label": "Boat that finds a person in a flooded street", "text": "A boat that finds a person in a flooded street"},
    {"id": "sewer_rover", "label": "Rover that inspects a 30 cm sewer pipe", "text": "A rover that inspects a 30 cm sewer pipe"},
    {"id": "field_drone", "label": "Drone that looks over a field for 10 minutes", "text": "A drone that looks over a field for 10 minutes"},
]
OWN_PARTS = {"id": "own_parts", "label": "I have my own parts", "kit": "my_rover"}

# what the simulator cannot do: word -> the reason shown on the "can't be tested here" row. Matched as whole words or phrases (intents.UNSUPPORTED).
UNTESTABLE_REASON = {
    "rain": "Rain is not simulated here.",
    "fog": "Fog and poor visibility are not simulated here.",
    "snow": "Snow and ice on the ground or the machine are not simulated here.",
    "hail": "Hail and impacts are not simulated here.",
    "underwater": "Going underwater is not simulated: boats stay on the surface.",
    "lightning": "Lightning and electrical faults are not simulated here.",
    "gps": "GPS loss or drift is not simulated here.",
    "temperature": "Temperature effects (cold batteries, heat) are not simulated here.",
    "dark": "Darkness and a degraded camera are not simulated here.",
    "dust": "Dust, dirt and a fouled lens are not simulated here.",
    "salt": "Salt water and corrosion are not simulated here.",
    "payload_drop": "Dropping or delivering a load is not simulated here. A fixed extra load can be set as the job's payload.",
    "follow": "Following a moving person is not simulated here: the person stays where they are.",
    "other": "The simulator cannot test this.",
}


TITLE = {"understanding": "What I understood", "design": "Design", "watch": "Watch it work", "tests": "Tests", "results": "Results", "parts": "Parts & build sheet",
         "improve": "Improve with AI", "limits": "What I can't test", "answer": "Answer", "blocked": "Can't do that here"}
# the job numbers in plain words (the key is what the operations and the planner call them)
PARAM_LABEL = {"pipe_d": "Pipe width", "distance_m": "Distance", "wet": "Wet floor", "minutes": "Time out", "wind_ms": "Worst wind", "wave_hs": "Worst waves",
               "range_m": "How far out", "payload_kg": "Extra load"}
PREVIEW_UNAVAILABLE = "Preview needs MuJoCo, which is not installed here. The facts above still hold."
TRAIN_LINK_LABEL = "Open Training (leaves this chat)"


def num(x) -> str:
    """A number as short text: 0.9, 1.35, 150."""
    try:
        return f"{round(float(x), 3):g}"
    except (TypeError, ValueError):
        return str(x)


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def unit_text(x, unit: str) -> str:
    return f"{num(x)} {unit}".strip()


def limit_line(n_not_tested: int) -> str:
    """Shown wherever results are shown (and in the Limits strip)."""
    return f"Simulation only, with a {AUTOPILOT}. {plural(n_not_tested, 'thing')} not tested here."


def bar_text(value: float, owner: str = "code") -> str:
    pct = f"{round(100 * float(value)):d}% of tries succeed"
    return f"{pct} (you set this)" if owner == "user" else pct


# ------------------------------------------------------------------ first answer
def understood(platform: str, summary: str) -> str:
    return f"{PLATFORM.get(platform, 'Machine')}: {summary}"


def draft_ready(n_tests: int, n_conditions: int, calm_run: bool) -> str:
    tail = " Watching a calm run now." if calm_run else ""
    return f"Here is a first design and {plural(n_tests, 'test')} ({plural(n_conditions, 'condition')}).{tail}"


def draft_started(source: str) -> str:
    return "Making a quick draft from the ready-made job. I will fill the cards in as each part finishes." if source == "draft" else \
        "No ready-made job fits, so the AI is planning this one. I will build the cards when its plan arrives."


def no_offline_draft() -> str:
    return ("I can only draft three ready-made jobs without the AI: inspecting a pipe, rescuing someone on the water, and watching from the air. "
            "Pick one of these, or save an AI key in the Goal tab settings and describe the job again.")


def kit_loaded(label: str) -> str:
    return f"Using your parts: {label}. Describe the job and I will design with what you have first."


def design_found(headline: str, scanned: int, seconds: float) -> str:
    return f"{headline} Checked {scanned} designs in {num(seconds)} s."


def design_not_found(note: str) -> str:
    return note or "Nothing in the parts library meets all of this. The closest design is shown with what it misses."


def assumed(label: str, value, unit: str) -> str:
    if label == PARAM_LABEL["wet"]:
        return "Assumed: wet floor" if float(value) >= 0.5 else "Assumed: dry floor"
    return f"Assumed: {label.lower()} {unit_text(value, unit)}"


def assumed_autopilot() -> str:
    return f"Assumed: the {AUTOPILOT} drives in every test"


def new_chat_started() -> str:
    return "Started a new chat for that job."


def ai_queued(position: int) -> str:
    return f"Another AI plan is running. This one is next in line (position {position})."


def blocked_waiting_live() -> str:
    return "The live view is busy with another run. Stop it, or wait."


def design_picked(label: str) -> str:
    return f"Switched to the design \"{label}\". Results from the earlier design are marked out of date; nothing was re-run."


def design_redone(headline: str) -> str:
    return f"Re-checked the design with the new job numbers. {headline}"


def kit_set() -> str:
    return "Using the parts in your kit for the designs from now on."


# ------------------------------------------------------------------ edits
def read_as(text: str) -> str:
    return f"I read this as: {text}"


def clamp_note(label: str, requested, applied, unit: str, side: str) -> str:
    bound = "maximum" if side == "max" else "minimum"
    return f"{label} limited to {unit_text(applied, unit)}, the simulator's {bound} (you asked for {unit_text(requested, unit)})."


def tries_clamp_note(requested, applied) -> str:
    return f"Tries limited to {num(applied)} per level (you asked for {num(requested)}); the allowed range is 1 to 20."


def bar_clamp_note(requested, applied) -> str:
    return f"The bar was limited to {round(100 * applied):d}% (you asked for {round(100 * requested):d}%)."


def levels_top_changed(title: str, before, after, unit: str) -> str:
    return f"{title} top level {num(before)} to {unit_text(after, unit)}"


def level_added(title: str, value, unit: str) -> str:
    return f"added {unit_text(value, unit)} to the {title} test"


def levels_set(title: str, levels: list, unit: str) -> str:
    return f"{title} levels now {', '.join(num(v) for v in levels)} {unit}".strip()


def ask_set(title: str, value, unit: str) -> str:
    return f"{title}: the level to cover is {unit_text(value, unit)}"


def test_added(title: str) -> str:
    return f"added the test \"{title}\""


def test_removed(title: str) -> str:
    return f"removed the test \"{title}\""


def tries_set(title: str | None, n) -> str:
    return f"{num(n)} tries per level for " + (f"\"{title}\"" if title else "every test")


def bar_set(title: str | None, value) -> str:
    who = f"\"{title}\"" if title else "every test"
    return f"bar for {who}: {bar_text(value, 'user')}"


def job_number_set(label: str, value, unit: str) -> str:
    return f"the job's {label} is {unit_text(value, unit)}"


def task_param_set(label: str, value, unit: str) -> str:
    return f"the test course {label} is {unit_text(value, unit)}"


def untestable_added(title: str, reason: str) -> str:
    return f"\"{title}\" cannot be tested here. {reason} I recorded it as not tested."


def already_there(what: str) -> str:
    return f"{what} was already like that."


def no_such_test(name: str) -> str:
    return f"There is no test called \"{name}\" in this chat."


def nothing_to_undo() -> str:
    return "There is nothing to undo."


def nothing_to_redo() -> str:
    return "There is nothing to redo."


def undone(label: str) -> str:
    return f"Undone: {label}."


def redone(label: str) -> str:
    return f"Redone: {label}."


def edits_failed(n: int, what: str) -> str:
    return f"{plural(n, 'of your edits')} no longer applied to {what}."


def stale_note() -> str:
    return "Some results are out of date because the machine or the tests changed after they ran. Nothing was re-run."


def needs_value(label: str) -> str:
    return f"How much for {label}? Pick a value."


def value_chip(label: str, value, unit: str) -> str:
    return f"{label} {unit_text(value, unit)}"


def did_you_mean() -> str:
    return "I did not understand that. Did you mean one of these?"


def new_chat_question() -> str:
    return "That sounds like a different job. Start a new chat for this?"


def help_text() -> str:
    return ("You can say things like: make the waves bigger, add wind 12 m/s, drop the delay test, 8 tries, run all, watch the worst case, build sheet, undo. "
            "Commands: /watch /test /sheet /undo /redo /new.")


# ------------------------------------------------------------------ running tests
def tests_started(n_conditions: int, n_tries: int) -> str:
    return f"Running {plural(n_conditions, 'condition')}, {plural(n_tries, 'try', 'tries')} in all. Stop works after the current condition."


def tests_stopping() -> str:
    return "Stopping after the current condition."


def tests_queued(position: int) -> str:
    return f"Another test run is going. This one is next in line (position {position})."


def queued_cancelled() -> str:
    return "Cancelled before it started."


def tests_nothing() -> str:
    return "There is nothing to run: every test here is one the simulator cannot do. They are listed as not tested."


def results_headline(met: int, total: int, below: int, not_run: int, stopped: bool) -> str:
    out = f"Met the bar in {met} of {total} simulated conditions with the {AUTOPILOT}"
    if below:
        out += f", {below} below the bar"
    if stopped or not_run:
        out += f" (stopped early, {not_run} not run)" if stopped else f" ({not_run} not run)"
    return out + "."


def weakest_line(title: str, why: str) -> str:
    return f"Weakest: {title}. {why}".strip()


def results_message(headline: str, weakest: str | None, bar: str, n_not_tested: int) -> str:
    parts = [headline]
    if weakest:
        parts.append(weakest)
    parts.append(f"Bar: {bar}.")
    parts.append(limit_line(n_not_tested))
    return " ".join(parts)


def results_next() -> str:
    return "Next: watch the weakest test, make it tougher, or get the build sheet."


def results_running(progress: dict) -> str:
    return f"Condition {min(progress.get('done', 0) + 1, max(progress.get('total', 1), 1))} of {progress.get('total', 0)}" + (" (stopping after this one)" if progress.get("stopping") else "")


def results_error(detail: str) -> str:
    return f"The test run stopped with an error: {detail}"


def interrupted(what: str) -> str:
    return f"{what} was interrupted when the server restarted. You can retry it."


# ------------------------------------------------------------------ watching
def watch_label(conditions_text: str, try_no: int = 1) -> str:
    return f"{AUTOPILOT.capitalize()}, {conditions_text}, try {try_no}. {WATCH_DEMO}"


def watch_started(conditions_text: str, source: str) -> str:
    if source == "auto":
        return f"Watching a calm run ({AUTOPILOT}). {WATCH_DEMO}"
    return f"Watching {conditions_text}. {WATCH_SIMILAR}"


def watch_replaced() -> str:
    return "Replaced by a newer run."


def watch_busy_auto() -> str:
    return "The live view is busy, so I did not start a calm run. Say \"watch it\" when you want one."


def watch_may_be_slow() -> str:
    return "Tests are running as well, so the live view may run slower."


def watch_no_mujoco() -> str:
    return "The live view needs MuJoCo, which is not installed here. Everything else still works."


def watch_not_watchable(title: str) -> str:
    return f"\"{title}\" cannot be watched yet. {NOT_WATCHABLE}"


def watch_no_design() -> str:
    return "There is no design to watch yet."


def watch_finished(success: bool, t: float) -> str:
    return (f"The {AUTOPILOT} reached the goal in {num(t)} s in this run." if success else f"This run did not reach the goal (it ended at {num(t)} s).")


# ------------------------------------------------------------------ AI planner
def ai_started(left: int | None) -> str:
    tail = f" About {left} AI requests are left today." if left is not None and left < 10 else ""
    return f"The AI is planning this job. Your draft stays usable meanwhile.{tail}"


def ai_stopped() -> str:
    return "Stopped. Requests already sent still count today. Your draft is unchanged."


def ai_unavailable(reason: str, closest: str | None) -> str:
    tail = f" Closest ready-made job: {closest}." if closest else ""
    return f"The AI planner was not available ({reason}). Your draft is unchanged.{tail}"


def ai_unavailable_no_draft(reason: str) -> str:
    return f"The AI planner was not available ({reason}) and no ready-made job fits. " + no_offline_draft()


def ai_unsupported(note: str) -> str:
    return note


def ai_quota_gone() -> str:
    return f"The free AI requests for today are used up. {RESETS}. Everything else still works."


def ai_off() -> str:
    return "The AI planner is off (no key saved, or switched off), so the offline draft is all you get. Everything else works."


def ai_done_changes(n: int) -> str:
    return f"The AI's plan differs from your draft in {plural(n, 'place')}. Choose which to keep."


def ai_done_same() -> str:
    return "The AI's plan matches your draft. Nothing to change."


def ai_adopted(source: str) -> str:
    return "Using the AI's plan." if source == "ai" else "Kept your draft."


def ai_adopted_keep_edits(n_ok: int, n_failed: int) -> str:
    out = f"Using the AI's plan with your {plural(n_ok, 'edit')} on top."
    if n_failed:
        out += f" {plural(n_failed, 'edit')} no longer applied."
    return out


def ai_failed(detail: str) -> str:
    return f"The AI step stopped with an error: {detail} Your draft is unchanged."


# ------------------------------------------------------------------ answers (no model: templates over stored results)
def answer_no_results() -> str:
    return "No tests have been run yet, so there is nothing to explain. Say \"run all\" to run them."


def answer_why_fail(title: str, condition: str, wins: int, n: int, crashes: dict, timeouts: int, mean_time) -> str:
    how = []
    for k, v in sorted(crashes.items()):
        how.append(f"{v} ended with {k}")
    if timeouts:
        how.append(f"{timeouts} ran out of time")
    ended = ", ".join(how) if how else "none ended in a crash or a timeout"
    t = f" Tries that succeeded took {num(mean_time)} s on average." if mean_time else ""
    return f"{title} at {condition}: {wins} of {n} tries succeeded; {ended}.{t}"


def answer_everything_met() -> str:
    return f"Nothing fell below the bar in the conditions that ran with the {AUTOPILOT}."


def answer_what_means(bar: str) -> str:
    return (f"A try is one run of the machine on one scene. A level meets the bar when {bar}. The same scenes are used at every level, "
            f"so only the tough condition changes. The {AUTOPILOT} drives; a better driver might do better, a worse one worse.")


def answer_cost(n_tests: int, n_conditions: int, n_tries: int) -> str:
    return (f"Running everything is {plural(n_tests, 'test')}, {plural(n_conditions, 'condition')} and {plural(n_tries, 'try', 'tries')} in all. "
            "I will only give a time after the first run, from what this machine measured.")


def answer_overview(platform: str, n_tests: int, ran: int, stale: int, headline: str | None) -> str:
    bits = [f"{PLATFORM.get(platform, 'Machine')} job with {plural(n_tests, 'test')}; {ran} have results"]
    if stale:
        bits.append(f"{stale} of those are out of date")
    if headline:
        bits.append(f"design check: {headline}")
    return "; ".join(bits) + "."


def answer_can_it(headline: str | None, results: str | None) -> str:
    base = "A question about the real world cannot be answered by the simulator alone. "
    if headline:
        base += f"The design check says: {headline} "
    base += results or "No tests have run for this yet, so nothing has been measured."
    return base.strip()


def answer_limits() -> str:
    return f"Here is what I cannot test. Everything I show is simulation only, with the {AUTOPILOT}."


# ------------------------------------------------------------------ blocked and refusals
def blocked_training() -> str:
    return "Training is not part of the chat yet. You can open the Training screen in the advanced tools (it leaves this chat)."


def blocked_hardware() -> str:
    return f"{HARDWARE_LINE} The chat never drives, arms or connects anything."


def blocked_no_plan() -> str:
    return "There is no job yet. Describe what you want to build and test first."


def blocked_no_design() -> str:
    return "There is no design yet to do that with."


def blocked_ai_in_chat() -> str:
    return "The AI plans a new job only. Edits and questions in the chat are handled without it."


def blocked_unknown_action(kind: str) -> str:
    return f"I do not know the action \"{kind}\"."


def blocked_other_chat() -> str:
    return "That job is in another chat. Open it there."


def drone_note() -> str:
    return OBSERVATION_ONLY


def error_generic(detail: str) -> str:
    return f"Something went wrong: {detail}"


# ------------------------------------------------------------------ build sheet
def sheet_ready(n_parts: int, n_buy: int) -> str:
    return f"The parts list has {plural(n_parts, 'part')}; {n_buy} to buy. Numbers on it are estimates, not measurements."


def sheet_exported() -> str:
    return "The build sheet was written as a printable page."


def rover_disabled(reason: str) -> str:
    return reason


ROVER_DISABLED_REASON = "The Hardware screen is built for the ESP32 + DRV8833 ground rover only, and this machine is a different one."
ROVER_ENABLED_LABEL = "Drive the real rover (leaves this chat)"


# ------------------------------------------------------------------ chips
CHIP = {
    "watch_rough": "Watch it in rough conditions",
    "run_all": "Run all tests",
    "add_waves": "Add waves of 1 m",
    "parts": "Parts & build sheet",
    "watch_weakest": "Watch the weakest test",
    "make_tougher": "Make it tougher",
    "fix_weakest": "Show designs that do better",
    "get_sheet": "Get the build sheet",
    "undo": "Undo",
    "stop": "Stop",
    "use_ai": "Use AI version",
    "keep_mine": "Keep mine",
    "use_ai_keep_edits": "Use AI, keep my edits",
    "new_chat": "Start a new chat for this",
    "keep_here": "No, keep working here",
    "retry": "Retry",
    "limits": "What can't you test?",
    "watch_calm": "Watch it",
    "cancel_queued": "Cancel",
}

# example arguments for every template above: the honesty test renders each one, and fails when a template is missing here
SAMPLES: dict[str, tuple] = {
    "limit_line": (7,), "bar_text": (0.75, "user"), "understood": ("water", "a boat to a person 150 m away"),
    "draft_ready": (7, 23, True), "draft_started": ("draft",), "no_offline_draft": (), "kit_loaded": ("ESP32 kit",), "design_found": ("Should do the job on paper.", 300, 0.4),
    "design_not_found": ("",), "read_as": ("waves top level 0.6 to 0.9 m",), "clamp_note": ("Waves", 2.0, 1.5, "m", "max"), "tries_clamp_note": (40, 20),
    "bar_clamp_note": (1.5, 1.0), "levels_top_changed": ("Waves", 0.6, 0.9, "m"), "level_added": ("Waves", 1.0, "m"), "levels_set": ("Waves", [0, 0.3, 0.6], "m"),
    "ask_set": ("Waves", 0.6, "m"), "test_added": ("Waves",), "test_removed": ("Slow commands",), "tries_set": (None, 8), "bar_set": ("Waves", 0.6),
    "job_number_set": ("distance", 150, "m"), "task_param_set": ("range", 80, "m"), "untestable_added": ("Rain", "Rain is not simulated here."), "already_there": ("The bar",),
    "no_such_test": ("snow",), "nothing_to_undo": (), "nothing_to_redo": (), "undone": ("waves top level",), "redone": ("waves top level",), "edits_failed": (2, "the AI plan"),
    "stale_note": (), "needs_value": ("runtime",), "value_chip": ("Runtime", 60, "min"), "did_you_mean": (), "new_chat_question": (), "help_text": (),
    "tests_started": (23, 92), "tests_stopping": (), "tests_queued": (1,), "tests_nothing": (), "queued_cancelled": (), "results_headline": (14, 23, 0, 9, True), "weakest_line": ("Waves", "Starts to fail at 0.6 m."),
    "results_message": ("Met the bar in 3 of 4 simulated conditions.", "Weakest: Waves.", "75% of tries succeed", 7), "results_next": (), "results_error": ("a failure",), "results_running": ({"done": 8, "total": 24, "stopping": True},),
    "interrupted": ("The test run",), "watch_label": ("calm conditions", 1), "watch_started": ("waves 0.6 m", "user"), "watch_replaced": (), "watch_busy_auto": (), "watch_may_be_slow": (),
    "watch_no_mujoco": (), "watch_not_watchable": ("A thruster fails",), "watch_no_design": (), "watch_finished": (True, 41.2), "ai_started": (4,), "ai_stopped": (),
    "ai_unavailable": ("no key", "Reach a person in the water"), "ai_unavailable_no_draft": ("no key",), "ai_unsupported": ("The AI says this needs a submarine.",), "ai_quota_gone": (),
    "ai_off": (), "ai_done_changes": (4,), "ai_done_same": (), "ai_adopted": ("ai",), "ai_adopted_keep_edits": (2, 1), "ai_failed": ("the plan was unreadable.",),
    "answer_no_results": (), "answer_why_fail": ("Waves", "0.9 m", 1, 4, {"capsized": 3}, 0, None), "answer_everything_met": (), "answer_what_means": ("75% of tries succeed",),
    "answer_cost": (6, 23, 92), "answer_overview": ("water", 6, 3, 1, "Should do the job on paper."), "answer_can_it": ("Should do the job on paper.", None), "answer_limits": (),
    "blocked_training": (), "blocked_hardware": (), "blocked_no_plan": (), "blocked_no_design": (), "blocked_ai_in_chat": (), "blocked_unknown_action": ("fly",), "blocked_other_chat": (),
    "drone_note": (), "error_generic": ("it broke",), "assumed": ("Time out", 10, "min"), "assumed_autopilot": (), "new_chat_started": (), "ai_queued": (2,),
    "blocked_waiting_live": (), "design_picked": ("Lightest",), "design_redone": ("Should do the job on paper.",), "kit_set": (), "sheet_ready": (12, 5), "sheet_exported": (), "rover_disabled": (ROVER_DISABLED_REASON,),
}


def render_all() -> list[str]:
    """Every template rendered once with example arguments, plus every constant: what the honesty test reads."""
    import inspect
    out: list[str] = []
    mod = globals()
    for name, obj in list(mod.items()):
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj) and obj.__module__ == __name__ and name in SAMPLES:
            out.append(obj(*SAMPLES[name]))
        elif isinstance(obj, str) and name.isupper():
            out.append(obj)
        elif isinstance(obj, dict) and name.isupper():
            out.extend(_strings(obj))
        elif isinstance(obj, list) and name.isupper():
            out.extend(_strings(obj))
    return out


def _strings(x) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, (list, tuple)):
        return [s for v in x for s in _strings(v)]
    return []


def untested_functions() -> list[str]:
    """Public template functions that have no sample arguments (the honesty test requires this to be empty, so no sentence can hide from it)."""
    import inspect
    helpers = {"num", "plural", "unit_text", "render_all", "untested_functions"}
    return [n for n, o in globals().items() if inspect.isfunction(o) and o.__module__ == __name__ and not n.startswith("_") and n not in helpers and n not in SAMPLES]
