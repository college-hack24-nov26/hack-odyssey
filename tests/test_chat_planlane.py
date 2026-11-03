"""The AI step of a new job, against a fake OpenRouter: the draft appears first, the AI plan arrives as a comparison, failures leave the draft alone, and the
model is called once per new job and for nothing else."""
import json
import time

import pytest

pytest.importorskip("mujoco")
from simlab.chat import words  # noqa: E402
from tests.chat_helpers import BOAT, KEY, PIPE, boat_plan, card, gr, make_engine, rover_plan, say  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402


@pytest.fixture()
def fake():
    f = FakeOpenRouter()
    yield f
    f.stop()


def slow_plan(fake, plan, secs=1.5):
    text = json.dumps(plan)
    chunks = [text[i:i + 60] for i in range(0, len(text), 60)]
    fake.script.append({"chunks": chunks, "delay": secs / len(chunks)})


def wait_for(cond, timeout=60.0, what="condition"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def improve(chat):
    return chat.view.cards.get("improve", {})


def boat_ai_plan():
    """What the AI would say for the flood boat: another wave level, a thruster test only, one thing it cannot test."""
    return boat_plan(params={"range_m": 150, "wave_hs": 1.2, "minutes": 45}, stress={"hazards": [{"id": "waves", "levels": [0.0, 0.4, 0.8, 1.2], "ask": 1.2}], "faults": ["thruster_out"]})


@pytest.fixture()
def rig(tmp_path, monkeypatch, fake):
    eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
    yield eng, fake
    eng.close()


# ------------------------------------------------------------------ the draft comes first
def test_the_draft_cards_exist_before_the_ai_result(rig):
    eng, fake = rig
    slow_plan(fake, boat_ai_plan(), secs=4.0)
    chat = eng.new_chat()
    eng.send(chat, text=BOAT)
    wait_for(lambda: all(c in chat.view.cards for c in ("understanding", "design", "tests", "parts")) and improve(chat).get("state") == "running", what="draft + a running AI step")
    assert improve(chat)["state"] == "running" and improve(chat)["diff"] is None and "draft stays usable" in improve(chat)["draft_note"].lower()
    assert card(chat, "understanding")["badge"] == {"source": "draft", "label": "Quick draft"}
    assert any(a["type"] == "stop" for a in improve(chat)["actions"])
    assert chat.view.dock["ai"]["running"] is True and chat.view.dock["busy"]["plan"] is True
    # local messages are not blocked by the AI step: an edit acts on the draft at once
    eng.send(chat, text="10 tries")
    wait_for(lambda: card(chat, "tests")["rows"][0]["tries"]["n"] == 10, what="the edit to apply during the AI wait")
    assert improve(chat)["state"] == "running"
    assert chat.wait_idle(120)
    assert improve(chat)["state"] == "done"
    assert len(fake.chat_requests()) == 1                                      # one logical call


def test_the_ai_plan_arrives_as_a_comparison_and_use_ai_replaces_the_draft(rig):
    eng, fake = rig
    slow_plan(fake, boat_ai_plan(), secs=0.5)
    chat = eng.new_chat()
    say(eng, chat, BOAT)
    imp = improve(chat)
    assert imp["state"] == "done" and imp["diff"]["count"] >= 2 and imp["model"]
    d = imp["diff"]
    assert {c["area"] for c in d["changes"]} >= {"job", "tests"} and d["ai"]["platform"] == "water" and d["base"] == {"source": "draft", "platform": "water"}
    assert all(set(c) == {"id", "area", "kind", "label", "draft", "ai", "yours"} for c in d["changes"])
    waves = next(c for c in d["changes"] if c["area"] == "job" and c["label"] == "Worst waves")
    assert waves["draft"] == "1 m" and waves["ai"] == "1.2 m" and waves["yours"] is False
    assert [c["id"] for c in chat.view.chips] == ["use_ai", "keep_mine", "use_ai_keep_edits"]
    assert [a["type"] for a in imp["actions"]] == ["use_ai", "keep_mine", "use_ai_keep_edits"]
    assert any("differs from your draft" in t for t in [m["text"] for m in chat.view.messages if m["role"] == "assistant"])
    assert chat.book.source == "catalogue"                                     # nothing changed until the person chooses
    say(eng, chat, action={"type": "use_ai", "args": {}})
    assert improve(chat)["state"] == "applied" and chat.book.source == "ai" and chat.s["source"] == "ai"
    assert card(chat, "understanding")["badge"] == {"source": "ai", "label": "AI version"}
    assert chat.book.params["wave_hs"] == 1.2 and chat.book.params["minutes"] == 45
    assert [c["sweep"]["hazard"] for c in chat.book.cases if c["kind"] == "sweep"] == ["waves"]
    assert chat.book.for_hazard("waves")["sweep"]["levels"] == [0.0, 0.4, 0.8, 1.2]
    assert chat.book.cases[-1]["title"] == "Rain" and chat.book.cases[-1]["status"] == "untestable"        # what the AI said cannot be tested stays on the not-tested list
    assert card(chat, "tests")["counts"]["untestable"] == 1 and "Rain" in " ".join(chat.book.not_tested_items())
    plan_id = chat.s["ai"]["plan_id"]
    assert gr.PLANS[plan_id]["understanding"]["summary"] == "Flood rescue boat"          # the Goal tab's stress test can use this plan
    assert [c["id"] for c in chat.view.chips][:1] != ["use_ai"]


def test_keep_mine_leaves_the_draft_exactly_as_it_was(rig):
    eng, fake = rig
    slow_plan(fake, boat_ai_plan(), secs=0.3)
    chat = eng.new_chat()
    say(eng, chat, BOAT)
    before = (chat.book.to_dict(), json.dumps(card(chat, "tests")["rows"]))
    say(eng, chat, action={"type": "keep_mine", "args": {}})
    assert improve(chat)["state"] == "kept" and chat.book.source == "catalogue" and (chat.book.to_dict(), json.dumps(card(chat, "tests")["rows"])) == before
    assert "Kept your draft" in [m["text"] for m in chat.view.messages if m["role"] == "assistant"][-1]
    assert all(c["id"] not in ("use_ai", "keep_mine") for c in chat.view.chips)


def test_use_ai_keep_my_edits_replays_the_persons_edits_on_the_ai_plan(rig):
    eng, fake = rig
    slow_plan(fake, boat_ai_plan(), secs=3.0)
    chat = eng.new_chat()
    eng.send(chat, text=BOAT)
    wait_for(lambda: improve(chat).get("state") == "running" and "tests" in chat.view.cards, what="draft")
    # while the AI works, the person edits the draft: waves levels, tries, a test the AI plan does not have, and something untestable
    for t in ("waves up to 0.75 m", "7 tries", "remove the wind test", "add fog"):
        eng.send(chat, text=t)
    wait_for(lambda: len(chat.book.history) == 4, what="the four edits")
    assert chat.wait_idle(120) and improve(chat)["state"] == "done"
    d = improve(chat)["diff"]
    assert d["yours"] >= 1 and len(d["edits"]) == 4
    yours = [c for c in d["changes"] if c["yours"]]
    assert yours and all(c["area"] in ("tests", "job", "assumptions") for c in yours)
    say(eng, chat, action={"type": "use_ai_keep_edits", "args": {}})
    assert improve(chat)["state"] == "applied" and chat.book.source == "ai"
    waves = chat.book.for_hazard("waves")
    assert 0.75 in waves["sweep"]["levels"] and "levels" in waves["touched"]               # her edit sits on top of the AI's levels, marked as hers
    assert waves["tries"]["n"] == 7 and chat.book.get("tc_n_fog") is not None
    msgs = [m["text"] for m in chat.view.messages if m["role"] == "assistant"]
    assert any("with your" in t and "on top" in t for t in msgs)
    assert any("no longer applied" in t for t in msgs)                                       # "remove the wind test": the AI plan has none, so that edit is listed, not lost
    assert chat.book.replay_failures and chat.book.replay_failures[0]["op"]["op"] == "remove"
    assert card(chat, "tests")["counts"]["tests"] == len(chat.book.testable())


def test_use_ai_without_edits_drops_them(rig):
    eng, fake = rig
    slow_plan(fake, boat_ai_plan(), secs=2.0)
    chat = eng.new_chat()
    eng.send(chat, text=BOAT)
    wait_for(lambda: improve(chat).get("state") == "running" and "tests" in chat.view.cards)
    eng.send(chat, text="7 tries")
    wait_for(lambda: len(chat.book.history) == 1)
    assert chat.wait_idle(120)
    say(eng, chat, action={"type": "use_ai", "args": {}})
    assert chat.book.get("tc_waves")["tries"]["n"] == 4 and not chat.book.history


def test_the_plan_the_ai_made_can_be_edited_and_re_checked_with_no_new_request(rig):
    eng, fake = rig
    slow_plan(fake, boat_ai_plan(), secs=0.3)
    chat = eng.new_chat()
    say(eng, chat, BOAT)
    say(eng, chat, action={"type": "use_ai", "args": {}})
    n = len(fake.chat_requests())
    say(eng, chat, "set the range to 300 m")
    assert chat.book.params["range_m"] == 300 and card(chat, "understanding")["numbers"][0]["value"] == 300
    say(eng, chat, "undo")
    assert chat.book.params["range_m"] == 150
    assert len(fake.chat_requests()) == n


# ------------------------------------------------------------------ failure, quota and stop
def test_an_ai_failure_leaves_the_draft_intact_with_a_plain_message(rig):
    eng, fake = rig
    fake.script += [{"status": 500, "error": "upstream exploded"}] * 8
    chat = eng.new_chat()
    say(eng, chat, BOAT, wait=120)
    imp = improve(chat)
    assert imp["state"] == "unavailable" and imp["reason"] and imp["closest"]
    assert all(c in chat.view.cards for c in ("understanding", "design", "tests", "parts")) and chat.book.source == "catalogue"
    last = [m["text"] for m in chat.view.messages if m["role"] == "assistant"]
    assert any(t.startswith("The AI planner was not available") and "Your draft is unchanged" in t and "Closest ready-made job" in t for t in last)
    assert "diff" in imp and imp["diff"] is None
    assert KEY not in json.dumps(chat.events)
    assert chat.view.dock["ai"]["running"] is False


def test_failed_attempts_count_against_the_day_and_a_spent_quota_turns_the_ai_off(rig, tmp_path):
    eng, fake = rig
    fake.script += [{"status": 429, "error": "You have exceeded your free-models-per-day limit"}] * 3
    chat = eng.new_chat()
    say(eng, chat, BOAT, wait=120)
    sent = len(fake.chat_requests())
    assert sent >= 1 and eng.budget.used_today() == sent
    assert improve(chat)["state"] == "unavailable"
    assert any(words.ai_quota_gone() == m["text"] for m in chat.view.messages)
    snap = eng.budget.snapshot()
    assert snap["exhausted"] is True and snap["enabled"] is False and snap["reason"] == words.RESETS
    assert chat.view.budget["enabled"] is False
    other = eng.new_chat()                                                    # the next job does not even try
    say(eng, other, PIPE)
    assert len(fake.chat_requests()) == sent and "improve" not in other.view.cards
    assert any(m["text"] == words.ai_quota_gone() for m in other.view.messages)
    assert other.book is not None and "design" in other.view.cards             # the offline draft still works


def test_with_no_requests_left_the_model_is_never_called_and_the_draft_still_appears(tmp_path, monkeypatch, fake):
    fake.key_info = {"free_model_daily_requests": {"used": 50, "limit": 50, "remaining": 0}, "is_free_tier": True}
    eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
    try:
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        assert fake.chat_requests() == [] and "improve" not in chat.view.cards and chat.book is not None
        assert chat.view.budget["exhausted"] is True and chat.view.budget["enabled"] is False
        say(eng, chat, "undo")
        assert fake.chat_requests() == []
    finally:
        eng.close()


def test_low_requests_are_warned_about_in_the_start_message(tmp_path, monkeypatch, fake):
    fake.key_info = {"free_model_daily_requests": {"used": 44, "limit": 50, "remaining": 6}, "is_free_tier": True}
    eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
    try:
        slow_plan(fake, boat_ai_plan(), secs=0.2)
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        assert any("About 6 AI requests are left today" in m["text"] for m in chat.view.messages)
        assert chat.view.budget["low"] is True and chat.view.budget["enabled"] is True
    finally:
        eng.close()


def test_stop_cancels_the_plan_and_keeps_the_draft(rig):
    eng, fake = rig
    slow_plan(fake, boat_ai_plan(), secs=30.0)
    chat = eng.new_chat()
    eng.send(chat, text=BOAT)
    wait_for(lambda: improve(chat).get("state") == "running" and "parts" in chat.view.cards, what="draft and AI")
    t0 = time.time()
    eng.send(chat, action={"type": "stop", "args": {"job": "plan"}})
    wait_for(lambda: improve(chat).get("state") == "stopped", what="the AI step to stop")
    assert time.time() - t0 < 10 and chat.wait_idle(30)
    assert chat.book.source == "catalogue" and "tests" in chat.view.cards
    assert any(m["text"] == words.ai_stopped() for m in chat.view.messages) and "still count today" in words.ai_stopped()
    assert chat.view.dock["ai"]["running"] is False and not eng.resources.plan.snapshot()["busy"]
    assert eng.budget.used_today() >= 1                                       # the request that was sent still counts


# ------------------------------------------------------------------ no ready-made job
def test_with_no_ready_made_job_the_ai_plan_becomes_the_plan(rig):
    eng, fake = rig
    fake.script.append({"chunks": [json.dumps(rover_plan())]})
    chat = eng.new_chat()
    say(eng, chat, "something to carry seed bags across my farm tracks", wait=120)
    assert len(fake.chat_requests()) == 1
    assert improve(chat)["state"] == "applied" and chat.book is not None and chat.book.source == "ai" and chat.s["source"] == "ai"
    for cid in ("understanding", "design", "tests", "watch", "parts"):
        assert cid in chat.view.cards, cid
    assert card(chat, "understanding")["badge"]["source"] == "ai" and card(chat, "design")["proposals"][0]["label"].endswith("(AI)")
    assert chat.book.platform == "ground" and [c["id"] for c in chat.book.cases if c["status"] == "untestable"] == ["tc_n_hail"]
    assert chat.book.base["questions"] == [] and chat.s["ai"].get("raw_plan", {}).get("questions") == []        # in the chat the AI never stops to ask
    assert not chat.view.chips or all(c["id"] != "use_ai" for c in chat.view.chips)
    assert chat.view.dock["done"] is True


def test_a_job_nothing_can_do_is_answered_honestly(rig):
    eng, fake = rig
    fake.script.append({"chunks": [json.dumps({"understanding": {"summary": "Map a cave", "platform": "unsupported", "why": "it needs a submarine"}})]})
    chat = eng.new_chat()
    say(eng, chat, "map a deep cave system with a robot", wait=120)
    assert improve(chat)["state"] == "unsupported" and chat.book is None
    assert any(c["type"] == "blocked" and "submarine" in c["reason"] for c in chat.view.cards.values())


def test_with_no_key_and_no_ready_made_job_the_chat_says_what_it_can_do(tmp_path, monkeypatch, fake):
    eng = make_engine(tmp_path, monkeypatch, fake=fake)
    try:
        chat = eng.new_chat()
        say(eng, chat, "organise my wedding")
        assert chat.book is None and fake.chat_requests() == []
        assert words.no_offline_draft() in [m["text"] for m in chat.view.messages]
        assert [c["id"] for c in chat.view.chips][:1] == ["ex:flood_boat"]
    finally:
        eng.close()


def test_an_ai_failure_with_no_draft_says_so_and_offers_the_three_jobs(rig):
    eng, fake = rig
    fake.script += [{"status": 500, "error": "down"}] * 6
    chat = eng.new_chat()
    say(eng, chat, "organise my wedding", wait=120)
    assert chat.book is None and improve(chat)["state"] == "unavailable"
    assert any("no ready-made job fits" in m["text"] for m in chat.view.messages if m["role"] == "assistant")
    assert [c["id"] for c in chat.view.chips][:1] == ["ex:flood_boat"]


def test_questions_the_ai_asks_never_stop_the_chat_and_cost_no_second_request(rig):
    eng, fake = rig
    p = boat_plan(questions=[{"id": "q1", "text": "How wet?", "kind": "choice", "choices": ["dry", "wet"]}])
    slow_plan(fake, p, secs=0.2)
    chat = eng.new_chat()
    say(eng, chat, BOAT)
    assert len(fake.chat_requests()) == 1 and improve(chat)["state"] == "done"
    say(eng, chat, action={"type": "use_ai", "args": {}})
    assert chat.book.base["questions"] == [] and len(fake.chat_requests()) == 1
