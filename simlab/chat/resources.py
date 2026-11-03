"""Who may use the three shared things, and what happens when they are busy.

The AI planner, the stress test and the live 3D simulation are each single-flight for the whole server (the dashboard has one simulation runner, one plan job and one
sweep job), and the person may have several chats and tabs open. A request for a busy plan or sweep lane is not an error: it waits in a queue, shown as an 'Up next'
chip with its position and an x, and starts by itself when the lane frees. The live view is different: a watch the person asked for replaces the one that is running
(the old run says so), while the calm run the chat starts by itself never replaces anything.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable


class Ticket:
    def __init__(self, lane: str, owner: str, label: str, start: Callable, on_cancel: Callable | None, on_preempt: Callable | None):
        self.id = uuid.uuid4().hex[:8]
        self.lane, self.owner, self.label = lane, owner, label
        self.start, self.on_cancel, self.on_preempt = start, on_cancel, on_preempt
        self.state = "queued"                     # queued | running | done | cancelled
        self.card: str | None = None              # the card waiting for this ticket (the engine sets it), so withdrawing the ticket can say so there
        self.created = time.time()

    def public(self, position: int | None = None) -> dict:
        return {"id": self.id, "lane": self.lane, "owner": self.owner, "label": self.label, "state": self.state, "position": position}


class Lane:
    def __init__(self, name: str, queueing: bool = True):
        self.name, self.queueing = name, queueing
        self.holder: Ticket | None = None
        self.queue: list[Ticket] = []
        self._lock = threading.RLock()

    def request(self, owner: str, label: str, start: Callable[[Ticket], None], on_cancel: Callable | None = None, *, auto: bool = False, preempt: bool = False,
                on_preempt: Callable | None = None, external_busy: bool = False) -> Ticket | None:
        """Ask for the lane. `start(ticket)` is called once, when the ticket gets it (at once if the lane is free; from the thread that releases it if it had to wait).
        Returns the ticket, or None for an automatic request that must not wait or replace anything (`auto`) when the lane is busy.
        `preempt` replaces the holder (its `on_preempt` runs); `external_busy` says something outside this registry holds the lane (the Lab tab's run)."""
        t = Ticket(self.name, owner, label, start, on_cancel, on_preempt)
        with self._lock:
            busy = self.holder is not None or external_busy
            if busy and auto:
                return None
            if busy and preempt:
                old = self.holder
                self.holder = None
                if old is not None:
                    old.state = "cancelled"
                    if old.on_preempt:
                        old.on_preempt(old)
            elif busy:
                if not self.queueing:
                    return None
                self.queue.append(t)
                return t
            self.holder, t.state = t, "running"
        start(t)
        return t

    def release(self, t: Ticket | None) -> None:
        """The holder is finished (or a queued ticket is withdrawn). The next in line starts, in its own thread."""
        if t is None:
            return
        nxt = None
        with self._lock:
            if t in self.queue:
                self.queue.remove(t)
                t.state = "cancelled"
                return
            if self.holder is t:
                self.holder = None
                t.state = "done" if t.state == "running" else t.state
                if self.queue:
                    nxt = self.queue.pop(0)
                    self.holder, nxt.state = nxt, "running"
        if nxt is not None:
            threading.Thread(target=nxt.start, args=(nxt,), daemon=True, name=f"lane-{self.name}").start()

    def cancel(self, ticket_id: str) -> bool:
        """The x on an 'Up next' chip (withdraws a queued ticket) or Stop on the running one (calls its on_cancel)."""
        with self._lock:
            t = next((x for x in self.queue if x.id == ticket_id), None)
            if t is None and self.holder is not None and self.holder.id == ticket_id:
                t = self.holder
        if t is None:
            return False
        if t in self.queue:
            self.release(t)
        elif t.on_cancel:
            t.on_cancel(t)
        return True

    def position(self, t: Ticket) -> int | None:
        with self._lock:
            return self.queue.index(t) + 1 if t in self.queue else None

    def snapshot(self) -> dict:
        with self._lock:
            return {"busy": self.holder is not None, "holder": self.holder.public() if self.holder else None,
                    "queue": [t.public(i + 1) for i, t in enumerate(self.queue)]}


class Resources:
    """The three lanes. `runner` is a function returning the live simulation runner (so tests can pass a fake one)."""

    def __init__(self, runner: Callable | None = None):
        self.plan = Lane("plan")
        self.sweep = Lane("sweep")
        self.live = Lane("live", queueing=False)
        self._runner = runner

    @property
    def runner(self):
        return self._runner() if self._runner else None

    def live_external(self) -> bool:
        """The simulation is running but not because of a ticket here (the Lab tab started it)."""
        r = self.runner
        return bool(r is not None and getattr(r, "running", False) and self.live.holder is None)

    def request_live(self, owner: str, label: str, start: Callable, *, auto: bool, on_preempt: Callable | None = None) -> Ticket | None:
        return self.live.request(owner, label, start, auto=auto, preempt=not auto, on_preempt=on_preempt, external_busy=self.live_external())

    def snapshot(self) -> dict:
        live = self.live.snapshot()
        live["external"] = self.live_external()
        live["busy"] = live["busy"] or live["external"]
        return {"plan": self.plan.snapshot(), "sweep": self.sweep.snapshot(), "live": live}
