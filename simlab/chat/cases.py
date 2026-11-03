"""Tests as data: the chat's list of test cases, the operations that edit it, undo/redo, and the staleness of results.

A plan's stress section (hazards with levels, faults) is turned into TestCases by `hazards.synth.synthesize`, so the stress-test engine, the report writer and the
code-owned honesty rules (threshold, controller note, not-tested list, statement) stay the single source of truth, and `CaseBook.to_catalogue()` turns the cases
back into exactly the catalogue dict `run_sweep` takes. Custom fixed conditions (several knobs at once) live only in the cases and become extra fault entries.

The visible plan is a *base* (the quick draft, or the AI's plan) plus an ordered list of edits. Edits are replayed on top of whichever base is kept, so switching
base never loses what the user did, and every field an edit touched is marked. Each user turn is one history entry, which is one undo step; undo is 20 deep.
Results are kept apart from the replay (by case id), so undoing an edit never throws a result away; a result is *stale* when the case's input signature
(machine, task, levels or conditions, bar, tries) no longer matches the one it ran with.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from ..hazards.synth import synthesize
from ..llm import menu
from . import words

TRIES_RANGE = (1, 20)
BAR_RANGE = (0.05, 1.0)
DEFAULT_TRIES = 4
UNDO_DEPTH = 20
MAX_LEVELS = 8
LIVE_KNOBS = ("wave_hs", "wind_ms", "current_ms", "grip_mu", "drag_ms2", "cmd_delay_s", "cmd_dropout")     # what the Lab can set on a running simulation
# job numbers that are on/off or whole numbers
INT_PARAMS = ("wet",)


# ---------------------------------------------------------------- small helpers
def clamp(v: float, lo: float, hi: float) -> tuple[float, str | None]:
    """(clamped value, 'min' | 'max' | None)."""
    if v < lo:
        return lo, "min"
    if v > hi:
        return hi, "max"
    return v, None


def _r(v: float) -> float:
    return round(float(v), 4)


def _short(text: str, limit: int = 160) -> str:
    first = re.split(r"(?<=[.!?])\s", (text or "").strip())[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def _sig(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:24] or "x"


def ensure_level(levels: list[float], value: float, desc: bool) -> list[float]:
    """`levels` with `value` inserted in severity order unless it is already there (within rounding)."""
    if any(abs(v - value) < 1e-6 for v in levels):
        return list(levels)
    return sorted([*levels, _r(value)], reverse=desc)


def allowed_knobs(platform: str) -> list[str]:
    """Knobs a custom fixed condition may set on this kind of machine: those its catalogue's hazards and faults already use, and the Lab can set live.
    drag is left out: the catalogue's mud level is a share of the machine's acceleration, a custom condition would have to say which unit."""
    m = menu.load(platform)
    knobs = {h["knob"] for h in m["hazards"].values() if h.get("knob")}
    for f in m["faults"].values():
        knobs |= set(f["hazards"])
    return sorted(k for k in knobs if k in LIVE_KNOBS and k != "drag_ms2")


def normalise_levels(raw: list[float], base: dict, clamps: list[str], label: str) -> list[float]:
    """Mild to severe, starting at the catalogue's mild level, clamped to the knob's range, at least 3 levels, at most 8 (the validator's own rules)."""
    lo, hi = base["range"]
    desc = base["levels"][0] > base["levels"][-1]
    mild = base["levels"][0]
    picked = []
    for v in raw:
        x, side = clamp(float(v), lo, hi)
        if side:
            clamps.append(words.clamp_note(label, v, x, base.get("unit", ""), side))
        picked.append(int(round(x)) if "task_param" in base else _r(x))
    picked = sorted(set(picked), reverse=desc)
    levels = [mild] + [x for x in picked if x != mild and (x < mild if desc else x > mild)]
    for x in base["levels"]:                                            # fewer than 3: pad from the catalogue's own ladder
        if len(levels) >= 3:
            break
        if x not in levels:
            levels.append(x)
    return sorted(set(levels), reverse=desc)[:MAX_LEVELS]


def hazard_info(platform: str, hid: str) -> dict | None:
    return menu.load(platform)["hazards"].get(hid)


# ---------------------------------------------------------------- plans
def draft_plan(platform: str, params: dict, summary: str, why: str = "", assumptions: list[str] | None = None) -> dict:
    """The quick draft's plan, in the same shape as a validated AI plan: every hazard and fault of the platform's catalogue (the three ready-made jobs'
    stress tests), each hazard's asked level (waves the job must handle ...) inserted into its levels so it is actually tested."""
    from ..hazards.sweep import load_catalogue
    m = menu.load(platform)
    cat = load_catalogue(menu.CATALOGUE_OF[platform])
    hazards = []
    for hid, h in m["hazards"].items():
        levels = list(h["levels"])
        key = h.get("ask")
        if key and params.get(key) is not None:
            lo, hi = h["range"]
            levels = ensure_level(levels, clamp(float(params[key]), lo, hi)[0], levels[0] > levels[-1])[:MAX_LEVELS]
        hazards.append({"id": hid, "levels": levels, "ask": None})
    return {"understanding": {"summary": summary, "platform": platform, "why": why, "assumptions": list(assumptions or [])}, "questions": [], "scenarios": [],
            "params": dict(params), "requirements": [], "task": {"preset": m["task"], "params": dict(cat.get("task_params") or {})},
            "stress": {"hazards": hazards, "faults": list(m["faults"])}, "designs": [], "issues": []}


@dataclass
class Outcome:
    """What one group of operations did: the ops that stuck (as stored, already clamped), the ones that did not, and the plain-words account."""
    applied: list[dict] = field(default_factory=list)
    failed: list[tuple[dict, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    clamps: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)          # case ids touched
    job_changed: bool = False                                  # a job number changed: the design must be redone

    def to_dict(self) -> dict:
        return {"applied": self.applied, "failed": [{"op": o, "why": w} for o, w in self.failed], "notes": self.notes, "clamps": self.clamps,
                "issues": self.issues, "changed": self.changed, "job_changed": self.job_changed}


class _State:
    """The materialised plan: job numbers, the test course, the cases."""

    def __init__(self):
        self.params: dict = {}
        self.task: dict = {"preset": "", "params": {}}
        self.cases: list[dict] = []
        self.touched_params: set[str] = set()
        self.touched_task: set[str] = set()
        self.next_user = 1


class CaseBook:
    def __init__(self, platform: str, base: dict, source: str = "catalogue", machine_sig: str = ""):
        self.platform = platform
        self.m = menu.load(platform)
        self.base = copy.deepcopy(base)
        self.source = source                       # 'catalogue' (the quick draft) | 'ai'
        self.machine_sig = machine_sig
        self.history: list[dict] = []              # [{"label", "ops"}]: every applied group, in order
        self.cursor = 0                            # how many of them are in force (undo moves it back, redo forward)
        self.runs: dict[str, dict] = {}            # case id -> last_run, kept outside the replay
        self.replay_failures: list[dict] = []
        self.st = _State()
        self._materialise()

    # ------------------------------------------------------------ derive
    def _derive(self) -> _State:
        st = _State()
        plan = self.base
        st.params = dict(plan.get("params") or {})
        st.task = {"preset": (plan.get("task") or {}).get("preset") or self.m["task"], "params": dict((plan.get("task") or {}).get("params") or {})}
        cat, asked = synthesize(plan, self.platform)
        explicit = {h["id"]: h.get("ask") for h in plan["stress"]["hazards"]}
        for h in cat["hazards"]:
            st.cases.append(self._sweep_case(h, asked.get(h.get("ask", "")), self.source, "plan" if explicit.get(h["id"]) is not None else "param"))
        for f in cat["faults"]:
            st.cases.append(self._fault_case(f, self.source))
        for s in plan.get("scenarios", []):
            if not s.get("hazard"):
                reason = s.get("why") or words.UNTESTABLE_REASON["other"]
                st.cases.append(self._untestable_case(s["name"], reason, f"tc_n_{slug(s['name'])}", self.source,
                                                      f"The AI thought of this but the simulator cannot test it: {s['name']}" + (f" ({s['why']})" if s.get("why") else "")))
        self._refresh_param_asks(st)
        return st

    def _base_case(self, cid: str, source: str) -> dict:
        return {"id": cid, "title": "", "why": "", "source": source, "status": "ready", "kind": "sweep", "sweep": None, "fixed": None,
                "task": {"preset": "", "params": {}}, "tries": {"start": 0, "n": DEFAULT_TRIES},
                "pass": {"metric": "success_rate", "op": ">=", "value": float(self.m["threshold"]), "owner": "code"},
                "untestable_reason": None, "not_tested_text": None, "touched": [], "input_sig": "", "last_run": None, "stale": False}

    def _sweep_case(self, h: dict, ask, source: str, ask_src: str) -> dict:
        base = self.m["hazards"][h["id"]]
        c = self._base_case(f"tc_{h['id']}", source)
        c["why"] = _short(h.get("plain", ""))
        c["sweep"] = {"hazard": h["id"], "knob": h.get("knob") or h.get("task_param"), "task_param": h.get("task_param"), "unit": h.get("unit", ""),
                      "levels": [(int(v) if "task_param" in h else v) for v in h["levels"]], "ask": ask, "ask_src": ask_src if ask is not None else None,
                      "ask_param": base.get("ask"), "descending": base["levels"][0] > base["levels"][-1], "mild": base["levels"][0],
                      "range": list(base["range"]), "title": h["title"]}
        return c

    def _fault_case(self, f: dict, source: str, custom: bool = False, cid: str | None = None) -> dict:
        c = self._base_case(cid or f"tc_f_{f['id']}", source)
        c["kind"] = "fixed"
        c["title"] = f["title"]
        c["why"] = _short(f.get("plain", ""))
        c["fixed"] = {"conditions": dict(f["hazards"]), "fault": None if custom else f["id"], "label": f["title"], "plain": f.get("plain", ""), "fix": f.get("fix", "")}
        return c

    def _untestable_case(self, title: str, reason: str, cid: str, source: str, text: str | None = None) -> dict:
        c = self._base_case(cid, source)
        c.update(title=title, why=reason, status="untestable", kind="untestable", untestable_reason=reason,
                 not_tested_text=text or f"You asked for \"{title}\" but the simulator cannot test it: {reason}")
        return c

    # ------------------------------------------------------------ replay
    def _materialise(self) -> None:
        keep_failures: list[dict] = []
        st = self._derive()
        for entry in self.history[:self.cursor]:
            for op in entry["ops"]:
                out = Outcome()
                if not self._apply_op(st, op, out) and out.failed:
                    keep_failures.append({"op": op, "why": out.failed[0][1], "label": entry.get("label", "")})
        self.replay_failures = keep_failures
        self.st = st
        self.refresh()

    def refresh(self) -> None:
        """Recompute titles, signatures, stale flags and statuses from the current state and the stored results."""
        st = self.st
        for c in st.cases:
            c["task"] = {"preset": st.task["preset"], "params": dict(st.task["params"])}
            if c["status"] == "untestable":
                c["input_sig"], c["last_run"], c["stale"] = self.case_sig(c), None, False
                c["watchable"], c["watch_reason"] = False, c["untestable_reason"]
                continue
            if c["kind"] == "sweep":
                s = c["sweep"]
                top = s["levels"][-1]
                s["title"] = self.m["hazards"][s["hazard"]]["title"]
                c["title"] = f"{s['title']} {'down to' if s['descending'] else 'up to'} {words.num(top)}" + (f" {s['unit']}" if s["unit"] and s["unit"] != "grip" else "")
                if s["ask_src"] == "param" and s["ask_param"]:
                    s["ask"] = st.params.get(s["ask_param"], s["ask"])
            c["input_sig"] = self.case_sig(c)
            run = self.runs.get(c["id"])
            c["last_run"] = run
            c["stale"] = bool(run and run.get("input_sig") != c["input_sig"])
            c["watchable"], c["watch_reason"] = self._watchable(c)
            c["status"] = "done" if run and not c["stale"] and run.get("verdict") in ("met", "below") else "ready"

    def _refresh_param_asks(self, st: _State) -> None:
        for c in st.cases:
            if c["kind"] == "sweep" and c["sweep"]["ask_src"] == "param" and c["sweep"]["ask_param"]:
                c["sweep"]["ask"] = st.params.get(c["sweep"]["ask_param"])

    def case_sig(self, c: dict) -> str:
        body = c["sweep"]["levels"] if c["kind"] == "sweep" else c["fixed"]["conditions"] if c["kind"] == "fixed" else c["id"]
        extra = c["sweep"]["ask"] if c["kind"] == "sweep" else None
        return _sig(self.machine_sig, c["task"], c["kind"], body, extra, c["pass"]["value"], c["tries"]["n"])

    def _watchable(self, c: dict) -> tuple[bool, str | None]:
        if c["kind"] == "sweep":
            s = c["sweep"]
            if s["task_param"]:
                return False, words.NOT_WATCHABLE
            return (True, None) if s["knob"] in LIVE_KNOBS else (False, words.NOT_WATCHABLE)
        f = c["fixed"]
        if f["fault"] is not None or not all(k in LIVE_KNOBS for k in f["conditions"]):
            return False, words.NOT_WATCHABLE
        return True, None

    def set_machine(self, sig: str) -> None:
        """A new machine (another design, a redesign): every result that ran on the old one becomes stale, nothing is re-run."""
        self.machine_sig = sig
        self.refresh()

    # ------------------------------------------------------------ lookups
    @property
    def params(self) -> dict:
        return self.st.params

    @property
    def task(self) -> dict:
        return self.st.task

    @property
    def cases(self) -> list[dict]:
        return self.st.cases

    @property
    def touched_params(self) -> set[str]:
        return self.st.touched_params

    def get(self, cid: str | None) -> dict | None:
        return next((c for c in self.st.cases if c["id"] == cid), None) if cid else None

    def for_hazard(self, hid: str) -> dict | None:
        return next((c for c in self.st.cases if c["kind"] == "sweep" and c["sweep"]["hazard"] == hid), None)

    def for_fault(self, fid: str) -> dict | None:
        return next((c for c in self.st.cases if c["kind"] == "fixed" and c["fixed"]["fault"] == fid), None)

    def testable(self) -> list[dict]:
        return [c for c in self.st.cases if c["status"] != "untestable"]

    def untestable(self) -> list[dict]:
        return [c for c in self.st.cases if c["status"] == "untestable"]

    def not_tested_items(self) -> list[str]:
        """What the Limits strip counts and the results carry: every case the simulator cannot test, then the catalogue's own list."""
        return [c["not_tested_text"] for c in self.untestable()] + list(self.m["not_tested"])

    def n_conditions(self, ids: list[str] | None = None) -> int:
        return sum(len(c["sweep"]["levels"]) if c["kind"] == "sweep" else 1 for c in self.testable() if ids is None or c["id"] in ids)

    def n_tries(self, ids: list[str] | None = None) -> int:
        return sum((len(c["sweep"]["levels"]) if c["kind"] == "sweep" else 1) * c["tries"]["n"] for c in self.testable() if ids is None or c["id"] in ids)

    def any_stale(self) -> bool:
        return any(c["stale"] for c in self.st.cases)

    # ------------------------------------------------------------ operations
    def apply(self, ops: list[dict], label: str = "") -> Outcome:
        """Apply one user turn's operations. Those that cannot apply are reported and left out; the rest become one history entry (one undo step)."""
        out = Outcome()
        st = copy.deepcopy(self.st)
        kept = []
        for op in ops:
            before = len(out.applied)
            if self._apply_op(st, op, out) and len(out.applied) > before:
                kept.append(out.applied[-1])
        if kept:
            self.history = self.history[:self.cursor] + [{"label": label or (out.notes[0] if out.notes else "edit"), "ops": kept}]
            self.cursor = len(self.history)
            self.st = st
            self.refresh()
        return out

    def can_undo(self) -> bool:
        return self.cursor > 0 and len(self.history) - self.cursor < UNDO_DEPTH

    def can_redo(self) -> bool:
        return self.cursor < len(self.history)

    def undo(self) -> str | None:
        if not self.can_undo():
            return None
        self.cursor -= 1
        label = self.history[self.cursor]["label"]
        self._materialise()
        return label

    def redo(self) -> str | None:
        if not self.can_redo():
            return None
        label = self.history[self.cursor]["label"]
        self.cursor += 1
        self._materialise()
        return label

    def flat_ops(self) -> list[dict]:
        """The edits in force, in order (what is replayed onto a new base)."""
        return [op for e in self.history[:self.cursor] for op in e["ops"]]

    def rebase(self, base: dict, source: str, keep_edits: bool = True) -> list[dict]:
        """Switch to another base plan. With `keep_edits` the history is replayed on it and the ops that no longer apply are returned; without, it is dropped."""
        self.base = copy.deepcopy(base)
        self.source = source
        self.m = menu.load(self.platform)
        if not keep_edits:
            self.history, self.cursor = [], 0
        self._materialise()
        return self.replay_failures

    def _apply_op(self, st: _State, op: dict, out: Outcome) -> bool:
        kind = op.get("op")
        fn = getattr(self, f"_op_{kind}", None) if isinstance(kind, str) else None
        if fn is None:
            out.failed.append((op, f"unknown operation {kind!r}"))
            return False
        try:
            return bool(fn(st, dict(op), out))
        except (KeyError, TypeError, ValueError) as e:
            out.failed.append((op, f"{type(e).__name__}: {e}"))
            return False

    def _fail(self, out: Outcome, op: dict, why: str) -> bool:
        out.failed.append((op, why))
        return False

    def _ok(self, out: Outcome, op: dict, note: str, *cases: str) -> bool:
        out.applied.append(op)
        out.notes.append(note)
        out.changed += [c for c in cases if c not in out.changed]
        return True

    def _sweep_of(self, st: _State, op: dict, out: Outcome) -> dict | None:
        c = next((x for x in st.cases if x["id"] == op.get("case")), None)
        if c is None or c["kind"] != "sweep":
            self._fail(out, op, f"no sweep test {op.get('case')!r}")
            return None
        return c

    @staticmethod
    def _touch(c: dict, *fields: str) -> None:
        for f in fields:
            if f not in c["touched"]:
                c["touched"].append(f)

    def _op_add_sweep(self, st: _State, op: dict, out: Outcome) -> bool:
        hid = op.get("hazard")
        base = self.m["hazards"].get(hid)
        if base is None:
            return self._fail(out, op, f"{hid!r} is not a tough condition for this kind of machine")
        label = base["title"]
        c = next((x for x in st.cases if x["kind"] == "sweep" and x["sweep"]["hazard"] == hid), None)
        raw = [float(v) for v in op.get("levels") or []]
        desc = base["levels"][0] > base["levels"][-1]
        if c is None:
            levels = normalise_levels(raw or list(base["levels"]), base, out.clamps, label)
            ask = op.get("ask")
            ask_src = "user"
            if ask is not None:
                lo, hi = base["range"]
                ask, side = clamp(float(ask), lo, hi)
                if side:
                    out.clamps.append(words.clamp_note(label, op["ask"], ask, base.get("unit", ""), side))
                levels = normalise_levels(levels + [ask], base, [], label)
            else:
                ask = st.params.get(base.get("ask")) if base.get("ask") else None
                ask_src = "param"
            c = self._sweep_case({"id": hid, "title": label, "plain": base.get("plain", ""), "knob": base.get("knob"), "task_param": base.get("task_param"),
                                  "unit": base.get("unit", ""), "levels": levels}, ask, "user", ask_src)
            c["touched"] = ["created"]
            st.cases.append(c)
            stored = {"op": "add_sweep", "hazard": hid, "levels": levels, **({"ask": ask} if ask_src == "user" else {})}
            return self._ok(out, stored, words.test_added(label), c["id"])
        added = []
        levels = list(c["sweep"]["levels"])
        for v in raw or ([op["ask"]] if op.get("ask") is not None else []):
            x, side = clamp(float(v), *base["range"])
            if side:
                out.clamps.append(words.clamp_note(label, v, x, base.get("unit", ""), side))
            if not any(abs(l - x) < 1e-6 for l in levels):
                added.append(x)
            levels = ensure_level(levels, int(round(x)) if "task_param" in base else x, desc)
        if not added and op.get("ask") is None:
            return self._fail(out, op, words.already_there(f"The {label} test"))
        c["sweep"]["levels"] = normalise_levels(levels, base, [], label)
        self._touch(c, "levels")
        stored = {"op": "add_sweep", "hazard": hid, "levels": list(added)}
        if op.get("ask") is not None:
            c["sweep"]["ask"], c["sweep"]["ask_src"] = max(base["range"][0], min(base["range"][1], float(op["ask"]))), "user"
            self._touch(c, "ask")
            stored["ask"] = c["sweep"]["ask"]
        note = words.level_added(label, added[0], base.get("unit", "")) if len(added) == 1 else words.levels_set(label, c["sweep"]["levels"], base.get("unit", ""))
        return self._ok(out, stored, note, c["id"])

    def _op_add_fixed(self, st: _State, op: dict, out: Outcome) -> bool:
        fid = op.get("fault")
        if fid:
            f = self.m["faults"].get(fid)
            if f is None:
                return self._fail(out, op, f"{fid!r} is not a failure this kind of machine can be tested with")
            if next((x for x in st.cases if x["kind"] == "fixed" and x["fixed"]["fault"] == fid), None):
                return self._fail(out, op, words.already_there(f"The test \"{f['title']}\""))
            c = self._fault_case(f, "user")
            c["touched"] = ["created"]
            st.cases.append(c)
            return self._ok(out, {"op": "add_fixed", "fault": fid}, words.test_added(f["title"]), c["id"])
        raw = op.get("conditions") or {}
        allowed = allowed_knobs(self.platform)
        cond, parts = {}, []
        for k, v in raw.items():
            if k not in allowed:
                return self._fail(out, op, f"{k!r} cannot be set for this kind of machine (allowed: {', '.join(allowed)})")
            lo, hi = menu.RANGES[k]
            x, side = clamp(float(v), lo, hi)
            hz = next((h for h in self.m["hazards"].values() if h.get("knob") == k), None)
            unit = hz.get("unit", "") if hz else ""
            if side:
                out.clamps.append(words.clamp_note(hz["title"] if hz else k, v, x, unit, side))
            cond[k] = _r(x)
            parts.append(f"{hz['title'] if hz else k} {words.unit_text(cond[k], unit)}")
        if not cond:
            return self._fail(out, op, "a test needs at least one condition")
        for x in st.cases:
            if x["kind"] == "fixed" and x["fixed"]["fault"] is None and x["fixed"]["conditions"] == cond:
                return self._fail(out, op, words.already_there("That combination"))
        title = op.get("title") or " + ".join(parts)
        n = st.next_user
        st.next_user += 1
        c = self._fault_case({"id": f"u{n}", "title": title, "plain": "Your own combination of conditions, all at once.", "hazards": cond, "fix": ""}, "user", custom=True, cid=f"tc_u{n}")
        c["touched"] = ["created"]
        st.cases.append(c)
        return self._ok(out, {"op": "add_fixed", "conditions": cond, "title": title}, words.test_added(title), c["id"])

    def _op_add_untestable(self, st: _State, op: dict, out: Outcome) -> bool:
        title = str(op.get("title") or "").strip()[:80]
        if not title:
            return self._fail(out, op, "an untestable test needs a name")
        reason = str(op.get("reason") or words.UNTESTABLE_REASON["other"])[:220]
        cid = f"tc_n_{slug(op.get('key') or title)}"
        if any(x["id"] == cid for x in st.cases):
            return self._fail(out, op, words.already_there(f"\"{title}\" as not tested"))
        c = self._untestable_case(title, reason, cid, "user")
        c["touched"] = ["created"]
        st.cases.append(c)
        return self._ok(out, {"op": "add_untestable", "title": title, "reason": reason, "key": op.get("key") or slug(title)}, words.untestable_added(title, reason), c["id"])

    def _op_set_levels(self, st: _State, op: dict, out: Outcome) -> bool:
        c = self._sweep_of(st, op, out)
        if c is None:
            return False
        s = c["sweep"]
        base = self.m["hazards"][s["hazard"]]
        old_top = s["levels"][-1]
        levels = normalise_levels([float(v) for v in op.get("levels") or []], base, out.clamps, s["title"])
        if levels == s["levels"]:
            return self._fail(out, op, words.already_there(f"The {s['title']} levels"))
        s["levels"] = levels
        self._touch(c, "levels")
        same_but_top = levels[:-1] == [v for v in (op.get("before") or [])][:-1] if op.get("before") else False
        note = words.levels_top_changed(s["title"], old_top, levels[-1], s["unit"]) if (same_but_top or len(levels) == len(op.get("before") or [])) \
            else words.levels_set(s["title"], levels, s["unit"])
        return self._ok(out, {"op": "set_levels", "case": c["id"], "levels": levels}, note, c["id"])

    def _op_set_ask(self, st: _State, op: dict, out: Outcome) -> bool:
        c = self._sweep_of(st, op, out)
        if c is None:
            return False
        s = c["sweep"]
        base = self.m["hazards"][s["hazard"]]
        x, side = clamp(float(op["ask"]), *base["range"])
        if side:
            out.clamps.append(words.clamp_note(s["title"], op["ask"], x, s["unit"], side))
        x = int(round(x)) if "task_param" in base else _r(x)
        if s["ask"] == x and s["ask_src"] == "user":
            return self._fail(out, op, words.already_there(f"The {s['title']} level to cover"))
        s["ask"], s["ask_src"] = x, "user"
        self._touch(c, "ask")
        if op.get("insert", True) and not any(abs(v - x) < 1e-6 for v in s["levels"]):
            s["levels"] = normalise_levels(ensure_level(s["levels"], x, s["descending"]), base, [], s["title"])
            self._touch(c, "levels")
        return self._ok(out, {"op": "set_ask", "case": c["id"], "ask": x, "insert": op.get("insert", True)}, words.ask_set(s["title"], x, s["unit"]), c["id"])

    def _op_set_tries(self, st: _State, op: dict, out: Outcome) -> bool:
        n, side = clamp(int(round(float(op["n"]))), *TRIES_RANGE)
        if side:
            out.clamps.append(words.tries_clamp_note(op["n"], n))
        targets = [c for c in st.cases if c["status"] != "untestable" and (op.get("case") in (None, c["id"]))]
        if op.get("case") and not targets:
            return self._fail(out, op, f"no test {op.get('case')!r}")
        changed = [c for c in targets if c["tries"]["n"] != n]
        if not changed:
            return self._fail(out, op, words.already_there("The number of tries"))
        for c in changed:
            c["tries"]["n"] = n
            self._touch(c, "tries")
        title = targets[0]["title"] if op.get("case") else None
        return self._ok(out, {"op": "set_tries", "case": op.get("case"), "n": n}, words.tries_set(title, n), *[c["id"] for c in changed])

    def _op_set_bar(self, st: _State, op: dict, out: Outcome) -> bool:
        v, side = clamp(float(op["value"]), *BAR_RANGE)
        if side:
            out.clamps.append(words.bar_clamp_note(op["value"], v))
        v = round(v, 3)
        targets = [c for c in st.cases if c["status"] != "untestable" and (op.get("case") in (None, c["id"]))]
        if op.get("case") and not targets:
            return self._fail(out, op, f"no test {op.get('case')!r}")
        changed = [c for c in targets if c["pass"]["value"] != v or c["pass"]["owner"] != "user"]
        if not changed:
            return self._fail(out, op, words.already_there("The bar"))
        for c in changed:
            c["pass"] = {"metric": "success_rate", "op": ">=", "value": v, "owner": "user"}
            self._touch(c, "bar")
        title = targets[0]["title"] if op.get("case") else None
        return self._ok(out, {"op": "set_bar", "case": op.get("case"), "value": v}, words.bar_set(title, v), *[c["id"] for c in changed])

    def _op_set_task_param(self, st: _State, op: dict, out: Outcome) -> bool:
        name = op.get("name")
        rng = menu.TASK_PARAMS.get(st.task["preset"], {}).get(name)
        if rng is None:
            return self._fail(out, op, f"{name!r} is not a setting of the test course {st.task['preset']!r}")
        x, side = clamp(float(op["value"]), *rng)
        if side:
            out.clamps.append(words.clamp_note(name, op["value"], x, "m" if name.endswith("_m") else "", side))
        x = int(round(x)) if name == "debris" else _r(x)
        if st.task["params"].get(name) == x:
            return self._fail(out, op, words.already_there(f"The course {name}"))
        st.task["params"][name] = x
        st.touched_task.add(name)
        return self._ok(out, {"op": "set_task_param", "name": name, "value": x}, words.task_param_set(name.replace("_m", "").replace("_", " "), x, "m" if name.endswith("_m") else ""),
                        *[c["id"] for c in st.cases])

    def _op_set_job_number(self, st: _State, op: dict, out: Outcome) -> bool:
        name = op.get("name")
        if name not in menu.PARAMS_OF[self.platform]:
            return self._fail(out, op, f"{name!r} is not a number for a {self.platform} job")
        lo, hi, unit, _ = menu.PARAMS[name]
        x, side = clamp(float(op["value"]), lo, hi)
        if side:
            out.clamps.append(words.clamp_note(name.replace("_", " "), op["value"], x, unit if unit != "0/1" else "", side))
        x = int(round(x)) if name in INT_PARAMS else _r(x)
        if st.params.get(name) == x:
            return self._fail(out, op, words.already_there(f"The job's {name.replace('_', ' ')}"))
        st.params[name] = x
        st.touched_params.add(name)
        self._refresh_param_asks(st)
        out.job_changed = True
        return self._ok(out, {"op": "set_job_number", "name": name, "value": x}, words.job_number_set(name.replace("_", " "), x, unit if unit != "0/1" else ""),
                        *[c["id"] for c in st.cases if c["kind"] == "sweep" and c["sweep"]["ask_param"] == name])

    def _op_remove(self, st: _State, op: dict, out: Outcome) -> bool:
        c = next((x for x in st.cases if x["id"] == op.get("case")), None)
        if c is None:
            return self._fail(out, op, f"no test {op.get('case')!r}")
        st.cases.remove(c)
        title = c["title"] if c["kind"] != "sweep" else c["sweep"]["title"]
        return self._ok(out, {"op": "remove", "case": c["id"]}, words.test_removed(title), c["id"])

    # ------------------------------------------------------------ running
    def groups(self, ids: list[str] | None = None) -> list[dict]:
        """The testable cases to run, grouped so that one run_sweep call serves cases with the same test course, tries and bar. Each group carries the
        catalogue dict and `asked` dict run_sweep takes, exactly as `synthesize` would have built them for these cases."""
        picked = [c for c in self.testable() if ids is None or c["id"] in ids]
        keyed: dict[tuple, list[dict]] = {}
        for c in picked:
            keyed.setdefault((c["task"]["preset"], json.dumps(c["task"]["params"], sort_keys=True), c["tries"]["n"], c["pass"]["value"]), []).append(c)
        out = []
        for (preset, params, n, bar), cs in keyed.items():
            cat, asked = self.to_catalogue(cs)
            out.append({"cases": [c["id"] for c in cs], "cat": cat, "asked": asked, "episodes": n, "bar": bar, "preset": preset})
        return out

    def to_catalogue(self, cases: list[dict] | None = None) -> tuple[dict, dict]:
        """(catalogue, asked) for run_sweep from these cases (default: all testable ones). With every case and no edits this equals `synthesize(base)`."""
        cases = self.testable() if cases is None else [c for c in cases if c["status"] != "untestable"]
        hazards, faults, asked = [], [], {}
        for c in cases:
            if c["kind"] == "sweep":
                s = c["sweep"]
                h = dict(self.m["hazards"][s["hazard"]])
                h["levels"] = list(s["levels"])
                h.pop("range", None)
                if s["ask"] is not None:
                    h["ask"] = f"ask_{s['hazard']}"
                    asked[h["ask"]] = s["ask"]
                else:
                    h.pop("ask", None)
                hazards.append(h)
            else:
                f = c["fixed"]
                if f["fault"]:
                    faults.append(self.m["faults"][f["fault"]])
                else:
                    faults.append({"id": c["id"], "title": c["title"], "plain": f["plain"], "hazards": dict(f["conditions"]), "fix": f["fix"]})
        task = self.st.task
        thr = cases[0]["pass"]["value"] if cases else float(self.m["threshold"])
        cat = {"id": "plan", "platform": self.platform, "task": task["preset"], "task_params": dict(task["params"]), "threshold": thr,
               "baseline": self.m["baseline"], "hazards": hazards, "faults": faults,
               "not_tested": [c["not_tested_text"] for c in self.untestable()] + list(self.m["not_tested"])}
        return cat, asked

    def stress_plan(self) -> dict:
        """The catalogue-backed part of the cases in plan.stress form ({hazards: [{id, levels, ask}], faults: [ids]}): the way back to a plan."""
        return {"hazards": [{"id": c["sweep"]["hazard"], "levels": list(c["sweep"]["levels"]), "ask": c["sweep"]["ask"] if c["sweep"]["ask_src"] != "param" else None}
                            for c in self.st.cases if c["kind"] == "sweep"],
                "faults": [c["fixed"]["fault"] for c in self.st.cases if c["kind"] == "fixed" and c["fixed"]["fault"]]}

    def record(self, run_id: str, case_id: str, per_level: list[dict], sig: str, bar: float, expected: int, partial: bool = False) -> None:
        """Store what a run found for one case. `sig` is the input signature the case had when the run started (edits during a run make it stale);
        `expected` is how many levels that run was going to cover."""
        done = [lv for lv in per_level if lv.get("n")]
        verdict = None
        if done:
            verdict = "partial" if len(done) < expected else ("met" if all(lv["success"] >= bar for lv in done) else "below")
        self.runs[case_id] = {"run_id": run_id, "at": time.time(), "machine_sig": self.machine_sig, "input_sig": sig, "per_level": per_level, "verdict": verdict,
                              "bar": bar, "partial": partial}
        self.refresh()

    # ------------------------------------------------------------ views
    def cells(self, c: dict, overlay: dict | None = None) -> list[dict]:
        """One cell per level (one for a fixed test): met bar, below bar, running, queued, not run, or not tested."""
        if c["status"] == "untestable":
            return [{"key": f"{c['id']}:x", "value": None, "label": "not tested", "state": "not_tested", "success": None, "wins": 0, "n": 0, "text": f"{c['title']}: not tested"}]
        run = c.get("last_run") or {}
        have = {None if lv.get("value") is None else round(float(lv["value"]), 6): lv for lv in run.get("per_level", [])}
        bar = c["pass"]["value"]
        values = c["sweep"]["levels"] if c["kind"] == "sweep" else [None]
        unit = c["sweep"]["unit"] if c["kind"] == "sweep" else ""
        name = c["sweep"]["title"] if c["kind"] == "sweep" else c["title"]
        out = []
        for v in values:
            key = None if v is None else round(float(v), 6)
            lv = have.get(key)
            label = words.unit_text(v, unit) if v is not None else "this test"
            state, wins, n, success, ci = "not_run", 0, 0, None, None
            if lv and lv.get("n"):
                n, success, ci = lv["n"], lv["success"], lv.get("ci")
                wins = int(round(success * n))
                state = "met" if success >= bar else "below"
            elif lv and lv.get("error"):
                state = "error"
            over = (overlay or {}).get(c["id"], {}).get(key)
            if over in ("running", "queued"):
                state = over
            text = f"{name} {label}: " + {"met": "met bar", "below": "below bar", "running": "running", "queued": "queued", "not_run": "not run", "error": "could not run"}[state] + \
                (f", {wins} of {n} tries" if n else "")
            out.append({"key": f"{c['id']}:{'' if v is None else words.num(v)}", "value": v, "label": label, "state": state, "success": success, "wins": wins, "n": n, "ci": ci, "text": text})
        return out

    def view(self, overlay: dict | None = None) -> list[dict]:
        out = []
        for c in self.st.cases:
            v = copy.deepcopy(c)
            if "running" in ((overlay or {}).get(c["id"]) or {}).values():
                v["status"] = "running"
            v["cells"] = self.cells(c, overlay)
            v["conditions"] = self.n_conditions([c["id"]]) if c["status"] != "untestable" else 0
            out.append(v)
        return out

    def summary(self) -> dict:
        t = self.testable()
        return {"tests": len(t), "conditions": self.n_conditions(), "tries": self.n_tries(), "not_tested": len(self.not_tested_items()), "untestable": len(self.untestable()),
                "stale": sum(1 for c in self.st.cases if c["stale"]), "with_results": sum(1 for c in t if c.get("last_run"))}

    # ------------------------------------------------------------ save
    def to_dict(self) -> dict:
        return {"platform": self.platform, "base": self.base, "source": self.source, "machine_sig": self.machine_sig, "history": self.history, "cursor": self.cursor,
                "runs": self.runs}

    @classmethod
    def from_dict(cls, d: dict) -> "CaseBook":
        b = cls(d["platform"], d["base"], d.get("source", "catalogue"), d.get("machine_sig", ""))
        b.history, b.cursor, b.runs = list(d.get("history", [])), int(d.get("cursor", 0)), dict(d.get("runs", {}))
        b._materialise()
        return b


def results_matrix(book: CaseBook, overlay: dict | None = None, ids: list[str] | None = None) -> dict:
    """The Results card's matrix JSON: one row per case, one cell per level, plus the counts the headline uses."""
    rows, met, below, not_run, total = [], 0, 0, 0, 0
    for c in book.cases:
        if ids is not None and c["id"] not in ids:
            continue
        cells = book.cells(c, overlay)
        rows.append({"case_id": c["id"], "title": c["title"], "kind": c["kind"], "unit": c["sweep"]["unit"] if c["kind"] == "sweep" else "", "stale": c["stale"],
                     "untestable": c["status"] == "untestable", "bar": {"value": c["pass"]["value"], "owner": c["pass"]["owner"], "text": words.bar_text(c["pass"]["value"], c["pass"]["owner"])},
                     "tries": c["tries"]["n"], "cells": cells})
        if c["status"] != "untestable":
            for cell in cells:
                total += 1
                met += cell["state"] == "met"
                below += cell["state"] == "below"
                not_run += cell["state"] in ("not_run", "queued", "running", "error")
    bars = {r["bar"]["text"] for r in rows if not r["untestable"]}
    return {"rows": rows, "met": met, "below": below, "not_run": not_run, "total": total, "bar_text": next(iter(bars)) if len(bars) == 1 else "set per test (see each row)"}
