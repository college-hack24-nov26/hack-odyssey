"""Who may use the shared things (the AI plan, the stress test, the live view) and what a busy lane looks like: a queued 'Up next' chip, never an error."""
import threading
import time

import pytest

pytest.importorskip("mujoco")
from simlab.chat.resources import Lane, Resources  # noqa: E402
from tests.chat_helpers import BOAT, FakeRunner, PIPE, SweepFake, boat_plan, card, make_engine, say, script_plan  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402


def wait_for(cond, timeout=20.0, what="condition"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


# ------------------------------------------------------------------ the lanes themselves
def test_a_lane_gives_itself_to_one_holder_and_queues_the_rest_in_order():
    lane, started = Lane("sweep"), []
    t1 = lane.request("a", "first", lambda t: started.append("first"))
    t2 = lane.request("b", "second", lambda t: started.append("second"))
    t3 = lane.request("c", "third", lambda t: started.append("third"))
    assert started == ["first"] and t1.state == "running" and t2.state == "queued" and t3.state == "queued"
    assert (lane.position(t2), lane.position(t3)) == (1, 2)
    snap = lane.snapshot()
    assert snap["busy"] and snap["holder"]["owner"] == "a" and [q["position"] for q in snap["queue"]] == [1, 2]
    lane.release(t1)
    wait_for(lambda: started == ["first", "second"], what="the second to start")
    assert t2.state == "running" and lane.position(t3) == 1
    lane.release(t2)
    wait_for(lambda: started == ["first", "second", "third"])
    lane.release(t3)
    assert not lane.snapshot()["busy"] and lane.snapshot()["queue"] == []


def test_a_queued_request_can_be_withdrawn_and_the_running_one_can_be_cancelled():
    lane, started, cancelled = Lane("plan"), [], []
    t1 = lane.request("a", "one", lambda t: started.append(1), on_cancel=lambda t: cancelled.append(t.id))
    t2 = lane.request("b", "two", lambda t: started.append(2))
    assert lane.cancel(t2.id) is True and t2.state == "cancelled" and lane.snapshot()["queue"] == []
    assert lane.cancel(t1.id) is True and cancelled == [t1.id]              # stop on the holder asks it to stop; it releases itself when it has
    assert lane.cancel("nope") is False
    lane.release(t1)
    assert started == [1] and not lane.snapshot()["busy"]


def test_a_user_watch_replaces_the_running_one_but_an_auto_run_never_does():
    lane, started, preempted = Lane("live", queueing=False), [], []
    first = lane.request("a", "calm", lambda t: started.append("calm"), auto=True, on_preempt=lambda old: preempted.append(old.label))      # what to do when THIS one is replaced
    assert first is not None and lane.holder is first
    assert lane.request("b", "auto", lambda t: started.append("auto2"), auto=True) is None            # never waits, never replaces
    mine = lane.request("c", "watch", lambda t: started.append("mine"), preempt=True)
    assert mine is not None and lane.holder is mine and preempted == ["calm"] and first.state == "cancelled" and started == ["calm", "mine"]
    assert lane.request("d", "plain", lambda t: started.append("x")) is None                          # the live lane has no queue
    assert lane.snapshot()["queue"] == []


def test_a_run_the_lab_tab_started_blocks_an_auto_run_and_is_replaced_by_a_user_one():
    class Lab:
        running = True
    res = Resources(runner=lambda: Lab())
    assert res.live_external() is True and res.snapshot()["live"]["busy"] and res.snapshot()["live"]["external"]
    assert res.request_live("a", "auto", lambda t: None, auto=True) is None
    started = []
    t = res.request_live("a", "mine", lambda tk: started.append(1), auto=False)
    assert t is not None and started == [1]
    assert res.live_external() is False                                    # now it is ours, not the Lab's


def test_the_three_lanes_are_independent():
    res = Resources(runner=lambda: None)
    a = res.plan.request("x", "plan", lambda t: None)
    b = res.sweep.request("x", "sweep", lambda t: None)
    c = res.live.request("x", "live", lambda t: None)
    assert a.state == b.state == c.state == "running"
    snap = res.snapshot()
    assert snap["plan"]["busy"] and snap["sweep"]["busy"] and snap["live"]["busy"] and snap["live"]["external"] is False


# ------------------------------------------------------------------ in the engine: watching
def test_in_the_engine_a_user_watch_replaces_a_running_one_and_the_old_run_says_so(tmp_path, monkeypatch):
    runner = FakeRunner(finish_after=60)
    eng = make_engine(tmp_path, monkeypatch, runner=runner)
    try:
        chat = eng.new_chat()
        say(eng, chat, BOAT, hold_runs=True)
        runs = lambda: card(chat, "watch")["runs"]  # noqa: E731
        assert [r["state"] for r in runs()] == ["running"] and runs()[0]["source"] == "auto"
        say(eng, chat, "watch the worst waves", hold_runs=True)
        wait_for(lambda: len(runs()) == 2 and runs()[0]["state"] == "replaced", what="the first run to be replaced")
        assert runs()[0]["note"] == "Replaced by a newer run." and runs()[1]["state"] == "running" and runs()[1]["source"] == "user"
        assert len(runner.starts) == 2
        say(eng, chat, "stop", hold_runs=True)
        wait_for(lambda: runs()[1]["state"] == "stopped")
        assert runner.stops >= 1
        wait_for(lambda: eng.resources.snapshot()["live"]["holder"] is None, what="the live lane to be freed")
    finally:
        eng.close()


def test_the_calm_run_a_chat_starts_by_itself_never_takes_the_view_from_anyone(tmp_path, monkeypatch):
    runner = FakeRunner(finish_after=60)
    eng = make_engine(tmp_path, monkeypatch, runner=runner)
    try:
        one, two = eng.new_chat(), eng.new_chat()
        say(eng, one, BOAT, hold_runs=True)
        assert len(runner.starts) == 1
        say(eng, two, PIPE, hold_runs=True)
        assert len(runner.starts) == 1                                       # chat two's own calm run waited for nothing and took nothing
        w = card(two, "watch")
        assert w["runs"] == [] and w["state"] == "idle" and "busy" in w["summary"].lower()
        assert card(one, "watch")["runs"][0]["state"] == "running"
        say(eng, two, "watch it", hold_runs=True)                            # asked for: it does replace, and chat one's run says so
        wait_for(lambda: card(one, "watch")["runs"][0]["state"] == "replaced")
        assert len(runner.starts) == 2
    finally:
        eng.close()


def test_a_run_the_lab_tab_started_blocks_the_auto_run_in_the_engine(tmp_path, monkeypatch):
    runner = FakeRunner(finish_after=60)
    runner.running = True                                                   # someone started it from the Lab tab
    eng = make_engine(tmp_path, monkeypatch, runner=runner)
    try:
        chat = eng.new_chat()
        say(eng, chat, BOAT, hold_runs=True)
        assert runner.starts == [] and card(chat, "watch")["runs"] == []
        assert chat.view.dock["busy"]["live"] is False
        assert eng.resources.snapshot()["live"]["external"] is True
    finally:
        eng.close()


# ------------------------------------------------------------------ in the engine: the stress test lane
def test_a_second_test_run_waits_as_an_up_next_chip_and_starts_when_the_first_ends(tmp_path, monkeypatch):
    gate = threading.Event()
    fake = SweepFake(monkeypatch, gate=gate)
    eng = make_engine(tmp_path, monkeypatch)
    try:
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        eng.send(chat, text="run all")
        assert fake.started.wait(30)
        eng.send(chat, text="test waves")
        wait_for(lambda: any(c["type"] == "blocked" and c["state"] == "waiting" for c in chat.view.cards.values()), what="the Up next card")
        wait_for(lambda: chat.view.cards.get("results-2", {}).get("state") == "queued", what="the queued results card")
        waiting = next(c for c in chat.view.cards.values() if c["type"] == "blocked" and c["state"] == "waiting")
        assert waiting["ticket"]["position"] == 1 and waiting["ticket"]["cancel"]["type"] == "cancel_queued" and "next in line" in waiting["reason"]
        assert chat.view.dock["queue"][0]["position"] == 1 and chat.view.dock["queue"][0]["cancel"]["type"] == "cancel_queued"
        assert any(e["type"] == "job" and e["job"]["state"] == "queued" for e in chat.events)
        gate.set()
        assert chat.wait_idle(120)
        assert card(chat, "results-1")["state"] == "done" and card(chat, "results-2")["state"] == "done"
        assert chat.view.dock["queue"] == [] and not eng.resources.snapshot()["sweep"]["busy"]
    finally:
        gate.set()
        chat.wait_idle(60)
        eng.close()


def test_withdrawing_a_queued_run_removes_it_from_the_line(tmp_path, monkeypatch):
    gate = threading.Event()
    fake = SweepFake(monkeypatch, gate=gate)
    eng = make_engine(tmp_path, monkeypatch)
    try:
        chat = eng.new_chat()
        say(eng, chat, BOAT)
        eng.send(chat, text="run all")
        assert fake.started.wait(30)
        eng.send(chat, text="test waves")
        wait_for(lambda: chat.view.dock["queue"], what="a queued run")
        ticket = chat.view.dock["queue"][0]["cancel"]["args"]["ticket"]
        say_events = eng.send(chat, action={"type": "cancel_queued", "args": {"ticket": ticket}})
        wait_for(lambda: not chat.view.dock["queue"] and not eng.resources.sweep.snapshot()["queue"], what="the queue to empty")
        assert not any(c["type"] == "blocked" and c["state"] == "waiting" for c in chat.view.cards.values())
        assert card(chat, "results-2")["state"] == "cancelled" and "before it started" in card(chat, "results-2")["summary"]
        assert any(e["type"] == "job" and e["job"]["state"] == "cancelled" for e in chat.events)
        assert chat.s["stress"]["state"] == "running" or chat.s["stress"]["state"] == "cancelled"
        gate.set()
        assert chat.wait_idle(120)
        assert card(chat, "results-1")["state"] == "done"
    finally:
        gate.set()
        chat.wait_idle(60)
        eng.close()


def test_two_chats_share_the_sweep_lane_and_the_second_waits(tmp_path, monkeypatch):
    gate = threading.Event()
    fake = SweepFake(monkeypatch, gate=gate)
    eng = make_engine(tmp_path, monkeypatch)
    try:
        one, two = eng.new_chat(), eng.new_chat()
        say(eng, one, BOAT)
        say(eng, two, BOAT)
        eng.send(one, text="test waves")
        assert fake.started.wait(30)
        eng.send(two, text="test waves")
        wait_for(lambda: two.view.dock["queue"], what="chat two to be queued")
        assert eng.resources.sweep.snapshot()["holder"]["owner"] == one.id and eng.resources.sweep.snapshot()["queue"][0]["owner"] == two.id
        gate.set()
        assert one.wait_idle(120) and two.wait_idle(120)
        assert card(one, "results-1")["state"] == "done" and card(two, "results-1")["state"] == "done"
    finally:
        gate.set()
        eng.close()


# ------------------------------------------------------------------ in the engine: the AI plan lane
def test_two_chats_compete_for_the_plan_lane_and_the_second_is_queued(tmp_path, monkeypatch):
    fake = FakeOpenRouter()
    try:
        script_plan(fake, boat_plan(), delay=0.25)
        script_plan(fake, boat_plan(), delay=0.0)
        eng = make_engine(tmp_path, monkeypatch, fake=fake, key=True)
        try:
            one, two = eng.new_chat(), eng.new_chat()
            eng.send(one, text=BOAT)
            wait_for(lambda: eng.resources.plan.snapshot()["busy"], what="the first plan to start")
            eng.send(two, text=BOAT)
            wait_for(lambda: two.view.cards.get("improve", {}).get("state") == "queued", what="chat two to be queued")
            wait_for(lambda: any("next in line" in m["text"] for m in two.view.messages if m["role"] == "assistant") and two.view.dock.get("queue"), what="the queue message and dock")
            assert two.view.dock["queue"][0]["position"] == 1 and eng.resources.plan.snapshot()["queue"][0]["owner"] == two.id
            assert one.wait_idle(120) and two.wait_idle(120)
            assert len(fake.chat_requests()) == 2                            # one request each, never two at once
            assert card(one, "improve")["state"] in ("done", "kept") and card(two, "improve")["state"] in ("done", "kept")
        finally:
            eng.close()
    finally:
        fake.stop()
