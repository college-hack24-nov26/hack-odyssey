"""The chat's HTTP API through a TestClient (no lifespan, no dashboard app, no Webots): route order, 202, the long-poll and its resume rules, stop, errors."""
import importlib
import re
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("mujoco")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from simlab.chat.chat import CARD_TYPES, EVENT_TYPES, MAX_WAIT  # noqa: E402
from simlab.chat.store import ID_RE  # noqa: E402
from tests.chat_helpers import BOAT, FakeRunner, KEY, PIPE, SweepFake, boat_plan, make_engine, script_plan  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
chat_router = importlib.import_module("simlab.dashboard.chat_router")        # the module (`from simlab.dashboard import chat_router` is the router object)


def build_app(decoy_first: bool = False) -> FastAPI:
    """The dashboard's order: the Lab, Builder, Goal and chat routers first, then catch-all routes shaped like sewerbot's own."""
    app = FastAPI()

    def decoys():
        @app.get("/api/{kind}/{what}")
        def _any(kind: str, what: str):
            return {"decoy": [kind, what]}

        @app.get("/api/{kind}/state")
        def _state(kind: str):
            return {"decoy": kind}

        @app.get("/api/{kind}/video")
        def _video(kind: str):
            return {"decoy": kind}
    if decoy_first:
        decoys()
    app.include_router(chat_router.router)
    if not decoy_first:
        decoys()
    return app


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    eng = make_engine(tmp_path, monkeypatch)
    chat_router.set_engine(eng)
    with TestClient(build_app()) as c:
        yield c, eng, tmp_path
    chat_router.set_engine(None)


def new(c) -> str:
    r = c.post("/api/chat/new")
    assert r.status_code == 200
    return r.json()["id"]


def wait_idle(eng, cid, t=120):
    assert eng.get(cid).wait_idle(t)


def poll_all(c, cid, after=0, wait=0):
    """Everything after `after`, following `more`."""
    got, last = [], after
    while True:
        r = c.get(f"/api/chat/{cid}/events", params={"after": last, "wait": wait}).json()
        got += r["events"]
        last = r["last"]
        if not r.get("more"):
            return got, last


# ------------------------------------------------------------------ route order
def test_the_chat_routes_are_not_shadowed_by_the_catch_all_routes(rig):
    c, eng, _ = rig
    assert c.get("/chat").status_code == 200 and "text/html" in c.get("/chat").headers["content-type"]
    caps = c.get("/api/chat/capabilities")
    assert caps.status_code == 200 and "platforms" in caps.json() and "decoy" not in caps.json()
    cid = new(c)
    assert "decoy" not in c.get(f"/api/chat/{cid}/state").json()
    assert "events" in c.get(f"/api/chat/{cid}/events").json()
    assert c.get("/api/chat/latest").json()["id"] == cid
    assert c.get("/api/lab/state").json() == {"decoy": ["lab", "state"]}                      # the decoys really are there for everything else


def test_the_test_would_notice_shadowing(tmp_path, monkeypatch):
    eng = make_engine(tmp_path, monkeypatch)
    chat_router.set_engine(eng)
    try:
        with TestClient(build_app(decoy_first=True)) as c:
            assert c.get("/api/chat/capabilities").json() == {"decoy": ["chat", "capabilities"]}
    finally:
        chat_router.set_engine(None)


def test_the_real_app_registers_the_chat_router_before_its_catch_all_routes():
    src = (ROOT / "sewerbot" / "dashboard" / "app.py").read_text(encoding="utf-8")
    inc = src.index("app.include_router(chat_router)")
    for catch_all in ('@app.get("/api/{kind}/state")', '@app.websocket("/ws/{kind}")', '@app.get("/api/{kind}/video")', '@app.post("/api/{kind}/detector")'):
        assert inc < src.index(catch_all), catch_all
    assert src.index("app.include_router(goal_router)") < inc
    init = (ROOT / "simlab" / "dashboard" / "__init__.py").read_text(encoding="utf-8")
    assert "chat_router" in init and "chat_router" in init.split("__all__")[1]


def test_the_old_page_is_still_served_at_the_root_and_the_chat_page_beside_it():
    src = (ROOT / "sewerbot" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert re.search(r'@app\.get\("/"\)\s+def index\(\):\s+return FileResponse\(STATIC / "index\.html"', src)
    assert (ROOT / "sewerbot" / "dashboard" / "static" / "index.html").is_file()
    html = (ROOT / "simlab" / "dashboard" / "static" / "chat.html").read_text(encoding="utf-8")
    for old in ("app.js", "lab.js", "goal.js", "builder.js", "train.js"):
        assert old not in html


# ------------------------------------------------------------------ capabilities, new, latest, state
def test_capabilities_say_what_the_page_may_offer(rig):
    c, eng, _ = rig
    caps = c.get("/api/chat/capabilities").json()
    assert set(caps["platforms"]) == {"ground", "air", "water"} and caps["platforms"]["air"]["observation_only"] is True and caps["card_types"] == list(CARD_TYPES)
    w = caps["platforms"]["water"]
    waves = next(h for h in w["hazards"] if h["id"] == "waves")
    assert waves["range"] == [0.0, 1.5] and waves["live"] is True and waves["unit"] == "m"
    assert {"ai", "mujoco", "live", "plan", "sweep", "hardware", "kits", "examples", "copy", "action_types"} <= set(caps)
    assert caps["ai"]["configured"] is False and caps["ai"]["enabled"] is False and caps["hardware"]["link"] == "/#hw"
    assert caps["kits"][0]["id"] == "my_rover" and {e["id"] for e in caps["examples"]} >= {"flood_boat", "sewer_rover", "field_drone", "own_parts"}
    assert KEY not in str(caps)


def test_a_new_chat_has_a_safe_id_and_an_empty_state(rig):
    c, eng, tmp = rig
    cid = new(c)
    assert ID_RE.match(cid)
    st = c.get(f"/api/chat/{cid}/state").json()
    assert st["phase"] == "empty" and st["cases"] == [] and st["plan"] is None and st["sheet"] is None and st["view"]["chips"]
    assert st["seq"] >= 4 and st["limits"]["count"] is None
    assert (tmp / "chats" / cid / "events.jsonl").is_file() and (tmp / "chats" / cid / "session.json").is_file()


def test_latest_is_the_chat_to_open_and_carries_its_events(rig):
    c, eng, _ = rig
    assert c.get("/api/chat/latest").json() == {"id": None}
    a = new(c)
    time.sleep(0.01)
    b = new(c)
    r = c.get("/api/chat/latest").json()
    assert r["id"] == b and r["seq"] >= 4 and r["events"] and r["last"] == r["events"][-1]["seq"]
    c.post(f"/api/chat/{a}/send", json={"text": "hello"})
    wait_idle(eng, a)
    assert c.get("/api/chat/latest").json()["id"] == a


def test_unknown_and_malformed_ids_are_404_and_bad_parameters_422(rig):
    c, eng, _ = rig
    for bad in ("0" * 12, "nope", "..%2F..", "A" * 12, "x" * 40):
        for path in ("state", "events"):
            assert c.get(f"/api/chat/{bad}/{path}").status_code == 404
        assert c.post(f"/api/chat/{bad}/send", json={"text": "hi"}).status_code == 404
    cid = new(c)
    assert c.get(f"/api/chat/{cid}/events", params={"after": -1}).status_code == 422
    assert c.get(f"/api/chat/{cid}/events", params={"wait": MAX_WAIT + 1}).status_code == 422
    assert c.get(f"/api/chat/{cid}/events", params={"after": "x"}).status_code == 422


# ------------------------------------------------------------------ send
def test_send_answers_202_at_once_and_the_result_is_in_the_events(rig):
    c, eng, _ = rig
    cid = new(c)
    t0 = time.time()
    r = c.post(f"/api/chat/{cid}/send", json={"text": BOAT, "client_msg_id": "c-1"})
    assert r.status_code == 202 and time.time() - t0 < 1.0
    j = r.json()
    assert j["accepted"] is True and j["turn_id"] == "t1" and j["intent"]["kind"] == "new_job" and j["intent"]["source"] == "new_job" and j["message_id"] == "m1" and j["seq"] >= 5
    wait_idle(eng, cid)
    ev, last = poll_all(c, cid)
    types = [e["type"] for e in ev]
    assert set(types) <= set(EVENT_TYPES) and ev[-1]["seq"] == last and "card.create" in types
    created = [e["card"]["id"] for e in ev if e["type"] == "card.create"]
    assert created[:4] == ["understanding", "design", "tests", "watch"] and "parts" in created


def test_send_takes_an_action_or_a_sentence_and_nothing_else(rig):
    c, eng, _ = rig
    cid = new(c)
    assert c.post(f"/api/chat/{cid}/send", json={}).status_code == 422
    assert c.post(f"/api/chat/{cid}/send", json={"text": "   "}).status_code == 422
    assert c.post(f"/api/chat/{cid}/send", json={"text": "x" * 2001}).status_code == 422
    assert c.post(f"/api/chat/{cid}/send", json={"action": {"args": {}}}).status_code == 422
    assert c.post(f"/api/chat/{cid}/send", json={"action": {"type": "x" * 41}}).status_code == 422
    assert c.post(f"/api/chat/{cid}/send", content="not json", headers={"content-type": "application/json"}).status_code == 422
    r = c.post(f"/api/chat/{cid}/send", json={"action": {"type": "undo", "args": {}}, "label": "Undo"})
    assert r.status_code == 202 and r.json()["intent"]["kind"] == "undo"
    wait_idle(eng, cid)
    msgs = eng.get(cid).view.messages
    assert msgs[0]["role"] == "user" and msgs[0]["kind"] == "action" and msgs[0]["text"] == "Undo"


def test_sending_the_same_client_message_id_twice_runs_the_turn_once(rig):
    c, eng, _ = rig
    cid = new(c)
    a = c.post(f"/api/chat/{cid}/send", json={"text": BOAT, "client_msg_id": "same"}).json()
    b = c.post(f"/api/chat/{cid}/send", json={"text": BOAT, "client_msg_id": "same"})
    assert b.status_code == 202 and b.json()["duplicate"] is True and b.json()["turn_id"] == a["turn_id"] == "t1"
    wait_idle(eng, cid)
    chat = eng.get(cid)
    assert [m["text"] for m in chat.view.messages if m["role"] == "user"] == [BOAT]
    assert len([e for e in chat.events if e["type"] == "card.create" and e["card"]["id"] == "understanding"]) == 1


def test_an_unexpected_failure_is_one_scrubbed_sentence_never_a_traceback(rig, monkeypatch):
    c, eng, tmp = rig
    cid = new(c)
    (tmp / "llm.json").write_text('{"api_key": "%s"}' % KEY)

    def boom(*a, **k):
        raise RuntimeError(f"upstream said {KEY}")
    monkeypatch.setattr(eng, "send", boom)
    r = c.post(f"/api/chat/{cid}/send", json={"text": "hello"})
    assert r.status_code == 500 and KEY not in r.text and "***" in r.json()["detail"] and "Traceback" not in r.text


def test_hardware_actions_over_http_are_refused_with_a_card_and_nothing_happens(rig):
    c, eng, _ = rig
    cid = new(c)
    c.post(f"/api/chat/{cid}/send", json={"text": BOAT})
    wait_idle(eng, cid)
    runs = len(eng.get(cid).s["runs"])
    for kind in ("arm", "drive", "checklist", "hw", "estop"):
        assert c.post(f"/api/chat/{cid}/send", json={"action": {"type": kind, "args": {"speed": 1}}}).status_code == 202
    wait_idle(eng, cid)
    chat = eng.get(cid)
    blocked = [x for x in chat.view.cards.values() if x["type"] == "blocked"]
    assert len(blocked) == 5 and all("Hardware screen" in b["reason"] for b in blocked) and len(chat.s["runs"]) == runs


# ------------------------------------------------------------------ the long-poll
def test_a_poll_with_nothing_new_waits_then_answers_empty(rig):
    c, eng, _ = rig
    cid = new(c)
    _, last = poll_all(c, cid)
    t0 = time.time()
    r = c.get(f"/api/chat/{cid}/events", params={"after": last, "wait": 0.4}).json()
    assert r == {"events": [], "last": last, "more": False} and 0.3 < time.time() - t0 < 3.0


def test_a_poll_returns_the_moment_something_happens(rig):
    c, eng, _ = rig
    cid = new(c)
    _, last = poll_all(c, cid)
    threading.Timer(0.4, lambda: c.post(f"/api/chat/{cid}/send", json={"text": "hello"})).start()
    t0 = time.time()
    r = c.get(f"/api/chat/{cid}/events", params={"after": last, "wait": 20}).json()
    assert 0.3 < time.time() - t0 < 5 and r["events"] and r["events"][0]["seq"] == last + 1 and r["events"][0]["type"] == "message"


def test_resuming_with_after_seq_gives_no_gaps_and_no_duplicates(rig):
    c, eng, _ = rig
    cid = new(c)
    chat = eng.get(cid)
    seen, last, finished = [], 0, threading.Event()

    def sender():
        for t in (BOAT, "make the waves bigger", "10 tries", "add rain", "undo", "what can you not test", "build sheet", "hello"):
            c.post(f"/api/chat/{cid}/send", json={"text": t})
            time.sleep(0.15)
        chat.wait_idle(120)
        finished.set()
    th = threading.Thread(target=sender)
    th.start()
    rounds = 0
    while True:
        rounds += 1
        if rounds % 4 == 0:                                        # an answer that never reached the page: it asks again from what it has
            c.get(f"/api/chat/{cid}/events", params={"after": last, "wait": 0})
        r = c.get(f"/api/chat/{cid}/events", params={"after": last, "wait": 0.5}).json()
        seen += r["events"]
        last = r["last"]
        if finished.is_set() and not r["events"]:
            break
    th.join()
    tail, _ = poll_all(c, cid, last)
    seen += tail
    assert [e["seq"] for e in seen] == list(range(1, len(seen) + 1)), "gaps or duplicates"
    full, _ = poll_all(c, cid)
    assert seen == full and len(seen) == chat.seq and rounds > 8


def test_a_long_log_is_returned_in_pages_with_more_set(rig):
    c, eng, _ = rig
    cid = new(c)
    chat = eng.get(cid)
    for i in range(1200):
        chat.emit("chips", chips=[{"id": f"c{i}", "label": str(i), "action": {"type": "undo", "args": {}}, "kind": "secondary", "enabled": True, "reason": None, "tag": None}])
    first = c.get(f"/api/chat/{cid}/events", params={"after": 0}).json()
    assert len(first["events"]) == 500 and first["more"] is True and first["last"] == 500
    allv, last = poll_all(c, cid)
    assert [e["seq"] for e in allv] == list(range(1, chat.seq + 1)) and last == chat.seq


def test_an_after_that_is_ahead_of_the_chat_says_reset(rig):
    c, eng, _ = rig
    cid = new(c)
    r = c.get(f"/api/chat/{cid}/events", params={"after": 99999}).json()
    assert r["reset"] is True and r["events"] == [] and r["last"] == eng.get(cid).seq


def test_the_fast_resume_path_equals_the_full_replay(rig):
    c, eng, _ = rig
    cid = new(c)
    c.post(f"/api/chat/{cid}/send", json={"text": BOAT})
    wait_idle(eng, cid)
    c.post(f"/api/chat/{cid}/send", json={"text": "add rain"})
    wait_idle(eng, cid)
    st = c.get(f"/api/chat/{cid}/state").json()
    c.post(f"/api/chat/{cid}/send", json={"text": "make the waves bigger"})
    wait_idle(eng, cid)
    tail, _ = poll_all(c, cid, st["seq"])
    assert tail and tail[0]["seq"] == st["seq"] + 1
    from simlab.chat.chat import View
    v = View()
    v.messages, v.order = list(st["view"]["messages"]), [x["id"] for x in st["view"]["cards"]]
    v.cards, v.chips, v.dock = {x["id"]: x for x in st["view"]["cards"]}, st["view"]["chips"], st["view"]["dock"]
    for e in tail:
        v.apply(e)
    assert v.snapshot()["cards"] == eng.get(cid).view.snapshot()["cards"] and v.snapshot()["messages"] == eng.get(cid).view.snapshot()["messages"]


# ------------------------------------------------------------------ the state snapshot
def test_the_state_snapshot_has_what_the_details_sheet_draws(rig):
    c, eng, _ = rig
    cid = new(c)
    c.post(f"/api/chat/{cid}/send", json={"text": PIPE})
    wait_idle(eng, cid)
    st = c.get(f"/api/chat/{cid}/state").json()
    assert st["phase"] == "ready" and st["platform"] == "ground" and st["plan"]["task"]["preset"] and st["cases"] and st["proposals"] and st["sheet"]["parts"]
    assert {"summary", "matrix", "not_tested", "statement", "limit_line", "history", "cursor", "can_undo", "can_redo", "budget", "resources", "limits", "view"} <= set(st)
    assert st["summary"]["tests"] == len([x for x in st["cases"] if x["status"] != "untestable"]) and "Simulation only" in st["limit_line"]
    assert st["view"]["cards"] and st["view"]["messages"] and "raw_plan" not in str(st["ai"])
    assert all("vehicle" not in p for p in st["proposals"])                                       # the machine spec stays on the server


# ------------------------------------------------------------------ stop
def wait_for(cond, timeout=30.0, what="condition"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def test_stop_ends_a_test_run_after_the_current_condition_and_keeps_what_finished(tmp_path, monkeypatch):
    gate = threading.Event()
    fake = SweepFake(monkeypatch, gate=gate)
    eng = make_engine(tmp_path, monkeypatch)
    chat_router.set_engine(eng)
    try:
        with TestClient(build_app()) as c:
            cid = new(c)
            c.post(f"/api/chat/{cid}/send", json={"text": BOAT})
            wait_idle(eng, cid)
            chat = eng.get(cid)
            c.post(f"/api/chat/{cid}/send", json={"text": "run all"})
            assert fake.started.wait(30)
            c.post(f"/api/chat/{cid}/send", json={"action": {"type": "stop", "args": {"job": "tests"}}})
            wait_for(lambda: chat.view.cards["results-1"]["progress"]["stopping"] is True, what="stopping to show")
            assert any("Stopping after the current condition" in m["text"] for m in chat.view.messages)
            gate.set()
            assert chat.wait_idle(60)
            res = chat.view.cards["results-1"]
            assert res["state"] == "stopped" and res["progress"]["done"] < res["progress"]["total"] and res["matrix"]["not_run"] > 0 and res["matrix"]["met"] >= 1
            assert "not run" in res["summary"] and res["statement"] and res["not_tested"]
            assert chat.s["stress"]["state"] == "stopped" and not eng.resources.sweep.snapshot()["busy"]
    finally:
        gate.set()
        chat_router.set_engine(None)


def test_stop_ends_the_ai_plan_and_the_live_view(tmp_path, monkeypatch):
    fake = FakeOpenRouter()
    runner = FakeRunner(finish_after=60)
    eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True, runner=runner)
    chat_router.set_engine(eng)
    try:
        import json
        text = json.dumps(boat_plan())
        chunks = [text[i:i + 60] for i in range(0, len(text), 60)]
        fake.script.append({"chunks": chunks, "delay": 1.0})
        with TestClient(build_app()) as c:
            cid = new(c)
            c.post(f"/api/chat/{cid}/send", json={"text": BOAT})
            chat = eng.get(cid)
            wait_for(lambda: chat.view.cards.get("improve", {}).get("state") == "running" and chat.s.get("runs") and chat.s["runs"][-1]["state"] == "running", what="AI and live view running")
            c.post(f"/api/chat/{cid}/send", json={"action": {"type": "stop", "args": {"job": "all"}}})
            wait_for(lambda: chat.view.cards["improve"]["state"] == "stopped", what="the plan to stop")
            wait_for(lambda: chat.s["runs"][-1]["state"] == "stopped", what="the live view to stop")
            assert runner.stops >= 1 and chat.wait_idle(30) and chat.book.source == "catalogue"
            assert chat.view.dock["busy"] == {"plan": False, "sweep": False, "live": False}
    finally:
        chat_router.set_engine(None)
        fake.stop()


def test_a_second_run_over_http_waits_as_a_queued_card(tmp_path, monkeypatch):
    gate = threading.Event()
    fake = SweepFake(monkeypatch, gate=gate)
    eng = make_engine(tmp_path, monkeypatch)
    chat_router.set_engine(eng)
    try:
        with TestClient(build_app()) as c:
            cid = new(c)
            c.post(f"/api/chat/{cid}/send", json={"text": BOAT})
            wait_idle(eng, cid)
            c.post(f"/api/chat/{cid}/send", json={"text": "run all"})
            assert fake.started.wait(30)
            c.post(f"/api/chat/{cid}/send", json={"text": "test waves"})
            wait_for(lambda: (lambda st: st["resources"]["sweep"]["queue"] and st["view"]["dock"].get("queue") and "results-2" in [x["id"] for x in st["view"]["cards"]])(c.get(f"/api/chat/{cid}/state").json()),
                     what="a queued run in the state")
            st = c.get(f"/api/chat/{cid}/state").json()
            assert st["resources"]["sweep"]["queue"][0]["position"] == 1 and st["view"]["dock"]["queue"][0]["position"] == 1
            assert any(x["id"] == "results-2" and x["state"] == "queued" for x in st["view"]["cards"])
    finally:
        gate.set()
        eng.get(cid).wait_idle(60)
        chat_router.set_engine(None)


# ------------------------------------------------------------------ the protocol document says what the server does
def test_the_protocol_document_names_every_card_event_and_action_the_server_has():
    from simlab.chat import intents
    doc = (ROOT / "docs" / "CHAT_PROTOCOL.md").read_text(encoding="utf-8")
    for name in (*CARD_TYPES, *EVENT_TYPES, *intents.ACTION_TYPES, *intents.FORBIDDEN_ACTIONS):
        assert f"`{name}`" in doc or f'"{name}"' in doc, name
    for fixed in ("/api/chat/capabilities", "/api/chat/new", "/api/chat/latest", "/events?after=", "/send", "/state", "long-poll", "202", "client_msg_id", "merge patch"):
        assert fixed in doc, fixed
    paths = {r.path for r in chat_router.router.routes}
    assert paths == {"/chat", "/api/chat/capabilities", "/api/chat/new", "/api/chat/latest", "/api/chat/{cid}/events", "/api/chat/{cid}/send", "/api/chat/{cid}/state"}
