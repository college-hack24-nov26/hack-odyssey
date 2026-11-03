"""The chat engine: turns in, events out.

A turn is a sentence or a button press. `Engine.send` parses it (intents.py, no model), records it, and queues it on the chat's own worker, so turns apply in order and
`send` can answer 202 at once. What a turn starts (the draft pipeline, a stress test, a watched run, the AI plan) runs on its own thread and reports through events.

The first message of a new job produces an instant offline draft from code that already exists, step by step with the real seconds each took:
  understand (job match, numbers)  ->  design (parts search)  ->  check (the Builder's evaluator on each design)  ->  tests (from the catalogue)  ->
  watch (a calm run, only if the live view is free)  ->  parts (the build sheet).
When a key is saved and today's requests are not spent, the AI planner starts beside it for that new job only (never for an edit, a button, a chip or a question) and its
plan arrives as a Draft-vs-AI comparison, or becomes the plan directly when no ready-made job fits. The model is never called for anything else.

Nothing here moves hardware: there is no arm, drive or checklist action, only a link to the Hardware screen on the parts card.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from ..goals import all_goals, match, propose
from ..goals.assembly import make_sheet, render as render_sheet
from ..goals.design import part_names
from ..hazards import run_sweep, write_report
from ..hazards.sweep import STATEMENT, conditions as sweep_conditions
from ..llm import LLMError, menu, settings as llm_settings
from ..llm.planner import finish_from_plan, run_plan, watch_setup
from ..parts import Build, build_to_vehicle, evaluate, load_inventory
from . import cards, cases as C, diff as chatdiff, intents, words
from .budget import Budget
from .chat import Chat, replay
from .resources import Resources
from .store import ROOT, Store

STEPS = (("understand", "Understand"), ("design", "Design"), ("check", "Check"), ("tests", "Tests"), ("watch", "Watch"), ("parts", "Parts"))
NEEDS_PLAN = ("edit", "undo", "redo", "watch", "run_tests", "sheet", "pick_design", "hardware", "confirm_new")


def _gr():
    """The goal router *module* (importing it by name at call time: `from simlab.dashboard import goal_router` would give the router object, and a module-level
    import would be circular). `make_client` is looked up on it at call time so tests can swap in a fake OpenRouter."""
    return importlib.import_module("simlab.dashboard.goal_router")


def _lab():
    return importlib.import_module("simlab.dashboard.router")


def _err(e: BaseException) -> str:
    return llm_settings.scrub(f"{type(e).__name__}: {e}")[:300]


def _summary(text: str) -> str:
    return " ".join((text or "").split())[:140].rstrip(" .,;")


def _clamp_params(platform: str, params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if k in menu.PARAMS_OF[platform]:
            lo, hi = menu.PARAMS[k][:2]
            x = max(lo, min(hi, float(v)))
            out[k] = int(round(x)) if k in C.INT_PARAMS else round(x, 4)
    return out


class Engine:
    def __init__(self, store: Store | None = None, resources: Resources | None = None, budget: Budget | None = None, runner: Callable | None = None,
                 reports_root: Path | None = None, mujoco: bool | None = None, poll: float = 0.4):
        self.store = store or Store()
        self._get_runner = runner or (lambda: _lab().runner)
        self.resources = resources or Resources(runner=lambda: self._get_runner())
        self.budget = budget or Budget(self.store.root, client_factory=lambda: _gr().make_client())
        self.budget.install()
        self.reports_root = Path(reports_root) if reports_root else ROOT / "runs" / "reports"
        self.mujoco = (importlib.util.find_spec("mujoco") is not None) if mujoco is None else mujoco
        self.poll = poll
        self.chats: dict[str, Chat] = {}
        self._lock = threading.RLock()

    def close(self) -> None:
        self.budget.uninstall()

    # ================================================================ registry
    def new_chat(self, title: str | None = None) -> Chat:
        with self._lock:
            chat = Chat(self.store.new_id(), self.store, title or "New chat")
            self.chats[chat.id] = chat
        self._publish_idle(chat)
        chat.save()
        return chat

    def get(self, cid: str) -> Chat:
        with self._lock:
            if cid in self.chats:
                return self.chats[cid]
            if not self.store.exists(cid):
                raise KeyError(f"no chat {cid!r}")
            chat = self._load(cid)
            self.chats[cid] = chat
            return chat

    def latest(self) -> Chat | None:
        mem = max(self.chats.values(), key=lambda c: c.updated, default=None)
        disk = self.store.list()
        if disk and (mem is None or disk[0]["updated"] > mem.updated):
            return self.get(disk[0]["id"])
        return mem

    def list(self) -> list[dict]:
        seen = {c["id"]: c for c in self.store.list()}
        for c in self.chats.values():
            seen[c.id] = {"id": c.id, "title": c.title, "updated": c.updated, "seq": c.seq}
        return sorted(seen.values(), key=lambda c: c["updated"], reverse=True)

    def _load(self, cid: str) -> Chat:
        events, session = self.store.load(cid)
        chat = Chat(cid, None)
        chat.events, chat.seq = events, (events[-1]["seq"] if events else 0)
        chat.view = replay(events)
        if session:
            chat.title, chat.created, chat.updated = session.get("title", "New chat"), session.get("created", chat.created), session.get("updated", chat.updated)
            chat.s = session.get("s") or {"counters": {}}
            if session.get("book"):
                chat.book = C.CaseBook.from_dict(session["book"])
        chat.store = self.store                       # attached only now, so replaying did not write the log again
        raw = (chat.s.get("ai") or {}).get("raw_plan")
        pid = (chat.s.get("ai") or {}).get("plan_id")
        if raw and pid:
            self._remember_plan(pid, raw)             # the Goal tab's stress test can use this plan again after a restart
        self._mark_interrupted(chat)
        return chat

    def _mark_interrupted(self, chat: Chat) -> None:
        """A job that was running when the server stopped is gone: say so and offer Retry. (Jobs live in this process, so after a restart none of them is alive.)"""
        changed = False
        for cid, card in list(chat.view.cards.items()):
            if card.get("state") not in ("running", "queued", "starting"):
                continue
            changed = True
            retry = None
            if card["type"] == "results":
                chat.card_patch(cid, {"state": "interrupted", "progress": {"stopping": False}})
                retry = cards.chip("retry", words.CHIP["retry"], {"type": "run_tests", "args": {"cases": card.get("case_ids") or "all"}}, kind="primary")
                what = "The test run"
            elif card["type"] == "watch":
                runs = [dict(r, state="interrupted" if r["state"] == "running" else r["state"]) for r in card.get("runs", [])]
                chat.card_patch(cid, {"state": "interrupted", "runs": runs, "current": runs[-1] if runs else None})
                retry = cards.chip("retry", words.CHIP["retry"], {"type": "watch", "args": {}}, kind="primary")
                what = "The watched run"
            elif card["type"] == "improve":
                chat.card_patch(cid, {"state": "interrupted"})
                what = "The AI plan"
            else:
                chat.card_patch(cid, {"state": "interrupted"})
                what = "That step"
            chat.message("assistant", words.interrupted(what))
            if retry:
                chat.emit("chips", chips=[retry])
        for key in ("ai",):
            if (chat.s.get(key) or {}).get("state") in ("running", "queued"):
                chat.s[key]["state"] = "interrupted"
        st = chat.s.get("stress")
        if st and st.get("state") in ("running", "queued"):
            st["state"] = "interrupted"
        for run in chat.s.get("runs", []):
            if run.get("state") == "running":
                run["state"] = "interrupted"
        if changed:
            self._dock(chat, idle=True)
            chat.save()

    # ================================================================ publishing small things
    def _publish_idle(self, chat: Chat) -> None:
        chat.emit("budget", ai=self.budget.snapshot())
        self._limits(chat)
        self._chips(chat)
        self._dock(chat, idle=True)

    def _limits(self, chat: Chat) -> None:
        book = chat.book
        if book is None:
            payload = {"count": None, "stale": False, "line": words.EMPTY_LIMITS}
        else:
            n = len(book.not_tested_items())
            payload = {"count": n, "stale": book.any_stale(), "line": words.limit_line(n)}
        if {k: chat.view.limits.get(k) for k in payload} != payload:
            chat.emit("limits", **payload)

    def _budget_event(self, chat: Chat) -> None:
        snap = self.budget.snapshot()
        if chat.view.budget != snap:
            chat.emit("budget", ai=snap)

    def _dock(self, chat: Chat, idle: bool = False) -> None:
        p = chat.s.get("pipeline") or {"steps": [], "t0": None, "done": True}
        steps = [dict(x) for x in p["steps"]]
        stress = chat.s.get("stress") or {}
        if stress.get("state") in ("running", "queued", "stopping"):
            steps.append({"id": "stress", "label": "Stress test", "state": "running" if stress["state"] != "queued" else "pending", "detail": stress.get("label", "")})
        ai = chat.s.get("ai") or {}
        running_ai = ai.get("state") in ("running", "queued")
        elapsed = round(time.time() - p["t0"], 1) if p.get("t0") and not p.get("done") else p.get("elapsed", 0)
        queue = []
        for t in list(chat.tickets.values()):
            pos = (self.resources.plan if t.lane == "plan" else self.resources.sweep).position(t)
            if pos:
                queue.append({"id": t.id, "label": t.label, "position": pos, "cancel": {"type": "cancel_queued", "args": {"ticket": t.id}}})
        runs = chat.s.get("runs", [])
        payload = {"steps": steps, "elapsed": elapsed, "started": p.get("t0"), "done": bool(p.get("done")),
                   "ai": {"running": running_ai, "state": ai.get("state"), "phase": ai.get("phase"), "seconds": ai.get("seconds", 0)}, "queue": queue,
                   "busy": {"plan": running_ai, "sweep": stress.get("state") in ("running", "stopping"), "live": bool(runs and runs[-1]["state"] == "running")}}
        prev = dict(chat.view.dock)
        if not payload["done"]:                                    # while a step runs the page ticks `elapsed` itself from `started`: only a real change is an event
            prev.pop("elapsed", None)
            same = prev == {k: v for k, v in payload.items() if k != "elapsed"}
        else:
            same = prev == payload
        if not same:
            chat.emit("dock", **payload)

    def _step(self, chat: Chat, step_id: str, state: str, detail: str = "") -> None:
        p = chat.s["pipeline"]
        st = next(x for x in p["steps"] if x["id"] == step_id)
        st.update(state=state, detail=detail)
        if all(x["state"] in ("done", "skipped") for x in p["steps"]):
            p["done"], p["elapsed"] = True, round(time.time() - p["t0"], 1)
        self._dock(chat)

    def _begin_pipeline(self, chat: Chat) -> None:
        chat.s["pipeline"] = {"steps": [{"id": i, "label": label, "state": "pending", "detail": ""} for i, label in STEPS], "t0": time.time(), "done": False, "elapsed": 0}

    def _chips(self, chat: Chat) -> None:
        chips = self._suggest(chat)
        if chat.view.chips != chips:
            chat.emit("chips", chips=chips)

    def _suggest(self, chat: Chat) -> list[dict]:
        """Up to four chips for what is sensible next. Free local ones act at once; none of them spends an AI request."""
        book, s = chat.book, chat.s
        ai = s.get("ai") or {}
        if book is None:
            if ai.get("state") in ("running", "queued"):
                return [cards.chip("stop", words.CHIP["stop"], {"type": "stop", "args": {"job": "plan"}}, kind="primary")]
            out = [cards.chip(f"ex:{e['id']}", e["label"], {"type": "new_job", "args": {"text": e["text"]}}, tag="instant") for e in words.EXAMPLES]
            out.append(cards.chip(f"ex:{words.OWN_PARTS['id']}", words.OWN_PARTS["label"], {"type": "use_kit", "args": {"kit": words.OWN_PARTS["kit"]}}, tag="instant"))
            return out
        if ai.get("state") == "done" and ai.get("diff"):
            return [cards.chip("use_ai", words.CHIP["use_ai"], {"type": "use_ai", "args": {}}, kind="primary"), cards.chip("keep_mine", words.CHIP["keep_mine"], {"type": "keep_mine", "args": {}}),
                    cards.chip("use_ai_keep_edits", words.CHIP["use_ai_keep_edits"], {"type": "use_ai_keep_edits", "args": {}})]
        sw, live = self.resources.sweep.snapshot(), self.resources.live.snapshot()
        out = []
        runs = s.get("results", [])
        watch_ok, why = self.mujoco, (None if self.mujoco else words.watch_no_mujoco())
        n_run = len(book.testable())
        if runs and runs[-1].get("state") in ("done", "stopped"):
            last = runs[-1]
            out.append(cards.chip("watch_weakest", words.CHIP["watch_weakest"], {"type": "watch", "args": {"weakest": True}}, kind="primary", enabled=watch_ok and bool(last.get("weakest")), reason=why))
            out.append(cards.chip("make_tougher", words.CHIP["make_tougher"], {"type": "edit", "args": {"ops": self._tougher_ops(book)}}))
            out.append(cards.chip("get_sheet", words.CHIP["get_sheet"], {"type": "sheet", "args": {"format": "json"}}))
        else:
            out.append(cards.chip("watch_rough", words.CHIP["watch_rough"], {"type": "watch", "args": {"worst": True}}, enabled=watch_ok, reason=why))
            out.append(cards.chip("run_all", f"{words.CHIP['run_all']} ({n_run})", {"type": "run_tests", "args": {"cases": "all"}}, kind="primary", enabled=n_run > 0))
            first = next((c for c in book.cases if c["kind"] == "sweep"), None)
            if first:
                t = first["sweep"]["title"].lower()
                out.append(cards.chip("add_more", f"Make the {t} tougher", {"type": "edit", "args": {"ops": self._tougher_ops(book, [first["id"]])}}))
            out.append(cards.chip("parts", words.CHIP["parts"], {"type": "sheet", "args": {"format": "json"}}))
        if sw["busy"] and sw["holder"] and sw["holder"]["owner"] == chat.id:
            out = [cards.chip("stop", words.CHIP["stop"], {"type": "stop", "args": {"job": "tests"}}, kind="primary")] + out
        return out[:4]

    def _tougher_ops(self, book, ids: list[str] | None = None) -> list[dict]:
        return [{"op": "set_levels", "case": c["id"], "levels": intents._top_move(c, +1, book.platform)} for c in book.cases if c["kind"] == "sweep" and (ids is None or c["id"] in ids)]

    def _after(self, chat: Chat) -> None:
        self._limits(chat)
        self._chips(chat)
        self._dock(chat)
        chat.save()

    # ================================================================ sending
    def capabilities(self) -> dict:
        res = self.resources.snapshot()
        platforms = {}
        for p in menu.PLATFORMS:
            m = menu.load(p)
            platforms[p] = {
                "label": words.PLATFORM[p], "observation_only": p == "air", "threshold": m["threshold"], "presets": menu.task_presets(p), "not_tested": m["not_tested"],
                "hazards": [{"id": hid, "title": h["title"], "unit": h.get("unit", ""), "range": list(h["range"]), "levels": list(h["levels"]), "knob": h.get("knob") or h.get("task_param"),
                             "live": h.get("knob") in C.LIVE_KNOBS, "plain": h.get("plain", ""), "descending": h["levels"][0] > h["levels"][-1]} for hid, h in m["hazards"].items()],
                "faults": [{"id": fid, "title": f["title"], "plain": f.get("plain", "")} for fid, f in m["faults"].items()],
                "params": [{"key": k, "label": words.PARAM_LABEL.get(k, k), "unit": "" if menu.PARAMS[k][2] == "0/1" else menu.PARAMS[k][2], "min": menu.PARAMS[k][0], "max": menu.PARAMS[k][1]}
                           for k in menu.PARAMS_OF[p]]}
        return {"ai": self.budget.snapshot(), "mujoco": self.mujoco, "live": res["live"], "plan": res["plan"], "sweep": res["sweep"], "hardware": {"link": "/#hw"},
                "platforms": platforms, "kits": [{"id": k, "label": v["label"]} for k, v in _gr().KITS.items()], "examples": words.EXAMPLES + [words.OWN_PARTS],
                "copy": {"headline": words.EMPTY_HEADLINE, "line": words.EMPTY_LINE, "placeholder": words.EMPTY_PLACEHOLDER, "limits": words.EMPTY_LIMITS},
                "action_types": list(intents.ACTION_TYPES), "card_types": list(cards.words.TITLE)}

    def ctx(self, chat: Chat) -> intents.ParseCtx:
        with chat.cond:
            if chat.book is None:
                return intents.ParseCtx()
            return intents.ctx_from_book(chat.book, has_results=bool(chat.s.get("results")), pending_ai=bool((chat.s.get("ai") or {}).get("pending")))

    def send(self, chat: Chat, text: str | None = None, action: dict | None = None, client_msg_id: str | None = None, label: str | None = None) -> dict:
        """Record a turn and queue it. Returns what the 202 answer carries. Nothing slow happens here."""
        if client_msg_id:                                           # a page that retried a send must not run the turn twice
            with chat.cond:
                dup = next((m for m in chat.view.messages if m.get("client_msg_id") == client_msg_id), None)
            if dup is not None:
                i = dup.get("intent") or {}
                return {"turn_id": dup.get("turn_id"), "accepted": True, "duplicate": True, "message_id": dup["id"], "seq": chat.seq,
                        "intent": {"kind": i.get("kind"), "source": i.get("source"), "confidence": i.get("confidence"), "read_as": i.get("read_as")}}
        intent = intents.parse(text, action, self.ctx(chat))
        turn_id = chat.next_id("t")
        shown = text if text else (label or (action or {}).get("type", "") or "")
        user_msg = chat.message("user", shown, kind="text" if text else "action", turn_id=turn_id, client_msg_id=client_msg_id, intent=intent.to_dict())
        out = {"turn_id": turn_id, "accepted": True, "message_id": user_msg["id"], "intent": {"kind": intent.kind, "source": intent.source, "confidence": round(intent.confidence, 2),
                                                                                        "read_as": intent.read_as}, "seq": chat.seq}
        if intent.kind == "new_chat":
            child = self.new_chat()
            out["new_chat"] = child.id
            chat.message("assistant", words.new_chat_started(), kind="note", link={"chat": child.id})
            t = ((intent.action or {}).get("args") or {}).get("text") or ""
            if t.strip():
                self.send(child, text=t)
            self._enqueue(chat, lambda: self._after(chat))
            return out
        self._enqueue(chat, lambda: self._run_turn(chat, turn_id, intent, text))
        return out

    def _enqueue(self, chat: Chat, fn: Callable) -> None:
        with chat.cond:
            chat.turns.append(fn)
            if chat.worker is None:
                chat.worker = threading.Thread(target=self._worker, args=(chat,), daemon=True, name=f"chat-{chat.id[:6]}")
                chat.worker.start()

    def _worker(self, chat: Chat) -> None:
        while True:
            with chat.cond:
                if not chat.turns:
                    chat.worker = None
                    chat.cond.notify_all()
                    return
                fn = chat.turns.popleft()
            try:
                fn()
            except Exception as e:                                     # a bug in one turn must not take the chat down
                chat.emit("error", code="turn_failed", message=words.error_generic(_err(e)))
                chat.save()

    def wait_idle(self, chat: Chat, timeout: float = 120.0) -> bool:
        return chat.wait_idle(timeout)

    # ================================================================ one turn
    def _run_turn(self, chat: Chat, turn_id: str, intent: intents.Intent, text: str | None) -> None:
        kind = intent.kind
        a = (intent.action or {}).get("args") or {}
        if kind in NEEDS_PLAN and chat.book is None:
            self._say_blocked(chat, words.blocked_no_plan(), chips=self._suggest(chat))
            return self._after(chat)
        if kind == "edit":
            self._edit(chat, intent)
        elif kind == "undo":
            self._undo_redo(chat, redo=False)
        elif kind == "redo":
            self._undo_redo(chat, redo=True)
        elif kind == "new_job":
            if chat.book is not None:                                   # this chat has a job: ask, and keep the question's chips on screen
                self._confirm_new(chat, a.get("text") or text or "")
                return chat.save()
            self._new_job(chat, a.get("text") or text or "", a.get("kit"))
        elif kind == "use_kit":
            self._use_kit(chat, a.get("kit"))
        elif kind == "run_tests":
            self._run_tests(chat, a)
        elif kind == "watch":
            self._watch(chat, a, source="user")
        elif kind == "sheet":
            self._sheet(chat, a.get("format", "json"))
        elif kind == "stop":
            self._stop(chat, a.get("job", "all"))
        elif kind == "pick_design":
            self._pick_design(chat, a.get("index", 0))
        elif kind in ("use_ai", "keep_mine", "use_ai_keep_edits"):
            self._apply_ai(chat, kind)
        elif kind == "question":
            self._answer(chat, intent.topic or a.get("topic") or "overview", text or "")
        elif kind in ("hardware", "train", "help"):
            self._answer(chat, kind, text or "")
        elif kind == "confirm_new":
            self._confirm_new(chat, a.get("text") or text or "")
            return chat.save()                                          # keep the question's own chips on screen: the usual suggestions would replace them
        elif kind == "keep_here":
            pass
        elif kind == "cancel_queued":
            self._cancel_queued(chat, a.get("ticket"))
        elif kind == "open_details":
            pass                                                          # the page opens its own sheet; nothing changes on the server
        elif kind == "ambiguous":
            chat.message("assistant", words.needs_value("that"))
            chat.emit("chips", chips=intent.chips)
            return chat.save()
        elif kind == "unclear":
            chat.message("assistant", words.did_you_mean())
            chat.emit("chips", chips=intent.chips or self._suggest(chat))
            return chat.save()
        elif kind == "blocked":
            self._say_blocked(chat, (intent.issues or [words.blocked_hardware()])[0], chips=self._suggest(chat))
        else:
            chat.message("assistant", words.blocked_unknown_action(kind))
        self._after(chat)

    def _say_blocked(self, chat: Chat, reason: str, *, nearest: dict | None = None, chips: list | None = None, state: str = "blocked", link: dict | None = None,
                     ticket: dict | None = None) -> str:
        cid = f"blocked-{chat.next_id('b')[1:]}"
        card = cards.blocked(cid, reason, state=state, nearest=nearest, chips=chips, link=link, ticket=ticket)
        if state == "waiting":
            card["title"] = "Waiting"
        chat.put_card(card)
        chat.message("assistant", reason)
        return cid

    # ================================================================ a new job
    def _new_job(self, chat: Chat, text: str, kit: str | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return self._say_blocked(chat, words.blocked_no_plan(), chips=self._suggest(chat))
        if chat.book is not None:
            return self._confirm_new(chat, text)
        if kit:
            self._use_kit(chat, kit, quiet=True)
        hits = match(text)
        snap = self.budget.snapshot()
        ai_ok = snap["enabled"]
        if not hits and not ai_ok:
            reason = snap["reason"] or words.RESETS
            chat.message("assistant", words.ai_unavailable_no_draft(reason) if snap["configured"] else words.no_offline_draft())
            chat.emit("chips", chips=self._suggest(chat))
            chat.s["text"] = None
            return chat.save()
        chat.s["text"] = text
        chat.title = _summary(text)[:48] or chat.title
        chat.message("assistant", words.draft_started("draft" if hits else "ai"))
        with chat.cond:
            chat.gen += 1
            gen = chat.gen
        if hits:
            chat.job_started()
            threading.Thread(target=self._draft_thread, args=(chat, gen, text, hits[0]["goal"]), daemon=True, name="chat-draft").start()
        if ai_ok:
            self._start_ai(chat, text, with_draft=bool(hits), gen=gen)
        elif snap["configured"] and snap["reason"]:
            chat.message("assistant", words.ai_quota_gone() if snap["exhausted"] else words.ai_off())
        self._budget_event(chat)
        chat.save()

    def _use_kit(self, chat: Chat, kit: str | None, quiet: bool = False) -> None:
        kits = _gr().KITS
        if kit not in kits:
            return self._say_blocked(chat, words.blocked_unknown_action(str(kit)[:40]))
        chat.s["kit"], chat.s["owned"] = kit, dict(kits[kit]["owned"])
        if chat.book is None:
            if not quiet:
                chat.message("assistant", words.kit_loaded(kits[kit]["label"]))
            return
        chat.message("assistant", words.kit_set())
        self._redesign(chat)

    def _confirm_new(self, chat: Chat, text: str) -> None:
        chat.message("assistant", words.new_chat_question())
        chips = [cards.chip("new_chat", words.CHIP["new_chat"], {"type": "new_chat", "args": {"text": text}}, kind="primary"),
                 cards.chip("keep_here", words.CHIP["keep_here"], {"type": "keep_here", "args": {}})]
        chat.emit("chips", chips=chips)

    def _owned(self, chat: Chat) -> dict:
        return dict(chat.s["owned"]) if chat.s.get("owned") is not None else load_inventory()

    def _goal(self, chat: Chat):
        gid = chat.s.get("goal_id")
        return all_goals().get(gid) if gid else None

    # ------------------------------------------------------------ the draft pipeline
    def _draft_thread(self, chat: Chat, gen: int, text: str, goal_id: str) -> None:
        try:
            self._draft(chat, gen, text, goal_id)
        except Exception as e:
            if chat.gen == gen:
                chat.emit("error", code="draft_failed", message=words.error_generic(_err(e)))
                p = chat.s.get("pipeline")
                if p:
                    p["done"] = True
                    for st in p["steps"]:
                        if st["state"] == "running":
                            st["state"] = "skipped"
                    self._dock(chat)
            chat.save()
        finally:
            chat.job_done()

    def _draft(self, chat: Chat, gen: int, text: str, goal_id: str) -> None:
        goal = all_goals()[goal_id]
        platform = goal.platform
        self._begin_pipeline(chat)
        self._step(chat, "understand", "running")
        t = time.time()
        stated = _clamp_params(platform, intents.extract_job(text, platform))
        params = {**_clamp_params(platform, goal.params(None)), **stated}
        summary = _summary(text)
        plan = C.draft_plan(platform, params, summary, goal.card.get("blurb", ""))
        with chat.cond:
            if chat.gen != gen:
                return
            chat.book = C.CaseBook(platform, plan, "catalogue")
            chat.s.update(platform=platform, goal_id=goal.id, goal_title=goal.title, source="draft", stated=stated, summary=summary, proposals=[], pick=0, design={},
                          design_task=goal.task, runs=[], results=[], last_weakest=None, calm_started=False)
            chat.put_card(cards.understanding(chat.s, chat.book))
        self._limits(chat)
        self._step(chat, "understand", "done", f"{time.time() - t:.1f} s")

        self._step(chat, "design", "running")
        t = time.time()
        r = propose(goal.id, owned=self._owned(chat), params=chat.book.params)
        secs = round(time.time() - t, 2)
        with chat.cond:
            if chat.gen != gen:
                return
            self._store_design(chat, r["proposals"], {"scanned": r["scanned"], "meets_goal": r["meets_goal"], "note": r["note"], "seconds": secs})
            chat.put_card(cards.design(chat.s))
        self._step(chat, "design", "done", f"{secs:.1f} s")
        self._tail(chat, gen, check_first=True, auto_watch=True)

    def _store_design(self, chat: Chat, entries: list[dict], info: dict) -> None:
        props = [self._proposal(i, e) for i, e in enumerate(entries)]
        chat.s["proposals"], chat.s["pick"], chat.s["design"] = props, 0, info
        if props:
            chat.book.set_machine(props[0]["machine_sig"])

    @staticmethod
    def _proposal(i: int, e: dict) -> dict:
        b = e["build"]
        return {"index": i, "label": e["label"], "why": e.get("why", ""), "from_ai": bool(e.get("from_ai")), "verdict": e["verdict"], "checks": e["checks"], "unmet": e["unmet"],
                "warns": e["warns"], "task_ok": e["task_ok"], "parts": [{"name": n, "have": n in e["owned"]} for n in e["parts"]], "owned": e["owned"], "to_buy": e["to_buy"],
                "mass": e["mass"], "runtime": e["runtime"], "build": b, "vehicle": e["vehicle"], "machine_sig": C._sig(b), "pill": "Calibrated" if b.get("calibration") else "Estimate",
                "facts": [], "key_facts": [], "findings": [], "assumptions": [], "budget": {}, "evaluated": False}

    def _evaluate_proposals(self, chat: Chat) -> None:
        """The check step: the Builder's own evaluator on every design (facts with units, findings, assumptions)."""
        task = chat.s.get("design_task")
        for p in chat.s["proposals"]:
            ev = evaluate(p["build"], task, suggest=False)
            facts = {f["key"]: f for f in ev["facts"]}
            keys = [k for k in cards.KEY_FACTS[chat.s["platform"]] if k in facts] or list(facts)[:4]
            p.update(facts=ev["facts"], key_facts=[{"key": k, "label": facts[k]["label"], "value": facts[k]["value"], "unit": facts[k]["unit"]} for k in keys[:4]],
                     findings=[{"severity": f["severity"], "title": f["title"], "detail": f["detail"], "scope": f["scope"]} for f in ev["findings"]][:8],
                     assumptions=ev["assumptions"], budget=ev["budget"], evaluated=True)

    def _tail(self, chat: Chat, gen: int, check_first: bool, auto_watch: bool) -> None:
        """check, tests, watch, parts and the preview: what follows a design (the draft's and the AI's alike)."""
        if check_first:
            self._step(chat, "check", "running")
            t = time.time()
            with chat.cond:
                if chat.gen != gen:
                    return
                self._evaluate_proposals(chat)
                chat.put_card(cards.design(chat.s))
                chat.put_card(cards.understanding(chat.s, chat.book, self._requirements(chat)))
            self._step(chat, "check", "done", f"{time.time() - t:.1f} s")
        self._step(chat, "tests", "running")
        t = time.time()
        with chat.cond:
            if chat.gen != gen:
                return
            chat.put_card(cards.tests(chat.s, chat.book))
        self._limits(chat)
        self._step(chat, "tests", "done", f"{time.time() - t:.1f} s")
        self._step(chat, "watch", "running")
        started = False
        if auto_watch and chat.s.get("proposals") and chat.s.get("platform"):
            started = self._watch(chat, {}, source="auto")
        else:
            chat.put_card(cards.watch(chat.s, self.mujoco))
        self._step(chat, "watch", "done" if started else "skipped", "started" if started else "not started")
        self._step(chat, "parts", "running")
        t = time.time()
        with chat.cond:
            if chat.gen != gen:
                return
            if chat.s.get("proposals"):
                chat.put_card(self._parts_card(chat))
        self._step(chat, "parts", "done" if chat.s.get("proposals") else "skipped", f"{time.time() - t:.1f} s")
        self._preview(chat, gen)
        with chat.cond:
            if chat.gen != gen:
                return
            book = chat.book
            summ = book.summary()
            if not chat.s.get("done_message"):
                chat.s["done_message"] = True
                chat.message("assistant", words.draft_ready(summ["tests"], summ["conditions"], started))
        self._after(chat)

    def _requirements(self, chat: Chat) -> list[str]:
        props = chat.s.get("proposals") or []
        return [c["text"] for c in props[chat.s.get("pick", 0)]["checks"]] if props else []

    def _preview(self, chat: Chat, gen: int) -> None:
        """The see-through picture of the picked design (the simulator's own vehicle model with the parts inside), drawn by the page. Needs MuJoCo."""
        props = chat.s.get("proposals") or []
        if not props:
            return
        p = props[chat.s.get("pick", 0)]
        prev: dict
        try:
            from ..parts.preview import preview
            b = Build.from_dict(p["build"])
            spec, _ = build_to_vehicle(b)
            out = preview(b, spec)
            prev = {"state": "ready", "scene": out["scene"], "poses": out["poses"]}
        except ImportError:
            prev = {"state": "unavailable", "reason": words.PREVIEW_UNAVAILABLE}
        except Exception as e:
            prev = {"state": "unavailable", "reason": llm_settings.scrub(f"{type(e).__name__}: {e}")[:160]}
        with chat.cond:
            if chat.gen == gen and chat.s.get("proposals") and chat.s["proposals"][chat.s.get("pick", 0)]["machine_sig"] == p["machine_sig"]:
                chat.s["preview"] = prev
                chat.put_card(cards.design(chat.s, prev))

    # ------------------------------------------------------------ parts and the build sheet
    def _sheet_json(self, chat: Chat) -> dict:
        p = chat.s["proposals"][chat.s.get("pick", 0)]
        goal = self._goal(chat)
        gd = goal.to_dict() if goal else {"title": chat.s.get("summary") or "Your job", "platform": chat.s["platform"]}
        return make_sheet({"build": p["build"]}, gd, self._owned(chat))

    def _parts_card(self, chat: Chat, export: dict | None = None) -> dict:
        sheet = self._sheet_json(chat)
        old = chat.view.cards.get("parts") or {}
        return cards.parts(chat.s, chat.book, sheet, cards.rover_link(bool(sheet["wiring"]["generated"]) and chat.s["platform"] == "ground"), export or old.get("export"))

    def _sheet(self, chat: Chat, fmt: str) -> None:
        props = chat.s.get("proposals") or []
        if not props:
            return self._say_blocked(chat, words.blocked_no_design(), chips=self._suggest(chat))
        export = None
        if fmt == "html":
            sheet = self._sheet_json(chat)
            name = f"{time.strftime('%Y%m%d_%H%M%S')}_sheet_{chat.s.get('goal_id') or 'plan'}_{uuid.uuid4().hex[:6]}"
            out = self.reports_root / name
            out.mkdir(parents=True, exist_ok=True)
            (out / "sheet.html").write_text(render_sheet(sheet), encoding="utf-8")
            export = {"url": f"/reports/{name}/sheet.html", "name": name}
        chat.put_card(self._parts_card(chat, export))
        sheet = self._sheet_json(chat)
        n_buy = sum(1 for r in sheet["parts"] if r["have"] < r["qty"])
        chat.message("assistant", words.sheet_exported() if export else words.sheet_ready(len(sheet["parts"]), n_buy))

    def _pick_design(self, chat: Chat, index) -> None:
        props = chat.s.get("proposals") or []
        try:
            i = int(index)
        except (TypeError, ValueError):
            i = -1
        if not 0 <= i < len(props):
            return self._say_blocked(chat, words.blocked_no_design())
        with chat.cond:
            chat.s["pick"] = i
            chat.book.set_machine(props[i]["machine_sig"])
            chat.put_card(cards.design(chat.s, None))
            chat.put_card(cards.understanding(chat.s, chat.book, self._requirements(chat)))
            chat.put_card(cards.tests(chat.s, chat.book))
            chat.put_card(self._parts_card(chat))
            self._mark_results_stale(chat)
        chat.message("assistant", words.design_picked(props[i]["label"]))
        self._preview(chat, chat.gen)

    def _redesign(self, chat: Chat) -> None:
        """The job's numbers changed: search and check the designs again (cheap, offline; for an AI plan its own designs are re-checked and the library searched if they
        fail, with no model call)."""
        with chat.cond:
            chat.gen += 1
            gen = chat.gen
        chat.job_started()
        threading.Thread(target=self._redesign_thread, args=(chat, gen), daemon=True, name="chat-redesign").start()

    def _redesign_thread(self, chat: Chat, gen: int) -> None:
        try:
            book, t = chat.book, time.time()
            owned = self._owned(chat)
            if chat.s.get("source") == "ai":
                raw = copy.deepcopy(book.base)
                raw["params"] = dict(book.params)
                done = finish_from_plan(raw, owned)
                entries, info = done["proposals"], {"scanned": done["scanned"], "meets_goal": done["meets_goal"], "note": done["note"]}
            else:
                r = propose(chat.s["goal_id"], owned=owned, params=book.params)
                entries, info = r["proposals"], {"scanned": r["scanned"], "meets_goal": r["meets_goal"], "note": r["note"]}
            info["seconds"] = round(time.time() - t, 2)
            with chat.cond:
                if chat.gen != gen:
                    return
                self._store_design(chat, entries, info)
                self._evaluate_proposals(chat)
                chat.put_card(cards.design(chat.s))
                chat.put_card(cards.understanding(chat.s, chat.book, self._requirements(chat)))
                chat.put_card(cards.tests(chat.s, chat.book))
                if chat.s["proposals"]:
                    chat.put_card(self._parts_card(chat))
                self._mark_results_stale(chat)
                head = chat.s["proposals"][0]["verdict"]["headline"] if chat.s["proposals"] else ""
            chat.message("assistant", words.design_redone(head))
            self._preview(chat, gen)
            self._after(chat)
        except Exception as e:
            chat.emit("error", code="redesign_failed", message=words.error_generic(_err(e)))
            chat.save()
        finally:
            chat.job_done()

    def _mark_results_stale(self, chat: Chat) -> None:
        book = chat.book
        for r in chat.s.get("results", []):
            card = chat.view.cards.get(r["card"])
            if card:
                stale = any((book.get(i) or {}).get("stale") for i in r["case_ids"])
                if card.get("stale") != stale:
                    chat.card_patch(r["card"], {"stale": stale})

    # ================================================================ edits
    def _edit(self, chat: Chat, intent: intents.Intent) -> None:
        book = chat.book
        with chat.cond:
            before_params = dict(book.params)
            label = None
            out = book.apply(intent.ops, label=label or "")
            changed_params = book.params != before_params
        if out.clamps and not out.applied:
            chat.message("assistant", " ".join(out.clamps))
        if out.applied:
            if book.history:
                book.history[book.cursor - 1]["label"] = "; ".join(n.rstrip(".") for n in out.notes)
            chat.message("assistant", words.read_as("; ".join(n.rstrip(".") for n in out.notes) + "."), kind="read_as", turn_id=None,
                         undo={"type": "undo", "args": {}}, ops=out.applied)
            if out.clamps:
                chat.message("assistant", " ".join(out.clamps), kind="note")
            for op in out.applied:
                if op["op"] == "add_untestable":
                    key = op.get("key", "other")
                    nearest = None
                    first = next((c for c in book.cases if c["kind"] == "sweep"), None)
                    if first:
                        nearest = {"label": f"Try stronger {first['sweep']['title'].lower()} instead",
                                   "action": {"type": "edit", "args": {"ops": self._tougher_ops(book, [first["id"]])}}}
                    self._say_blocked(chat, words.untestable_added(op["title"], op["reason"]), nearest=nearest, chips=[cards.chip("n", nearest["label"], nearest["action"])] if nearest else [])
            self._publish_after_edit(chat, changed_params, out)
        else:
            for _, why in out.failed:
                if not out.clamps:
                    chat.message("assistant", why if not why.startswith(("KeyError", "TypeError", "ValueError", "unknown operation", "no ")) else words.error_generic(why))
            for i in intent.issues:
                chat.message("assistant", i)
            if not out.failed and not out.clamps and not intent.issues:
                chat.message("assistant", words.did_you_mean())
        if changed_params and out.job_changed:
            self._redesign(chat)

    def _publish_after_edit(self, chat: Chat, changed_params: bool, out) -> None:
        with chat.cond:
            chat.put_card(cards.tests(chat.s, chat.book))
            if changed_params or out.changed:
                chat.put_card(cards.understanding(chat.s, chat.book, self._requirements(chat)))
            if "limits" in chat.view.cards:
                chat.put_card(cards.limits(chat.book, chat.s.get("platform")))
            self._mark_results_stale(chat)

    def _undo_redo(self, chat: Chat, redo: bool) -> None:
        book = chat.book
        with chat.cond:
            before = dict(book.params)
            label = book.redo() if redo else book.undo()
            changed = book.params != before
        if label is None:
            return chat.message("assistant", words.nothing_to_redo() if redo else words.nothing_to_undo()) and None
        chat.message("assistant", (words.redone if redo else words.undone)(label.rstrip(".")), kind="note")
        with chat.cond:
            chat.put_card(cards.tests(chat.s, book))
            chat.put_card(cards.understanding(chat.s, book, self._requirements(chat)))
            if "limits" in chat.view.cards:
                chat.put_card(cards.limits(book, chat.s.get("platform")))
            self._mark_results_stale(chat)
        if changed:
            self._redesign(chat)

    # ================================================================ questions and links
    def _answer(self, chat: Chat, topic: str, question: str) -> None:
        book, s = chat.book, chat.s
        n_nt = cards.n_not_tested(book)
        if topic == "help":
            return chat.message("assistant", words.help_text()) and None
        if topic == "hardware":
            chat.message("assistant", words.blocked_hardware())
            if s.get("proposals"):
                chat.put_card(self._parts_card(chat))
            return
        if topic == "train":
            return self._say_blocked(chat, words.blocked_training(), link={"label": words.TRAIN_LINK_LABEL, "href": "/#train", "leaves_chat": True})
        if topic == "limits":
            chat.put_card(cards.limits(book, s.get("platform")))
            chat.message("assistant", words.answer_limits())
            return
        cid = f"answer-{chat.next_id('a')[1:]}"
        details: list[str] = []
        if book is None:
            text = words.blocked_no_plan()
        elif topic == "why_fail":
            text, details = self._why_fail(chat)
        elif topic == "what_means":
            text = words.answer_what_means(C.results_matrix(book)["bar_text"])
        elif topic == "cost":
            sm = book.summary()
            text = words.answer_cost(sm["tests"], sm["conditions"], sm["tries"])
        elif topic == "can_it":
            props = s.get("proposals") or []
            res = (s.get("results") or [None])[-1]
            text = words.answer_can_it(props[s.get("pick", 0)]["verdict"]["headline"] if props else None, res["headline"] if res else None)
        else:
            sm = book.summary()
            props = s.get("proposals") or []
            res = (s.get("results") or [None])[-1]
            text = words.answer_overview(s["platform"], sm["tests"], sm["with_results"], sm["stale"], props[s.get("pick", 0)]["verdict"]["headline"] if props else None)
        chat.put_card(cards.answer(cid, topic, question, text, details, n_nt))
        chat.message("assistant", text)

    def _why_fail(self, chat: Chat) -> tuple[str, list[str]]:
        book, w = chat.book, chat.s.get("last_weakest")
        if not chat.s.get("results"):
            return words.answer_no_results(), []
        if not w:
            return words.answer_everything_met(), []
        case = book.get(w["case_id"])
        run = (case or {}).get("last_run") or book.runs.get(w["case_id"]) or {}
        lv = next((x for x in run.get("per_level", []) if (x.get("value") is None and w.get("level") is None) or (x.get("value") is not None and w.get("level") is not None
                                                                                                                   and abs(x["value"] - w["level"]) < 1e-6)), None)
        if not lv:
            return words.answer_everything_met(), []
        cond = words.unit_text(w["level"], (case or {}).get("sweep", {}).get("unit", "")) if w.get("level") is not None else "this test"
        wins = int(round(lv["success"] * lv["n"]))
        return words.answer_why_fail(w["title"], cond, wins, lv["n"], lv.get("crashes", {}), lv.get("timeouts", 0), lv.get("mean_time")), \
            [f"try {s['seed'] + 1}: " + ("reached the goal" if s["success"] else f"did not reach the goal ({s.get('crash') or ('timeout' if s['timeout'] else 'no result')})") for s in lv.get("per_seed", [])]

    # ================================================================ watching
    def _watch_task(self, chat: Chat) -> dict:
        from ..tasks import get_task_spec
        preset = chat.s.get("design_task") or chat.book.task["preset"]
        d = get_task_spec(preset).to_dict()
        extra = chat.book.task["params"] if preset == chat.book.task["preset"] else {}
        d["params"] = {**d.get("params", {}), **extra}
        return d

    def _watch_conditions(self, chat: Chat, a: dict) -> tuple[dict, str, str | None]:
        """(Lab hazards, plain text of them, a reason it cannot be watched). Faults and fixed obstacles are not watchable yet; mud is a share of the machine's pulling
        force and is converted to the Lab's absolute unit here, on the server."""
        book = chat.book
        picked: dict[str, float] = {}

        def level_of(c: dict, level) -> float | None:
            if c["kind"] == "sweep":
                return c["sweep"]["levels"][-1] if level is None else level
            return None

        if a.get("weakest") and chat.s.get("last_weakest"):
            w = chat.s["last_weakest"]
            a = {**a, "case": w["case_id"], "level": w.get("level"), "worst": True}
        if a.get("conditions"):
            for k, v in a["conditions"].items():
                if k in C.LIVE_KNOBS:
                    picked[k] = float(v)
        elif a.get("case"):
            c = book.get(a["case"])
            if c is None:
                return {}, "", words.no_such_test(str(a["case"]))
            if not c.get("watchable"):
                return {}, "", words.watch_not_watchable(c["title"])
            if c["kind"] == "sweep":
                picked[c["sweep"]["knob"]] = float(level_of(c, a.get("level")))
            else:
                picked.update({k: float(v) for k, v in c["fixed"]["conditions"].items() if k in C.LIVE_KNOBS})
        elif a.get("worst"):
            for c in book.cases:
                if c["kind"] == "sweep" and c.get("watchable") and c["sweep"]["ask"] is not None:
                    picked[c["sweep"]["knob"]] = float(c["sweep"]["ask"])
            if not picked:
                first = next((c for c in book.cases if c["kind"] == "sweep" and c.get("watchable")), None)
                if first:
                    picked[first["sweep"]["knob"]] = float(first["sweep"]["levels"][-1])
        spec_params = (chat.s["proposals"][chat.s.get("pick", 0)]["vehicle"] or {}).get("params", {})
        text_parts, hz = [], {}
        by_knob = {c["sweep"]["knob"]: c for c in book.cases if c["kind"] == "sweep"}
        for k, v in picked.items():
            c = by_knob.get(k)
            unit = c["sweep"]["unit"] if c else ""
            name = c["sweep"]["title"].lower() if c else k
            text_parts.append(f"{name} {words.unit_text(v, unit if unit != 'grip' else '')}".strip())
            if k == "drag_ms2":
                hz[k] = v * float(spec_params.get("max_accel", 0.5))
            else:
                hz[k] = v
        return hz, ", ".join(text_parts) or "calm conditions", None

    def _watch(self, chat: Chat, a: dict, source: str = "user") -> bool:
        """Start the live view. Returns True when a run started. The client never sends a vehicle: everything is resolved here from the chosen design."""
        s = chat.s
        if not s.get("proposals"):
            self._say_blocked(chat, words.watch_no_design()) if source == "user" else None
            return False
        if not self.mujoco:
            chat.put_card(cards.watch(s, False))
            if source == "user":
                self._say_blocked(chat, words.watch_no_mujoco())
            return False
        hz, ctext, why = self._watch_conditions(chat, a)
        if why:
            self._say_blocked(chat, why, nearest={"label": words.CHIP["watch_calm"], "action": {"type": "watch", "args": {}}}, chips=[cards.chip("calm", words.CHIP["watch_calm"], {"type": "watch", "args": {}})])
            return False
        try:
            vehicle, task, rt = self._resolve_watch(chat, hz)
        except Exception as e:
            self._say_blocked(chat, words.error_generic(_err(e)))
            return False
        runner = self._get_runner()
        n = len(s.get("runs", [])) + 1
        run_id = f"w{n}"
        label = words.watch_label(ctext) if not hz else f"{words.watch_label(ctext)} {words.WATCH_SIMILAR}"
        run = {"id": run_id, "n": n, "label": label, "source": source, "conditions": {k: round(v, 4) for k, v in hz.items()}, "conditions_text": ctext, "state": "starting",
               "started": time.time(), "scene_id": None, "result": None, "final": None, "note": None, "similar": bool(hz)}

        def on_preempt(old):                                         # runs when a newer watch takes the view from THIS one
            self._finish_run(chat, run_id, "replaced", note=words.watch_replaced())

        def start(ticket):
            with chat.cond:
                s.setdefault("runs", []).append(run)
                run["state"] = "running"
                chat.tickets[ticket.id] = ticket
                chat.put_card(cards.watch(s, True))
            try:
                runner.start(vehicle, task, policy="baseline", backend="mujoco", speed=1.0, seed=0, loop=False, hazards=rt)    # seed 0 is "try 1", the same first try a stress test runs
            except Exception as e:
                self._finish_run(chat, run_id, "error", note=_err(e))
                self.resources.live.release(ticket)
                return
            threading.Thread(target=self._monitor, args=(chat, run_id, ticket, runner), daemon=True, name="chat-watch").start()

        ticket = self.resources.request_live(chat.id, "Watch it work", start, auto=(source == "auto"), on_preempt=on_preempt)
        if ticket is None:
            with chat.cond:
                chat.put_card(cards.watch(s, True, busy=True))
            if source == "user":
                self._say_blocked(chat, words.blocked_waiting_live(), state="waiting")
            return False
        if source != "auto":
            sw = self.resources.sweep.snapshot()
            msg = words.watch_started(ctext, source)
            if sw["busy"]:
                msg += " " + words.watch_may_be_slow()
            chat.message("assistant", msg)
        return True

    def _resolve_watch(self, chat: Chat, hz: dict):
        """VehicleSpec, TaskSpec and the Lab's cleaned hazards, validated the way POST /api/lab/start does."""
        from ..registry import TASKS
        from ..spec.task import TaskSpec
        from ..spec.vehicle import VehicleSpec
        vehicle = VehicleSpec.from_dict(chat.s["proposals"][chat.s.get("pick", 0)]["vehicle"])
        task = TaskSpec.from_dict(self._watch_task(chat))
        vehicle.check()
        cls = TASKS.get(task.task)
        if "mujoco" not in cls.backends:
            raise ValueError(f"task {task.name!r} runs on {list(cls.backends)}, not on mujoco")
        cls(task, vehicle)
        return vehicle, task, _lab()._clean_hazards(hz)

    def _finish_run(self, chat: Chat, run_id: str | None, state: str, final: dict | None = None, note: str | None = None, result: dict | None = None) -> None:
        if run_id is None:
            return
        with chat.cond:
            run = next((r for r in chat.s.get("runs", []) if r["id"] == run_id), None)
            if run is None or run["state"] not in ("running", "starting"):
                return
            run.update(state=state, final=final, note=note, result=result, finished=time.time())
            chat.put_card(cards.watch(chat.s, self.mujoco))
        self._dock(chat)
        chat.save()

    def _monitor(self, chat: Chat, run_id: str, ticket, runner) -> None:
        """Follow the shared simulation runner for this run: its result, or the moment something else (another tab, the Lab tab) replaces it."""
        t0, ours = time.time(), None
        try:
            while time.time() - t0 < 1800:
                time.sleep(self.poll)
                run = next((r for r in chat.s.get("runs", []) if r["id"] == run_id), None)
                if run is None or run["state"] != "running":
                    return
                st = dict(runner.state() or {})
                sid = st.get("scene_id")
                if sid and ours is None:
                    ours = sid
                    with chat.cond:
                        run["scene_id"] = sid
                        chat.put_card(cards.watch(chat.s, self.mujoco))
                if st.get("status") == "error":
                    return self._finish_run(chat, run_id, "error", note=llm_settings.scrub(str(st.get("error", "")))[:200])
                if ours and sid and sid != ours:
                    return self._finish_run(chat, run_id, "replaced", note=words.watch_replaced())
                res = st.get("result")
                if res and st.get("status") in ("finished", "episode done"):
                    final = {"success": bool(res.get("success")), "t": res.get("t"), "crash": res.get("crash"), "timeout": bool(res.get("timeout")), "collisions": res.get("collisions"),
                             "battery": st.get("battery")}
                    note = words.watch_finished(final["success"], final["t"] or 0)
                    return self._finish_run(chat, run_id, "finished", final=final, note=note, result=res)
                if not st.get("running") and st.get("status") in ("idle", "error") and time.time() - t0 > 3:
                    return self._finish_run(chat, run_id, "stopped", note="Stopped.")
        finally:
            self.resources.live.release(ticket)
            chat.tickets.pop(ticket.id, None)
            self._dock(chat)

    # ================================================================ stress tests
    def _ids_for_run(self, chat: Chat, arg) -> list[str] | None:
        if arg in (None, "all"):
            return None
        ids = [i for i in (arg if isinstance(arg, list) else [arg]) if chat.book.get(i) and chat.book.get(i)["status"] != "untestable"]
        return ids

    def _run_tests(self, chat: Chat, a: dict) -> None:
        book, s = chat.book, chat.s
        if not s.get("proposals"):
            return self._say_blocked(chat, words.blocked_no_design(), chips=self._suggest(chat))
        ids = self._ids_for_run(chat, a.get("cases"))
        with chat.cond:
            groups = book.groups(ids)
            case_ids = [i for g in groups for i in g["cases"]]
            if not case_ids:
                return chat.message("assistant", words.tests_nothing()) and None
            snap = {"sigs": {i: book.case_sig(book.get(i)) for i in case_ids}, "expected": {i: (len(book.get(i)["sweep"]["levels"]) if book.get(i)["kind"] == "sweep" else 1) for i in case_ids},
                    "bars": {i: book.get(i)["pass"]["value"] for i in case_ids}, "titles": {i: book.get(i)["title"] for i in case_ids}}
            n = int(chat.next_id("r")[1:])
            run = {"id": f"r{n}", "card": f"results-{n}", "n": n, "started": time.time(), "case_ids": case_ids, "state": "queued", "conditions": book.n_conditions(case_ids), "tries": book.n_tries(case_ids)}
            machine = s["proposals"][s.get("pick", 0)]
        cancel = threading.Event()

        def start(ticket):
            chat.job_started()                                       # counted before the thread exists, so the chat is never "idle" between the turn and the work
            threading.Thread(target=self._sweep_thread, args=(chat, ticket, run, groups, snap, machine, cancel), daemon=True, name="chat-sweep").start()

        ticket = self.resources.sweep.request(chat.id, f"Run {words.plural(len(case_ids), 'test')}", start, on_cancel=lambda t: cancel.set())
        ticket.card = run["card"]                                    # so withdrawing a queued ticket can say which results card it was
        chat.tickets[ticket.id] = ticket
        chat.cancel[run["id"]] = cancel
        if ticket.state == "queued":
            pos = self.resources.sweep.position(ticket) or 1
            with chat.cond:
                if (s.get("stress") or {}).get("state") not in ("running", "stopping"):         # a run in progress keeps its own state in the dock
                    s["stress"] = {"state": "queued", "run": run["id"], "label": f"queued, position {pos}"}
                chat.put_card(cards.results(run["card"], run, book, self._overlay(book, run, snap, None, queued=True), "", None, None, "queued", self._progress(run, 0, 0, "queued")))
            chat.emit("job", job={"id": run["id"], "kind": "tests", "state": "queued", "label": f"Run {words.plural(len(case_ids), 'test')}", "done": 0, "total": run["conditions"], "position": pos})
            self._say_blocked(chat, words.tests_queued(pos), state="waiting", ticket={"id": ticket.id, "position": pos, "cancel": {"type": "cancel_queued", "args": {"ticket": ticket.id}}})
            self._dock(chat)
        else:
            chat.message("assistant", words.tests_started(run["conditions"], run["tries"]))

    @staticmethod
    def _progress(run: dict, done: int, tries_done: int, label: str, stopping: bool = False) -> dict:
        return {"done": done, "total": run["conditions"], "tries_done": tries_done, "tries_total": run["tries"], "label": label, "stopping": stopping}

    def _overlay(self, book, run: dict, snap: dict, order_done: int | None, queued: bool = False, order: list | None = None) -> dict:
        """Which cells of this run are running or waiting, for the matrix. `order` is every (case id, level) in the order the sweep will do them."""
        ov: dict = {}
        if queued or order is None:
            for i in run["case_ids"]:
                c = book.get(i)
                vals = c["sweep"]["levels"] if c["kind"] == "sweep" else [None]
                ov[i] = {(None if v is None else round(float(v), 6)): "queued" for v in vals}
            return ov
        for pos, (cid, v) in enumerate(order):
            if pos >= (order_done or 0):
                ov.setdefault(cid, {})[None if v is None else round(float(v), 6)] = "running" if pos == order_done else "queued"
        return ov

    def _case_of(self, run: dict, cond: dict) -> str | None:
        cands = [f"tc_{cond['id']}"] if cond["kind"] == "hazard" else [f"tc_f_{cond['id']}", cond["id"]]
        return next((c for c in cands if c in run["case_ids"]), None)

    def _sweep_thread(self, chat: Chat, ticket, run: dict, groups: list[dict], snap: dict, machine: dict, cancel: threading.Event) -> None:
        s, book = chat.s, chat.book
        results, acc = [], {}
        done = tries_done = 0
        report_urls: list[str] = []
        state = "done"
        try:
            b = machine["build"]
            spec, _ = build_to_vehicle(Build.from_dict(b))
            if spec is None:
                raise ValueError("that design does not make a valid machine")
            ctx = {"max_accel": float(spec.params.get("max_accel", 0.5)), "thrusters": int(b.get("drive", {}).get("count", 1))}
            order = [(self._case_of(run, c), (c["value"] if c["kind"] == "hazard" else None)) for g in groups for c in sweep_conditions(g["cat"], ctx)]
            order = [(cid, v) for cid, v in order if cid]
            with chat.cond:
                run["state"] = "running"
                s["stress"] = {"state": "running", "run": run["id"], "label": f"condition 1 of {run['conditions']}"}
                chat.put_card(cards.results(run["card"], run, book, self._overlay(book, run, snap, 0, order=order), "", None, None, "running", self._progress(run, 0, 0, "starting")))
            chat.emit("job", job={"id": run["id"], "kind": "tests", "state": "running", "label": f"Run {words.plural(len(run['case_ids']), 'test')}", "done": 0, "total": run["conditions"]})
            self._dock(chat)
            chat.save()                                              # so a crash from here on is found, and shown as interrupted, after a restart
            for g in groups:
                def on_result(cond, r, d, total, g=g):
                    nonlocal done, tries_done
                    cid = self._case_of(run, cond)
                    done += 1
                    tries_done += r.get("n", 0)
                    if cid is None:
                        return
                    with chat.cond:
                        acc.setdefault(cid, []).append({"value": cond["value"], **r})
                        book.record(run["id"], cid, list(acc[cid]), snap["sigs"][cid], snap["bars"][cid], snap["expected"][cid], partial=True)
                        label = f"condition {min(done + 1, run['conditions'])} of {run['conditions']}" if done < run["conditions"] else "finishing"
                        s["stress"] = {"state": "stopping" if cancel.is_set() else "running", "run": run["id"], "label": label}
                        chat.put_card(cards.results(run["card"], run, book, self._overlay(book, run, snap, done, order=order), "", None, None, "running",
                                                    self._progress(run, done, tries_done, f"{cond['id']} {cond['value'] if cond['value'] is not None else ''}".strip(), cancel.is_set())))
                    chat.emit("job", job={"id": run["id"], "kind": "tests", "state": "running", "label": f"Run {words.plural(len(run['case_ids']), 'test')}", "done": done, "total": run["conditions"]})
                    self._dock(chat)
                res = run_sweep(spec, g["cat"], ctx, g["asked"], g["episodes"], None, cancel, on_result=on_result, per_seed=True)
                results.append(res)
                if res["partial"]:
                    state = "stopped"
                    break
            if cancel.is_set():
                state = "stopped"
            for res, g in zip(results, groups):
                try:
                    name = write_report(res, s.get("goal_id") or "plan", s.get("summary") or "Your job", {"name": b.get("name", ""), "parts": part_names(b)}, root=self.reports_root)
                    report_urls.append(f"/reports/{name}/report.html")
                except OSError:
                    pass
        except Exception as e:
            state = "error"
            err = _err(e)
        finally:
            self.resources.sweep.release(ticket)
            chat.tickets.pop(ticket.id, None)
        try:
            self._finish_tests(chat, run, snap, results, acc, state, report_urls, locals().get("err"), done, tries_done)
        finally:
            chat.cancel.pop(run["id"], None)
            chat.job_done()

    def _finish_tests(self, chat: Chat, run: dict, snap: dict, results: list, acc: dict, state: str, reports: list[str], err: str | None, done: int, tries_done: int) -> None:
        s, book = chat.s, chat.book
        with chat.cond:
            for cid, levels in acc.items():
                book.record(run["id"], cid, levels, snap["sigs"][cid], snap["bars"][cid], snap["expected"][cid], partial=state == "stopped" and len(levels) < snap["expected"][cid])
            weakest = self._weakest(chat, results, snap)
            matrix = C.results_matrix(book, None, ids=run["case_ids"])
            met, below, not_run, total = matrix["met"], matrix["below"], matrix["not_run"], matrix["total"]
            headline = words.results_headline(met, total, below, not_run, state == "stopped")
            run.update(state=state, finished=time.time(), seconds=round(time.time() - run["started"], 1))
            wline = words.weakest_line(weakest["title"], weakest["why"]) if weakest else None
            n_nt = len(book.not_tested_items())
            message = words.results_message(headline, wline, matrix["bar_text"], n_nt) if state != "error" else words.results_error(err or "")
            s["results"].append({"id": run["id"], "card": run["card"], "state": state, "case_ids": run["case_ids"], "headline": headline, "weakest": weakest, "met": met, "total": total,
                                 "below": below, "not_run": not_run, "started": run["started"], "seconds": run["seconds"], "report": reports[0] if reports else None})
            s["last_weakest"] = weakest
            s["stress"] = {"state": state, "run": run["id"], "label": ""}
            report = {"url": reports[0], "urls": reports} if reports else None
            progress = self._progress(run, done, tries_done, "finished" if state == "done" else state)
            chat.put_card(cards.results(run["card"], run, book, None, headline if state != "error" else words.results_error(err or ""), weakest, report, state if state != "error" else "error", progress))
        chat.emit("job", job={"id": run["id"], "kind": "tests", "state": state, "label": f"Run {words.plural(len(run['case_ids']), 'test')}", "done": done, "total": run["conditions"]})
        chat.message("assistant", message)
        self._after(chat)

    def _weakest(self, chat: Chat, results: list[dict], snap: dict) -> dict | None:
        """run_sweep's weakest point, found again as a test and the first level below the bar (for 'watch the weakest' and 'why did it fail')."""
        for res in results:
            w = res.get("weakest")
            if not w:
                continue
            cid = next((c for c in snap["sigs"] if c in (f"tc_{w['id']}", f"tc_f_{w['id']}", w["id"])), None)
            if cid is None:
                continue
            case = chat.book.get(cid)
            level = None
            if case and case["kind"] == "sweep":
                bar = case["pass"]["value"]
                run = case.get("last_run") or {}
                below = [lv["value"] for lv in run.get("per_level", []) if lv.get("n") and lv["success"] < bar]
                level = below[0] if below else None
            return {"case_id": cid, "level": level, "title": (case["title"] if case else w["title"]), "why": w["why"], "fix": w.get("fix", "")}
        return None

    def _stop(self, chat: Chat, job: str) -> None:
        job = {"ai": "plan", "sweep": "tests", "watch": "live", "sim": "live"}.get(job, job)
        if job in ("plan", "all"):
            ev = chat.cancel.get("plan")
            if ev is not None:
                ev.set()
            for t in list(chat.tickets.values()):
                if t.lane == "plan":
                    waiting = t.state == "queued"
                    self.resources.plan.cancel(t.id)
                    if waiting:                                      # a plan still in the line never starts: say it was stopped
                        chat.tickets.pop(t.id, None)
                        if (chat.s.get("ai") or {}).get("state") == "queued":
                            chat.s["ai"]["state"] = "stopped"
                            chat.put_card(cards.improve(chat.s["ai"]))
                            chat.message("assistant", words.ai_stopped())
        if job in ("tests", "all"):
            for key, ev in list(chat.cancel.items()):
                if key.startswith("r"):
                    ev.set()
            for t in list(chat.tickets.values()):
                if t.lane == "sweep":
                    self.resources.sweep.cancel(t.id)
            st = chat.s.get("stress") or {}
            if st.get("state") == "running":
                st["state"] = "stopping"
                chat.message("assistant", words.tests_stopping())
                for rc in [c for c in chat.view.cards.values() if c["type"] == "results" and c["state"] == "running"]:
                    chat.card_patch(rc["id"], {"progress": {"stopping": True}})
        if job in ("live", "all"):
            runs = chat.s.get("runs", [])
            if runs and runs[-1]["state"] in ("running", "starting"):
                rid = runs[-1]["id"]
                try:
                    self._get_runner().stop()
                except Exception:
                    pass
                self._finish_run(chat, rid, "stopped", note="Stopped.")
        self._dock(chat)

    def _cancel_queued(self, chat: Chat, ticket_id: str | None) -> None:
        """The x on an 'Up next' chip: the waiting job is withdrawn, and whatever card was waiting for it says so."""
        ticket = chat.tickets.get(str(ticket_id))
        was_waiting = ticket is not None and ticket.state == "queued"
        for lane in (self.resources.plan, self.resources.sweep):
            if lane.cancel(str(ticket_id)):
                chat.tickets.pop(str(ticket_id), None)
                break
        for cid, card in list(chat.view.cards.items()):
            if card["type"] == "blocked" and (card.get("ticket") or {}).get("id") == ticket_id:
                chat.card_patch(cid, {"state": "blocked", "ticket": None, "title": words.TITLE["blocked"]})
        if not was_waiting:
            return
        if ticket.lane == "sweep" and getattr(ticket, "card", None) in chat.view.cards:
            run = chat.view.cards[ticket.card]
            chat.card_patch(ticket.card, {"state": "cancelled", "summary": words.queued_cancelled(), "actions": []})
            chat.emit("job", job={"id": run["run_id"], "kind": "tests", "state": "cancelled", "label": ticket.label, "done": 0, "total": run["progress"]["total"]})
            if (chat.s.get("stress") or {}).get("run") == run["run_id"]:
                chat.s["stress"] = {"state": "cancelled", "run": run["run_id"], "label": ""}
            chat.cancel.pop(run["run_id"], None)
        elif ticket.lane == "plan" and (chat.s.get("ai") or {}).get("state") == "queued":
            chat.s["ai"]["state"] = "stopped"
            chat.put_card(cards.improve(chat.s["ai"]))
            chat.message("assistant", words.ai_stopped())

    # ================================================================ the AI planner
    def _start_ai(self, chat: Chat, text: str, with_draft: bool, gen: int) -> None:
        ai = chat.s["ai"] = {"state": "queued", "seconds": 0, "phase": "Starting", "chars": 0, "tail": "", "with_draft": with_draft, "gen": gen}
        cancel = threading.Event()
        chat.cancel["plan"] = cancel

        def start(ticket):
            ai["state"] = "running"
            chat.put_card(cards.improve(ai))
            chat.job_started()
            threading.Thread(target=self._ai_thread, args=(chat, ticket, text, cancel, gen), daemon=True, name="chat-ai").start()

        snap = self.budget.snapshot()
        ticket = self.resources.plan.request(chat.id, "AI plan", start, on_cancel=lambda t: cancel.set())
        chat.tickets[ticket.id] = ticket
        if ticket.state == "queued":
            pos = self.resources.plan.position(ticket) or 1
            chat.put_card(cards.improve(ai))
            chat.message("assistant", words.ai_queued(pos))
        else:
            chat.message("assistant", words.ai_started(snap["left"]))
        self._dock(chat)

    def _ai_thread(self, chat: Chat, ticket, text: str, cancel: threading.Event, gen: int) -> None:
        ai = chat.s["ai"]
        t0, stop_tick = time.time(), threading.Event()
        info = {"phase": "Starting", "chars": 0, "tail": ""}

        def emit(kind, data):
            if kind == "phase":
                info["phase"] = data
            elif kind == "text":
                info["chars"], info["tail"] = len(data), data[-700:]

        def ticker():
            while not stop_tick.wait(1.0):
                ai.update(seconds=round(time.time() - t0), phase=info["phase"], chars=info["chars"], tail=info["tail"])
                chat.put_card(cards.improve(ai))
                self._dock(chat)

        threading.Thread(target=ticker, daemon=True, name="chat-ai-tick").start()
        res, failure, stopped = None, None, False
        try:
            gr = _gr()
            res = run_plan(text, None, self._owned(chat), client=gr.make_client(), cancel=cancel, emit=emit, final=True)
        except LLMError as e:
            if e.kind == "cancelled":
                stopped = True
            else:
                failure = llm_settings.scrub(str(e))
        except Exception as e:
            failure = _err(e)
        finally:
            stop_tick.set()
            self.resources.plan.release(ticket)
            chat.tickets.pop(ticket.id, None)
        ai.update(seconds=round(time.time() - t0), phase=info["phase"], chars=info["chars"], tail=info["tail"])
        try:
            self._ai_done(chat, res, failure, stopped, gen)
        except Exception as e:
            ai["state"] = "failed"
            ai["reason"] = _err(e)
            chat.put_card(cards.improve(ai))
            chat.emit("error", code="ai_failed", message=words.ai_failed(_err(e)), card="improve")
        finally:
            self._budget_event(chat)
            self._after(chat)
            chat.job_done()

    def _ai_done(self, chat: Chat, res: dict | None, failure: str | None, stopped: bool, gen: int) -> None:
        ai = chat.s["ai"]
        with_draft = chat.book is not None and ai.get("with_draft")
        if stopped:
            ai["state"] = "stopped"
            chat.put_card(cards.improve(ai))
            return chat.message("assistant", words.ai_stopped()) and None
        if failure:
            ai.update(state="failed", reason=failure)
            chat.put_card(cards.improve(ai))
            return chat.message("assistant", words.ai_failed(failure)) and None
        ai["calls"] = (res or {}).get("calls")
        if res["state"] == "unsupported":
            ai.update(state="unsupported", reason=res["note"])
            chat.put_card(cards.improve(ai))
            return self._say_blocked(chat, words.ai_unsupported(res["note"]), chips=self._suggest(chat))
        if res.get("source") != "ai" or not res.get("raw_plan"):                 # run_plan fell back to the offline planner: no key, quota, network, an unusable answer
            reason = res.get("reason") or "no answer"
            if reason.startswith("daily_limit"):
                self.budget.mark_exhausted()
            closest = None
            if res.get("proposals") and res.get("goal"):
                closest = res["goal"].get("title")
            ai.update(state="unavailable", reason=llm_settings.scrub(reason)[:200], closest=closest)
            chat.put_card(cards.improve(ai))
            msg = words.ai_quota_gone() if reason.startswith("daily_limit") else (words.ai_unavailable(ai["reason"], closest) if chat.book is not None else words.ai_unavailable_no_draft(ai["reason"]))
            return chat.message("assistant", msg) and None
        raw = copy.deepcopy(res["raw_plan"])
        raw["questions"] = []                                                  # in the chat the AI never stops to ask: questions become none
        pid = uuid.uuid4().hex[:10]
        self._remember_plan(pid, raw)
        ai.update(model=res.get("model"), plan_id=pid, raw_plan=raw, result_meta={"scanned": res.get("scanned"), "meets_goal": res.get("meets_goal"), "note": res.get("note")})
        proposals = [self._proposal(i, e) for i, e in enumerate(res["proposals"])]
        pending = {"raw_plan": raw, "proposals": proposals, "scanned": res.get("scanned"), "meets_goal": res.get("meets_goal"), "note": res.get("note"), "model": res.get("model")}
        if not with_draft:
            ai["state"] = "applied"
            return self._adopt_ai(chat, pending, keep_edits=False, first=True, gen=gen)
        with chat.cond:
            cur_book = chat.book
            ai_book = C.CaseBook(raw["understanding"]["platform"], raw, "ai") if raw["understanding"]["platform"] == cur_book.platform else C.CaseBook(raw["understanding"]["platform"], raw, "ai")
            cur_snap = chatdiff.snapshot(cur_book, chat.s.get("proposals") or [], chat.s.get("pick", 0), chat.s.get("summary", ""), [a["text"] for a in cards.understanding(chat.s, cur_book)["assumptions"]])
            ai_snap = chatdiff.snapshot(ai_book, proposals, 0, raw["understanding"]["summary"], list(raw["understanding"].get("assumptions", [])))
            d = chatdiff.compare(cur_snap, ai_snap)
            d["edits"] = [e["label"] for e in cur_book.history[:cur_book.cursor]]
            d["ai"] = {"source": "ai", "model": res.get("model"), "platform": raw["understanding"]["platform"], "summary": raw["understanding"]["summary"]}
            d["base"] = {"source": chat.s.get("source", "draft"), "platform": cur_book.platform}
            ai["pending"] = pending
            ai["diff"] = d
        if d["count"] == 0:
            ai["state"] = "kept"
            ai["pending"] = None
            chat.put_card(cards.improve(ai))
            return chat.message("assistant", words.ai_done_same()) and None
        ai["state"] = "done"
        chat.put_card(cards.improve(ai))
        chat.message("assistant", words.ai_done_changes(d["count"]))

    def _remember_plan(self, pid: str, raw: dict) -> None:
        plans = _gr().PLANS
        plans[pid] = raw
        for old in list(plans)[:-20]:
            plans.pop(old, None)

    def _apply_ai(self, chat: Chat, kind: str) -> None:
        ai = chat.s.get("ai") or {}
        pending = ai.get("pending")
        if ai.get("state") != "done" or not pending:
            return chat.message("assistant", words.blocked_no_plan()) and None
        if kind == "keep_mine":
            ai.update(state="kept", pending=None)
            chat.put_card(cards.improve(ai))
            return chat.message("assistant", words.ai_adopted("draft")) and None
        self._adopt_ai(chat, pending, keep_edits=(kind == "use_ai_keep_edits"), first=False, gen=chat.gen)

    def _adopt_ai(self, chat: Chat, pending: dict, keep_edits: bool, first: bool, gen: int) -> None:
        """Make the AI's plan the base. The person's edits are replayed on top of it when asked; the ones that no longer fit are listed."""
        raw = pending["raw_plan"]
        platform = raw["understanding"]["platform"]
        ai = chat.s["ai"]
        failed: list[dict] = []
        n_edits = 0
        with chat.cond:
            if chat.gen != gen and first:
                return
            if first or chat.book is None or chat.book.platform != platform:
                n_edits = len(chat.book.flat_ops()) if (chat.book is not None and not first) else 0
                chat.book = C.CaseBook(platform, raw, "ai")
                failed = [{"op": o} for o in []] if keep_edits is False else []
            else:
                n_edits = len(chat.book.flat_ops())
                failed = chat.book.rebase(raw, "ai", keep_edits=keep_edits)
            s = chat.s
            s.update(platform=platform, source="ai", summary=raw["understanding"]["summary"] or s.get("summary", ""), goal_id=s.get("goal_id") if not first else None,
                     design_task=raw["task"]["preset"], text=s.get("text"))
            if first:
                s.update(stated={}, runs=[], results=[], last_weakest=None, calm_started=False)
            info = {"scanned": pending.get("scanned"), "meets_goal": pending.get("meets_goal"), "note": pending.get("note"), "seconds": ai.get("seconds")}
            s["proposals"], s["pick"], s["design"] = pending["proposals"], 0, info
            if pending["proposals"]:
                chat.book.set_machine(pending["proposals"][0]["machine_sig"])
            self._evaluate_proposals(chat)
            ai.update(state="applied", pending=None, diff=ai.get("diff") if not first else None)
        if first:
            self._begin_pipeline(chat)
            self._step(chat, "understand", "done", f"{ai.get('seconds', 0)} s")
            self._step(chat, "design", "done", "from the AI plan")
            with chat.cond:
                chat.put_card(cards.understanding(chat.s, chat.book, self._requirements(chat)))
                chat.put_card(cards.design(chat.s))
                chat.put_card(cards.improve(ai))
            self._step(chat, "check", "done", "")
            self._tail(chat, gen, check_first=False, auto_watch=True)
            return
        with chat.cond:
            chat.put_card(cards.understanding(chat.s, chat.book, self._requirements(chat)))
            chat.put_card(cards.design(chat.s))
            chat.put_card(cards.tests(chat.s, chat.book))
            if chat.s["proposals"]:
                chat.put_card(self._parts_card(chat))
            chat.put_card(cards.improve(ai))
            self._mark_results_stale(chat)
        self._preview(chat, chat.gen)
        chat.message("assistant", words.ai_adopted_keep_edits(max(0, n_edits - len(failed)), len(failed)) if keep_edits else words.ai_adopted("ai"))
        if failed:
            chat.message("assistant", words.edits_failed(len(failed), "the AI plan"), kind="note")

    # ================================================================ read side
    def state(self, chat: Chat) -> dict:
        """A snapshot for the Details sheet: the cases, the plan, the proposals, the results, the build sheet, the limits."""
        with chat.cond:
            s, book = chat.s, chat.book
            snapshot = chat.view.snapshot()
            out = {"id": chat.id, "title": chat.title, "seq": chat.seq, "created": chat.created, "updated": chat.updated, "phase": "ready" if book is not None else "empty",
                   "ai": {k: v for k, v in (s.get("ai") or {}).items() if k not in ("raw_plan", "pending")}, "budget": self.budget.snapshot(), "resources": self.resources.snapshot(),
                   "limits": snapshot["limits"], "view": {"messages": snapshot["messages"], "cards": snapshot["cards"], "chips": snapshot["chips"], "dock": snapshot["dock"]}}
            if book is None:
                return {**out, "cases": [], "plan": None, "proposals": [], "results": [], "sheet": None}
            props = s.get("proposals") or []
            sheet = self._sheet_json(chat) if props else None
            return {**out, "platform": s["platform"], "job": {"text": s.get("text"), "summary": s.get("summary"), "goal": s.get("goal_id"), "source": s.get("source"), "kit": s.get("kit")},
                    "plan": {"understanding": book.base["understanding"], "params": book.params, "touched_params": sorted(book.touched_params), "task": book.task,
                             "stress": book.stress_plan(), "source": book.source, "issues": book.base.get("issues", [])},
                    "cases": book.view(), "summary": book.summary(), "matrix": C.results_matrix(book), "not_tested": book.not_tested_items(), "statement": STATEMENT,
                    "limit_line": words.limit_line(len(book.not_tested_items())), "proposals": [cards.proposal_card_view(p) for p in props], "pick": s.get("pick", 0),
                    "design": {k: v for k, v in (s.get("design") or {}).items()}, "results": s.get("results", []), "runs": s.get("runs", []), "sheet": sheet,
                    "history": [{"label": e["label"], "ops": e["ops"]} for e in book.history], "cursor": book.cursor, "can_undo": book.can_undo(), "can_redo": book.can_redo(),
                    "replay_failures": book.replay_failures}
