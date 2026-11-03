"""Honesty is structural: no sentence of the chat says a design is safe, results always carry their limits, a drone only observes, and the chat cannot drive hardware."""
import json
import re

import pytest

pytest.importorskip("mujoco")

from simlab.chat import cards, words  # noqa: E402
from simlab.hazards.sweep import STATEMENT  # noqa: E402
from tests.chat_helpers import BOAT, DRONE, PIPE, SweepFake, card, make_engine, say, texts  # noqa: E402

BANNED = re.compile(r"\b(safe|safely|guarantee[ds]?|proven|certified)\b", re.I)
PASS = re.compile(r"\bpass(?:es|ed|ing)?\b", re.I)


def all_strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from all_strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from all_strings(v)


def check_words(strings, where):
    for s in strings:
        assert not BANNED.search(s), f"{where}: banned word in {s!r}"
        if PASS.search(s):
            assert "in simulation" in s.lower(), f"{where}: 'pass' without 'in simulation' in {s!r}"


# ------------------------------------------------------------------ the wording module
def test_no_template_uses_a_banned_word_and_pass_only_comes_with_in_simulation():
    rendered = words.render_all()
    assert len(rendered) > 150
    check_words(rendered, "words.py")


def test_every_template_has_sample_arguments_so_no_sentence_can_hide_from_the_test():
    assert words.untested_functions() == []
    unknown = [n for n in words.SAMPLES if not callable(getattr(words, n, None))]
    assert unknown == []                                          # a stale sample name would hide a renamed template


def test_the_code_owned_statement_is_the_only_text_with_the_word_guarantee_and_no_sentence_repeats_it():
    assert "not a guarantee" in STATEMENT
    assert not any("guarantee" in s.lower() for s in words.render_all())
    assert not any(STATEMENT in s for s in words.render_all())


def test_the_limit_line_names_the_simulation_the_autopilot_and_the_count():
    for n in (0, 1, 8):
        line = words.limit_line(n)
        assert "Simulation only" in line and words.AUTOPILOT in line and f"{n} thing" in line
    assert "not tested" in words.limit_line(3)


def test_results_always_read_met_the_bar_in_n_of_m_simulated_conditions():
    h = words.results_headline(14, 23, 3, 6, True)
    assert h.startswith("Met the bar in 14 of 23 simulated conditions with the simple built-in autopilot")
    assert "not run" in h
    msg = words.results_message(h, None, "75% of tries succeed", 8)
    assert "Simulation only" in msg and "8 things not tested" in msg and "Bar: 75%" in msg


# ------------------------------------------------------------------ what the engine says and shows
@pytest.fixture()
def boat(tmp_path, monkeypatch):
    SweepFake(monkeypatch)
    eng = make_engine(tmp_path, monkeypatch)
    chat = eng.new_chat()
    say(eng, chat, BOAT)
    yield eng, chat, tmp_path
    eng.close()


def test_a_whole_conversation_never_says_safe_and_every_result_shows_its_limits(boat):
    eng, chat, tmp = boat
    for t in ("make the waves bigger", "add rain", "10 tries", "undo", "run all", "why did it fail", "what can you not test", "build sheet", "what does this mean",
              "how long will it take", "hardware", "train a detector", "hello"):
        say(eng, chat, t)
    check_words([m["text"] for m in chat.view.messages if m["role"] == "assistant"], "messages")
    for c in chat.view.cards.values():
        check_words([c.get("summary", "")], c["id"])
        if c["type"] in ("limits", "answer", "blocked", "improve", "results"):
            check_words(all_strings({k: v for k, v in c.items() if k != "statement"}), c["id"])
    res = card(chat, "results-1")
    assert res["state"] == "done"
    assert res["statement"] == STATEMENT and res["limit_line"] == words.limit_line(len(res["not_tested"])) and res["not_tested"]
    assert "simulated conditions with the simple built-in autopilot" in res["summary"]
    last = [m["text"] for m in chat.view.messages if m["role"] == "assistant" and m["text"].startswith("Met the bar")][-1]
    assert words.AUTOPILOT in last and "not tested" in last                  # the chat message itself carries the limit line
    assert card(chat, "answer-1")["limit_line"].startswith("Simulation only")


def test_the_limits_card_and_the_strip_list_what_was_not_tested_including_what_the_person_asked_for(boat):
    eng, chat, _ = boat
    say(eng, chat, "add rain")
    assert chat.view.limits["count"] == len(chat.book.not_tested_items()) == len(eng.state(chat)["not_tested"]) and len(card(chat, "tests")["untestable"]) == 1
    say(eng, chat, "what can't you test")
    lim = card(chat, "limits")
    assert any("Rain" in i["text"] for i in lim["items"]) and lim["statement"] == STATEMENT and lim["count"] == chat.view.limits["count"]
    assert any(i["kind"] == "catalogue" for i in lim["items"])
    rain = next(c for c in card(chat, "tests")["rows"] if c["status"] == "untestable")
    assert rain["cells"][0]["state"] == "not_tested" and rain["untestable_reason"]


def test_the_report_and_the_state_carry_the_statement_and_the_not_tested_list(boat):
    eng, chat, tmp = boat
    say(eng, chat, "add fog")
    say(eng, chat, "run all")
    url = card(chat, "results-1")["report"]["url"]
    folder = tmp / "reports" / url.split("/")[2]
    data = json.loads((folder / "report.json").read_text(encoding="utf-8"))["result"]
    assert data["statement"] == STATEMENT and any("fog" in t.lower() for t in data["not_tested"]) and data["not_tested"]
    html = (folder / "report.html").read_text(encoding="utf-8")
    assert "not a guarantee" in html
    st = eng.state(chat)
    assert st["statement"] == STATEMENT and st["not_tested"] and "Simulation only" in st["limit_line"]


def test_a_stale_result_is_marked_when_the_tests_changed_after_it_ran(boat):
    eng, chat, _ = boat
    say(eng, chat, "test it")
    assert chat.view.limits["stale"] is False and card(chat, "results-1")["stale"] is False
    say(eng, chat, "make the waves bigger")
    assert card(chat, "results-1")["stale"] is True and chat.view.limits["stale"] is True
    say(eng, chat, "undo")
    assert card(chat, "results-1")["stale"] is False and chat.view.limits["stale"] is False


def test_the_parts_card_has_no_drive_arm_or_checklist_and_the_rover_link_is_disabled_for_a_boat(boat):
    eng, chat, _ = boat
    parts = card(chat, "parts")
    assert parts["rover"]["enabled"] is False and parts["rover"]["reason"] and parts["rover"]["leaves_chat"] is True
    assert parts["hardware_line"] == words.HARDWARE_LINE and parts["wiring"]["generated"] is False and parts["wiring"]["note"] == words.NO_WIRING
    for a in parts["actions"]:
        assert a["type"] in ("open_details", "sheet")
    forbidden = re.compile(r"\b(drive|arm|disarm|checklist|connect|flash|estop)\b", re.I)
    assert not any(forbidden.search(a["label"]) for a in parts["actions"])
    assert cards.rover_link(True)["enabled"] is True and cards.rover_link(True)["href"] == "/#hw"
    assert cards.rover_link(False)["enabled"] is False


def test_hardware_words_only_point_to_the_hardware_screen(boat):
    eng, chat, _ = boat
    runs = len(chat.s["runs"])
    ev = say(eng, chat, "drive it")
    assert words.blocked_hardware() in texts(ev)
    assert not any(e["type"] == "job" for e in ev) and len(chat.s["runs"]) == runs
    for kind in ("arm", "drive", "checklist", "estop"):
        n = chat.seq
        say(eng, chat, action={"type": kind, "args": {}})
        blocked = [e["card"] for e in chat.events[n:] if e["type"] == "card.create"]
        assert blocked and blocked[-1]["type"] == "blocked" and "Hardware screen" in blocked[-1]["reason"]


def test_a_drone_job_says_observation_only_everywhere_it_matters(tmp_path, monkeypatch):
    eng = make_engine(tmp_path, monkeypatch)
    try:
        chat = eng.new_chat()
        say(eng, chat, DRONE)
        u, d, p = card(chat, "understanding"), card(chat, "design"), card(chat, "parts")
        for c in (u, d, p):
            assert c["observation_only"] is True
        assert "observation only" in (u["observation_note"] or "").lower() and u["observation_note"] == words.OBSERVATION_ONLY
        assert d["observation_note"] == words.OBSERVATION_ONLY
        assert "Observation only" in words.drone_note()
        assert card(chat, "limits" if "limits" in chat.view.cards else "understanding")
        say(eng, chat, "what can you not test")
        assert card(chat, "limits")["observation_only"] is True
    finally:
        eng.close()


def test_a_ground_job_is_not_called_observation_only(tmp_path, monkeypatch):
    eng = make_engine(tmp_path, monkeypatch)
    try:
        chat = eng.new_chat()
        say(eng, chat, PIPE)
        assert card(chat, "understanding")["observation_only"] is False and card(chat, "understanding")["observation_note"] is None
        assert card(chat, "design")["what_this_means"] == words.ESTIMATE and "predictions" in words.ESTIMATE
    finally:
        eng.close()


def test_the_watch_card_never_calls_a_run_a_test(boat):
    eng, chat, _ = boat
    w = card(chat, "watch")
    assert "not a test" in w["demo_note"] and "similar run" in w["similar_note"].lower() and "not the exact failed try" in w["similar_note"]
    say(eng, chat, "watch the worst waves")
    run = card(chat, "watch")["runs"][-1]
    assert run["similar"] is True and "similar run" in run["label"].lower()
    say(eng, chat, "watch a thruster failure")
    assert any("cannot be watched" in c["reason"] for c in chat.view.cards.values() if c["type"] == "blocked")
