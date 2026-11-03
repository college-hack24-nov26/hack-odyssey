"""One chat: an append-only event log, the cards and chips it adds up to, and the long-poll that streams it to the page.

Everything the page shows is an event with a `seq` (1, 2, 3 ...), a `ts` and a `type`. `View` is the reducer: replaying the events of a chat in order rebuilds exactly
the cards, messages, chips and dock the page had, which is how a reload (or a second tab) restores a chat, and how the store test checks that replay equals live.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque

CARD_TYPES = ("understanding", "design", "watch", "tests", "results", "parts", "improve", "limits", "answer", "blocked")
EVENT_TYPES = ("message", "card.create", "card.patch", "chips", "dock", "job", "budget", "limits", "error")
MAX_WAIT = 25.0
PAGE = 500


def merge_patch(target, patch):
    """RFC 7386 JSON merge patch: objects merge key by key, null deletes a key, anything else (lists included) replaces."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = dict(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def make_patch(old, new):
    """The merge patch that turns `old` into `new` (None when they are equal). Dicts are diffed key by key, everything else is replaced."""
    if old == new:
        return None
    if isinstance(old, dict) and isinstance(new, dict):
        patch = {}
        for k in set(old) | set(new):
            if k not in new:
                patch[k] = None
            elif k not in old:
                patch[k] = copy.deepcopy(new[k])
            else:
                sub = make_patch(old[k], new[k])
                if sub is not None:
                    patch[k] = sub
        return patch or None
    return copy.deepcopy(new)


class View:
    """What the events add up to. `apply` is the only way it changes."""

    def __init__(self):
        self.messages: list[dict] = []
        self.cards: dict[str, dict] = {}
        self.order: list[str] = []
        self.chips: list[dict] = []
        self.dock: dict = {}
        self.jobs: dict[str, dict] = {}
        self.budget: dict = {}
        self.limits: dict = {}

    def apply(self, ev: dict) -> None:
        t = ev["type"]
        if t == "message":
            self.messages.append({k: v for k, v in ev.items() if k != "type"})
        elif t == "card.create":
            card = copy.deepcopy(ev["card"])
            if card["id"] not in self.cards:
                self.order.append(card["id"])
            self.cards[card["id"]] = card
        elif t == "card.patch":
            if ev["id"] in self.cards:
                self.cards[ev["id"]] = merge_patch(self.cards[ev["id"]], ev["patch"])
        elif t == "chips":
            self.chips = copy.deepcopy(ev["chips"])
        elif t == "dock":
            self.dock = {k: v for k, v in ev.items() if k not in ("type", "seq", "ts")}
        elif t == "job":
            self.jobs[ev["job"]["id"]] = copy.deepcopy(ev["job"])
        elif t == "budget":
            self.budget = copy.deepcopy(ev["ai"])
        elif t == "limits":
            self.limits = {k: v for k, v in ev.items() if k not in ("type", "seq", "ts")}

    def snapshot(self) -> dict:
        return {"messages": copy.deepcopy(self.messages), "cards": [copy.deepcopy(self.cards[i]) for i in self.order], "chips": copy.deepcopy(self.chips),
                "dock": copy.deepcopy(self.dock), "jobs": copy.deepcopy(self.jobs), "budget": copy.deepcopy(self.budget), "limits": copy.deepcopy(self.limits)}


def replay(events: list[dict]) -> View:
    v = View()
    for ev in events:
        v.apply(ev)
    return v


class Chat:
    """The log and the state of one conversation. The engine (orchestrator.py) fills `s`, `book` and the runtime fields; this class only knows events."""

    def __init__(self, cid: str, store=None, title: str = "New chat"):
        self.id = cid
        self.title = title
        self.created = self.updated = time.time()
        self.store = store
        self.cond = threading.Condition(threading.RLock())
        self.events: list[dict] = []
        self.seq = 0
        self.view = View()
        self.s: dict = {"counters": {}}          # engine state that survives a restart (JSON only)
        self.book = None                         # cases.CaseBook once a job exists
        # runtime only: nothing below is saved
        self.turns: deque = deque()
        self.worker: threading.Thread | None = None
        self.busy = 0
        self.gen = 0                             # bumped whenever the draft pipeline must be abandoned
        self.cancel: dict[str, threading.Event] = {}
        self.tickets: dict[str, object] = {}
        self.t0 = time.time()

    # ------------------------------------------------------------ ids
    def next_id(self, prefix: str) -> str:
        with self.cond:
            c = self.s.setdefault("counters", {})
            c[prefix] = c.get(prefix, 0) + 1
            return f"{prefix}{c[prefix]}"

    # ------------------------------------------------------------ events
    def emit(self, type_: str, **payload) -> dict:
        assert type_ in EVENT_TYPES, type_
        with self.cond:
            self.seq += 1
            ev = {"seq": self.seq, "ts": round(time.time(), 3), "type": type_, **payload}
            self.events.append(ev)
            self.view.apply(ev)
            self.updated = ev["ts"]
            if self.store is not None:
                self.store.append_event(self.id, ev)
            self.cond.notify_all()
        return ev

    def message(self, role: str, text: str, kind: str = "say", **extra) -> dict:
        return self.emit("message", id=self.next_id("m"), role=role, kind=kind, text=text, **{k: v for k, v in extra.items() if v is not None})

    def card_create(self, card: dict) -> dict:
        assert card["type"] in CARD_TYPES, card["type"]
        return self.emit("card.create", card=card)

    def card_patch(self, cid: str, patch: dict) -> dict | None:
        if not patch:
            return None
        return self.emit("card.patch", id=cid, patch=patch)

    def put_card(self, card: dict) -> None:
        """Create the card, or patch only what changed in it."""
        with self.cond:
            old = self.view.cards.get(card["id"])
            if old is None:
                self.card_create(card)
            else:
                self.card_patch(card["id"], make_patch(old, card))

    def events_after(self, after: int = 0, wait: float = 0.0, limit: int = PAGE) -> dict:
        """Events with seq > after, as soon as there are any (or an empty list after `wait` seconds, at most MAX_WAIT).
        {'events': [...], 'last': seq of the last one returned (or `after`), 'more': True if the page was cut, 'reset': True if `after` is ahead of this chat}."""
        wait = max(0.0, min(float(wait), MAX_WAIT))
        deadline = time.monotonic() + wait
        with self.cond:
            if after > self.seq:
                return {"events": [], "last": self.seq, "more": False, "reset": True}
            while self.seq <= after:
                left = deadline - time.monotonic()
                if left <= 0:
                    return {"events": [], "last": after, "more": False}
                self.cond.wait(left)
            if after >= self.events[0]["seq"] - 1:
                start = after - (self.events[0]["seq"] - 1)
            else:
                start = 0
            chunk = self.events[start:start + limit]
            return {"events": copy.deepcopy(chunk), "last": chunk[-1]["seq"], "more": start + limit < len(self.events)}

    # ------------------------------------------------------------ background work bookkeeping
    def job_started(self) -> None:
        with self.cond:
            self.busy += 1

    def job_done(self) -> None:
        with self.cond:
            self.busy = max(0, self.busy - 1)
            self.cond.notify_all()

    def idle(self) -> bool:
        with self.cond:
            return not self.turns and self.worker is None and self.busy == 0

    def wait_idle(self, timeout: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout
        with self.cond:
            while not self.idle():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.cond.wait(min(left, 0.25))
        return True

    # ------------------------------------------------------------ save
    def session(self) -> dict:
        with self.cond:
            return {"version": 1, "id": self.id, "title": self.title, "created": self.created, "updated": self.updated, "seq": self.seq, "s": copy.deepcopy(self.s),
                    "book": self.book.to_dict() if self.book is not None else None}

    def save(self) -> None:
        if self.store is not None:
            self.store.save_session(self.id, self.session())
