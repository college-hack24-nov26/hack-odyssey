"""Chats on disk: the log replays to the same page, writes are atomic, a restart restores the chat and marks dead jobs, and the key is never stored."""
import copy
import json
import os
import shutil
import threading

import pytest

pytest.importorskip("mujoco")
from simlab.chat import chat as chatmod  # noqa: E402
from simlab.chat import store  # noqa: E402
from simlab.chat.orchestrator import Engine  # noqa: E402
from simlab.chat.store import Store  # noqa: E402
from tests.chat_helpers import BOAT, KEY, SweepFake, boat_plan, gr, make_engine, say, script_plan, card  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402


# ------------------------------------------------------------------ ids
def test_ids_are_twelve_hex_characters_and_can_never_leave_the_folder(tmp_path):
    st = Store(tmp_path)
    cid = st.new_id()
    assert store.ID_RE.match(cid) and len(cid) == 12
    for bad in ("../x", "..", "a" * 11, "A" * 12, "x" * 12, "", "0123456789ab/..", None, 12):
        assert not st.valid_id(bad)
    with pytest.raises(KeyError):
        st.folder("../../etc")
    with pytest.raises(KeyError):
        st.load("0" * 12)


def test_the_listing_ignores_folders_that_are_not_chats(tmp_path):
    st = Store(tmp_path)
    (tmp_path / "notachat").mkdir()
    (tmp_path / "_budget.json").write_text("{}")
    cid = st.new_id()
    st.append_event(cid, {"seq": 1, "ts": 1.0, "type": "chips", "chips": []})
    assert [c["id"] for c in st.list()] == [cid] and st.latest() == cid and st.exists(cid) and not st.exists("0" * 12)
    st.delete(cid)
    assert st.list() == []


# ------------------------------------------------------------------ the log replays to the same page
def test_replaying_the_event_log_gives_exactly_the_page_that_was_live(tmp_path, monkeypatch):
    SweepFake(monkeypatch)
    eng = make_engine(tmp_path, monkeypatch)
    try:
        chat = eng.new_chat()
        for t in (BOAT, "make the waves bigger", "add rain", "10 tries", "test it", "undo", "why did it fail", "build sheet"):
            say(eng, chat, t)
        live = chat.view.snapshot()
        events, session = eng.store.load(chat.id)
        assert [e["seq"] for e in events] == list(range(1, chat.seq + 1))
        assert chatmod.replay(events).snapshot() == live
        assert session["seq"] == chat.seq and session["id"] == chat.id and session["book"]["platform"] == "water"
    finally:
        eng.close()


def test_a_restart_restores_the_chat_its_plan_its_tests_and_its_results(tmp_path, monkeypatch):
    SweepFake(monkeypatch)
    eng = make_engine(tmp_path, monkeypatch)
    chat = eng.new_chat()
    for t in (BOAT, "make the waves bigger", "add rain", "test it", "undo"):
        say(eng, chat, t)
    want_state = eng.state(chat)
    cid = chat.id
    eng.close()
    eng2 = Engine(store=Store(tmp_path / "chats"), reports_root=tmp_path / "reports", mujoco=True)
    try:
        c2 = eng2.get(cid)
        assert c2 is not chat and c2.seq == chat.seq and c2.view.snapshot() == chat.view.snapshot()
        assert c2.book.cases == chat.book.cases and c2.book.cursor == chat.book.cursor and c2.book.params == chat.book.params
        got = eng2.state(c2)
        for k in ("platform", "cases", "plan", "summary", "matrix", "results", "pick", "history", "cursor", "can_undo", "can_redo", "not_tested"):
            assert got[k] == want_state[k], k
        assert got["proposals"] == want_state["proposals"] and got["sheet"] == want_state["sheet"]
        # and the chat keeps working: counters continue, a new edit applies and undoes
        n = c2.seq
        say(eng2, c2, "redo")
        assert c2.seq > n and eng2.latest().id == cid
        ids = [m["id"] for m in c2.view.messages]
        assert len(ids) == len(set(ids))                          # message ids continue after the restart, no clash
    finally:
        eng2.close()


# ------------------------------------------------------------------ atomic writes
def test_the_old_file_survives_a_crash_between_the_temp_write_and_the_replace(tmp_path, monkeypatch):
    target = tmp_path / "session.json"
    store.atomic_write(target, '{"v": 1}')

    def boom(src, dst):
        raise KeyboardInterrupt("power cut")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        store.atomic_write(target, '{"v": 2}')
    monkeypatch.undo()
    assert json.loads(target.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["session.json"]          # and the temp file was cleaned up


def test_a_stray_temp_file_from_a_hard_crash_is_ignored(tmp_path):
    st = Store(tmp_path)
    cid = st.new_id()
    st.append_event(cid, {"seq": 1, "ts": 1.0, "type": "chips", "chips": []})
    st.save_session(cid, {"id": cid, "title": "t", "seq": 1})
    (tmp_path / cid / "session.json.tmp-1-2-abcdef").write_text("{ half a file")
    events, session = st.load(cid)
    assert len(events) == 1 and session["title"] == "t" and [c["id"] for c in st.list()] == [cid]


def test_windows_holding_the_file_for_a_moment_is_retried(tmp_path, monkeypatch):
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(store.time, "sleep", lambda s: None)
    target = tmp_path / "x.json"
    store.atomic_write(target, "ok")
    assert target.read_text() == "ok" and len(calls) == 3
    monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError("never")))
    with pytest.raises(PermissionError):
        store.atomic_write(target, "again", retries=3)
    assert target.read_text() == "ok"


def test_a_log_cut_short_by_a_crash_loses_only_its_last_line(tmp_path):
    st = Store(tmp_path)
    cid = st.new_id()
    for i in (1, 2, 3):
        st.append_event(cid, {"seq": i, "ts": float(i), "type": "chips", "chips": []})
    with open(tmp_path / cid / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"seq": 4, "ts": 4.0, "type": "chi')
    events, _ = st.load(cid)
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_a_missing_or_broken_session_file_still_replays_the_log(tmp_path):
    st = Store(tmp_path)
    cid = st.new_id()
    st.append_event(cid, {"seq": 1, "ts": 1.0, "type": "chips", "chips": [{"id": "a", "label": "A", "action": {"type": "undo", "args": {}}}]})
    assert st.load(cid)[1] is None
    (tmp_path / cid / "session.json").write_text("not json")
    assert st.load(cid)[1] is None
    eng = Engine(store=st, mujoco=True)
    try:
        c = eng.get(cid)
        assert c.view.chips and c.book is None
    finally:
        eng.close()


# ------------------------------------------------------------------ plans come back after a restart
def test_the_ai_plan_is_put_back_where_the_goal_tabs_stress_test_looks_for_it(tmp_path, monkeypatch):
    eng = make_engine(tmp_path, monkeypatch)
    chat = eng.new_chat()
    raw = boat_plan()
    chat.s["ai"] = {"state": "applied", "plan_id": "abc123def4", "raw_plan": raw}
    chat.save()
    eng.close()
    monkeypatch.setattr(gr, "PLANS", {})
    eng2 = Engine(store=Store(tmp_path / "chats"), mujoco=True)
    try:
        eng2.get(chat.id)
        assert gr.PLANS["abc123def4"] == raw
    finally:
        eng2.close()


# ------------------------------------------------------------------ jobs that died with the old process
def test_a_job_that_was_running_when_the_server_stopped_comes_back_interrupted_with_retry(tmp_path, monkeypatch):
    gate = threading.Event()
    fake = SweepFake(monkeypatch, gate=gate)
    eng = make_engine(tmp_path, monkeypatch)
    try:
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        eng.send(chat, text="test it")
        assert fake.started.wait(30)
        # a crash right now: what is on disk is what a restart finds
        snap = tmp_path / "snapshot"
        shutil.copytree(tmp_path / "chats" / chat.id, snap / chat.id)
        assert card(chat, "results-1")["state"] == "running"
        eng2 = Engine(store=Store(snap), reports_root=tmp_path / "reports2", mujoco=True)
        try:
            c2 = eng2.get(chat.id)
            res = c2.view.cards["results-1"]
            assert res["state"] == "interrupted" and res["progress"]["stopping"] is False
            assert any("interrupted" in m["text"] for m in c2.view.messages if m["role"] == "assistant")
            retry = [c for c in c2.view.chips if c["id"] == "retry"]
            assert retry and retry[0]["action"]["type"] == "run_tests"
            assert c2.s["stress"]["state"] == "interrupted"
            assert all(r["state"] != "running" for r in c2.s.get("runs", []))
            assert c2.view.dock["busy"] == {"plan": False, "sweep": False, "live": False}
        finally:
            eng2.close()
    finally:
        gate.set()
        chat.wait_idle(60)
        eng.close()


# ------------------------------------------------------------------ the key is never stored
def test_the_api_key_appears_in_no_file_and_no_event_even_when_the_server_echoes_it(tmp_path, monkeypatch):
    fake = FakeOpenRouter()
    try:
        fake.script += [{"status": 500, "error": f"upstream said {KEY} is bad"}] * 6
        eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
        try:
            chat = eng.new_chat()
            say(eng, chat, BOAT, wait=120)
            say(eng, chat, "make the waves bigger")
            assert chat.view.cards["improve"]["state"] in ("unavailable", "failed")
            blob = json.dumps(chat.events) + json.dumps(eng.state(chat)) + json.dumps(eng.capabilities())
            assert KEY not in blob
            for p in (tmp_path / "chats").rglob("*"):
                if p.is_file():
                    assert KEY not in p.read_text(encoding="utf-8", errors="replace"), p
        finally:
            eng.close()
    finally:
        fake.stop()
