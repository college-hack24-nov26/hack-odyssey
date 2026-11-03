"""The model is called for a new job and for nothing else. After the first message, a long scripted conversation (edits, questions, buttons, tests, watching, undo,
nonsense) must make zero requests to the client, even with a key saved and requests left."""
import json

import pytest

pytest.importorskip("mujoco")
from simlab.llm import OpenRouter  # noqa: E402
from simlab.llm import client as llm_client  # noqa: E402
from tests.chat_helpers import BOAT, PIPE, SweepFake, boat_plan, gr, make_engine, say, script_plan  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402

SCRIPT = [
    "make the waves bigger", "add wind 12 m/s", "10 tries", "undo", "redo", "bar 60%", "add rain", "what can you not test", "why did it fail", "how long will it take",
    "what does this mean", "build sheet", "hardware", "train a detector", "hello", "asdf qwer", "make it last longer", "add a thruster failure", "watch it", "watch the worst waves",
    "test waves", "why did it fail", "watch the weakest", "set the range to 300 m", "undo", "a rover for a 30 cm pipe", "stop", "/sheet", "/undo", "/test waves",
    "waves up to 0.75 m", "remove the debris test", "where are we", "can it handle a storm", "make it calmer",
]
ACTIONS = [{"type": "answer", "args": {"topic": "limits"}}, {"type": "keep_here", "args": {}}, {"type": "pick_design", "args": {"index": 0}}, {"type": "edit_test", "args": {"case": "tc_waves", "tries": 3}},
           {"type": "add_test", "args": {"conditions": {"wave_hs": 0.5, "wind_ms": 6}}}, {"type": "sheet", "args": {"format": "html"}}, {"type": "use_kit", "args": {"kit": "my_rover"}},
           {"type": "set_job_number", "args": {"name": "wave_hs", "value": 0.8}}, {"type": "run_tests", "args": {"cases": ["tc_waves"]}}]


@pytest.fixture()
def fake():
    f = FakeOpenRouter()
    yield f
    f.stop()


def make_counting(monkeypatch, fake):
    calls = []

    def factory():
        calls.append(1)
        return OpenRouter(base_url=fake.url, sleep=lambda s: None)
    monkeypatch.setattr(gr, "make_client", factory)
    return calls


def forbid_every_client_method(monkeypatch):
    def nope(*a, **k):
        raise AssertionError("the model was called")
    for name in ("chat_with_fallback", "chat", "usage", "choose_models", "_stream"):
        monkeypatch.setattr(OpenRouter, name, nope)


def test_after_the_first_message_a_long_chat_makes_zero_client_calls(tmp_path, monkeypatch, fake):
    SweepFake(monkeypatch)
    eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
    calls = make_counting(monkeypatch, fake)
    try:
        script_plan(fake, boat_plan())
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        assert chat.view.cards["improve"]["state"] == "done" and len(fake.chat_requests()) == 1       # the new job used its one request
        eng.budget.snapshot(force=True)                                                              # the day's counter read, now cached for a minute
        calls_after_first, requests_after_first = len(calls), len(fake.requests)
        forbid_every_client_method(monkeypatch)
        n = 0
        for t in SCRIPT:
            say(eng, chat, t)
            n += 1
        for a in ACTIONS:
            say(eng, chat, action=a)
            n += 1
        say(eng, chat, action={"type": "use_ai_keep_edits", "args": {}})
        say(eng, chat, "undo")
        n += 2
        assert n >= 30
        assert len(calls) == calls_after_first and len(fake.requests) == requests_after_first       # not one client made, not one request sent
        assert not [e for e in chat.events if e["type"] == "error"], [e for e in chat.events if e["type"] == "error"]
        assert chat.view.cards["improve"]["state"] == "applied"                                      # and the AI choice itself was local too
        assert eng.budget.used_today() == 1
        for cid in ("understanding", "design", "tests", "results-1", "parts", "limits"):
            assert cid in chat.view.cards, cid
    finally:
        eng.close()


def test_the_first_message_with_no_key_makes_zero_calls(tmp_path, monkeypatch, fake):
    eng = make_engine(tmp_path, monkeypatch, fake=fake)
    calls = make_counting(monkeypatch, fake)
    forbid_every_client_method(monkeypatch)
    try:
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        assert len(calls) == 0 and fake.requests == [] and "improve" not in chat.view.cards and chat.book is not None
        say(eng, chat, "make the waves bigger")
        assert len(calls) == 0 and fake.requests == []
    finally:
        eng.close()


def test_the_first_message_with_the_planner_switched_off_makes_zero_calls_even_with_a_key(tmp_path, monkeypatch, fake):
    eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
    (tmp_path / "chat.json").write_text(json.dumps({"ai": "never"}))
    calls = make_counting(monkeypatch, fake)
    forbid_every_client_method(monkeypatch)
    try:
        chat = eng.new_chat()
        say(eng, chat, PIPE)
        assert len(calls) == 0 and fake.requests == [] and "improve" not in chat.view.cards and chat.book is not None
        assert any("switched off" in m["text"] or "off" in m["text"] for m in chat.view.messages if m["role"] == "assistant")
        assert chat.view.budget["enabled"] is False and chat.view.budget["setting"] == "never"
    finally:
        eng.close()


def test_the_instant_example_chips_are_local(tmp_path, monkeypatch, fake):
    eng = make_engine(tmp_path, monkeypatch, fake=fake)
    forbid_every_client_method(monkeypatch)
    try:
        chat = eng.new_chat()
        chips = [c for c in chat.view.chips if c["tag"] == "instant"]
        assert len(chips) == 4
        for ch in chips[:3]:
            c = eng.new_chat()
            say(eng, c, action=ch["action"], label=ch["label"])
            assert c.book is not None and "design" in c.view.cards and "improve" not in c.view.cards
        assert fake.requests == []
    finally:
        eng.close()


def test_only_a_new_job_can_start_the_ai_and_a_job_in_a_chat_that_has_one_cannot(tmp_path, monkeypatch, fake):
    eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
    try:
        script_plan(fake, boat_plan())
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        n = len(fake.chat_requests())
        for t in ("a drone to watch a field for 20 minutes", "a rover for a 30 cm pipe", "/new a rover for a pipe", "new job"):
            say(eng, chat, t)
        assert len(fake.chat_requests()) in (n, n + 1)            # only "/new ..." can, by making another chat, whose own new job may use one request
        before = len(fake.chat_requests())
        say(eng, chat, "make a boat for rescuing someone in a lake")                            # a description, but this chat already has a job: asks, never replaces silently
        assert len(fake.chat_requests()) == before
        assert [c["id"] for c in chat.view.chips] == ["new_chat", "keep_here"]
    finally:
        eng.close()
