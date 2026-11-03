"""Shared rig for the chat tests: an Engine on a temp folder with a fake simulation runner, a fake OpenRouter and (optionally) a fast stand-in for the stress test.
Nothing here starts the dashboard app or Webots."""
from __future__ import annotations

import copy
import importlib
import json
import threading
import time
from pathlib import Path

from simlab.chat import cases as C
from simlab.chat.orchestrator import Engine
from simlab.chat.store import Store
from simlab.llm import OpenRouter
from simlab.parts.templates import example_builds
from tests.fake_openrouter import FakeOpenRouter

gr = importlib.import_module("simlab.dashboard.goal_router")
sweep_mod = importlib.import_module("simlab.hazards.sweep")
KEY = "sk-or-v1-" + "q1w2e3r4" * 6
ROVER = example_builds()["esp32_pipe_rover"]

BOAT = "A boat that finds a person in a flooded street 150 m away in 1 m waves"
PIPE = "A rover that inspects a 30 cm sewer pipe"
DRONE = "A drone that looks over a field for 10 minutes"


class FakeRunner:
    """Stands in for the Lab's LabRunner: start() makes it 'run' and after `finish_after` seconds it reports a finished episode."""

    def __init__(self, finish_after: float = 0.3):
        self.running = False
        self.starts: list[dict] = []
        self.stops = 0
        self.finish_after = finish_after
        self._t0 = 0.0
        self.n = 0

    def start(self, vehicle, task, **kw):
        self.running = True
        self.n += 1
        self._t0 = time.time()
        self.starts.append({"vehicle": vehicle.name, "task": task.name, **kw})

    def stop(self):
        self.running = False
        self.stops += 1

    def state(self):
        if not self.running:
            return {"running": False, "status": "idle"}
        if time.time() - self._t0 > self.finish_after:
            return {"running": True, "status": "finished", "scene_id": f"s{self.n}", "t": 41.2, "battery": 0.8, "result": {"success": True, "t": 41.2, "crash": None, "timeout": False}}
        return {"running": True, "status": "running", "scene_id": f"s{self.n}", "t": 2.0}


def isolate(tmp_path: Path, monkeypatch) -> None:
    """Every file the chat or the planner touches goes to the temp folder; no key in the environment."""
    for var, sub in (("SIMLAB_LLM", "llm.json"), ("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inv.yaml"), ("SIMLAB_GOALS", "goals"), ("SIMLAB_BUILDS", "builds"),
                     ("SIMLAB_CHAT_CONFIG", "chat.json"), ("SIMLAB_CHATS", "chats")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def save_key(tmp_path: Path) -> None:
    (tmp_path / "llm.json").write_text(json.dumps({"api_key": KEY}), encoding="utf-8")


def make_engine(tmp_path: Path, monkeypatch, *, runner=None, mujoco: bool = True, fake: FakeOpenRouter | None = None, key: bool = False, poll: float = 0.05) -> Engine:
    isolate(tmp_path, monkeypatch)
    if key:
        save_key(tmp_path)
    if fake is not None:
        monkeypatch.setattr(gr, "make_client", lambda: OpenRouter(base_url=fake.url, sleep=lambda s: None))
    eng = Engine(store=Store(tmp_path / "chats"), runner=lambda: runner or FakeRunner(), reports_root=tmp_path / "reports", mujoco=mujoco, poll=poll)
    return eng


def settle(chat, timeout: float = 15.0) -> None:
    """Wait until no watched run is still going (the fake runner finishes one a moment after it starts)."""
    t0 = time.time()
    while time.time() - t0 < timeout and any(r["state"] in ("running", "starting") for r in chat.s.get("runs", [])):
        time.sleep(0.02)


def say(eng: Engine, chat, text: str | None = None, action: dict | None = None, wait: float = 180.0, label: str | None = None, hold_runs: bool = False) -> list[dict]:
    """Send one turn, wait until the chat is idle (and, unless `hold_runs`, until its watched run has finished), return the events it produced."""
    n = chat.seq
    eng.send(chat, text=text, action=action, label=label)
    assert chat.wait_idle(wait), f"the chat did not finish {text or action}"
    if not hold_runs:
        settle(chat)
        assert chat.wait_idle(wait)
    return chat.events[n:]


def texts(events: list[dict], role: str = "assistant") -> list[str]:
    return [e["text"] for e in events if e["type"] == "message" and e["role"] == role]


def card(chat, cid: str) -> dict:
    return chat.view.cards[cid]


def boat_plan(**over) -> dict:
    """A water plan as the AI would send it (a boat with a thruster), valid for validate_plan."""
    p = {"understanding": {"summary": "Flood rescue boat", "platform": "water", "why": "a boat", "assumptions": ["calm current"]}, "params": {"range_m": 120, "wave_hs": 0.6},
         "questions": [], "scenarios": [{"name": "Rain", "why": "visibility", "hazard": None}], "requirements": [],
         "task": {"preset": "water_rescue", "params": {"range_m": 120}},
         "stress": {"hazards": [{"id": "waves", "levels": [0.0, 0.3, 0.6], "ask": 0.6}], "faults": ["thruster_out"]}, "designs": []}
    p.update(over)
    return p


def rover_plan(**over) -> dict:
    """A ground plan as the AI would send it, with one design (the example ESP32 pipe rover)."""
    p = {"understanding": {"summary": "Field rover", "platform": "ground", "why": "a rover", "assumptions": []}, "params": {"distance_m": 20},
         "questions": [{"id": "q1", "text": "How wet?", "kind": "choice", "choices": ["dry", "wet"]}], "scenarios": [{"name": "Mud", "why": "stuck", "hazard": "resistance"},
                                                                                                           {"name": "Hail", "why": "damage", "hazard": None}],
         "requirements": [], "task": {"preset": "goto_open", "params": {}},
         "stress": {"hazards": [{"id": "grip", "levels": [0.8, 0.4, 0.05], "ask": 0.2}], "faults": ["lost_commands"]},
         "designs": [{"label": "Pipe rover", "why": "known to work", "build": copy.deepcopy(ROVER)}]}
    p.update(over)
    return p


def script_plan(fake: FakeOpenRouter, plan: dict, delay: float = 0.0) -> None:
    text = json.dumps(plan)
    fake.script.append({"chunks": [text[i:i + 60] for i in range(0, len(text), 60)], "delay": delay})


class SweepFake:
    """Replaces the per-condition simulation so a whole stress test takes a moment. A level at or above `break_at` fails; `gate` (an Event) holds every condition
    until it is set, so a test can look at a run that is in progress."""

    def __init__(self, monkeypatch, break_at: float = 0.9, gate: threading.Event | None = None, delay: float = 0.0):
        self.calls: list[tuple] = []
        self.gate, self.delay, self.break_at = gate, delay, break_at
        self.started = threading.Event()
        monkeypatch.setattr(sweep_mod, "_run_condition", self._run)

    def _run(self, spec, cat, cond, episodes, sims, per_seed=False):
        self.started.set()
        if self.gate is not None:
            self.gate.wait(30)
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((cond["kind"], cond["id"], cond["value"], episodes))
        bad = cond["kind"] == "hazard" and cond["value"] is not None and cond["value"] >= self.break_at and cond["id"] == "waves"
        wins = 0 if bad else episodes
        out = {"success": wins / episodes, "ci": [0.0, 1.0], "n": episodes, "crashes": {"capsized": episodes} if bad else {}, "timeouts": 0, "mean_time": None if bad else 12.5}
        if per_seed:
            out["per_seed"] = [{"seed": s, "success": not bad, "t": 12.5, "crash": "capsized" if bad else None, "timeout": False} for s in range(episodes)]
        return out
