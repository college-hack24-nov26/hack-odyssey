"""Goal -> machine: the catalogue, the sentence matcher, the designer and the plain-language verdict."""
import json
import time

import pytest

pytest.importorskip("mujoco")
from simlab.goals import all_goals, get_goal, match, propose  # noqa: E402
from simlab.goals.plain import finding_line, verdict  # noqa: E402
from simlab.parts import evaluate  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inventory.yaml"), ("SIMLAB_GOALS", "goals")):
        monkeypatch.setenv(var, str(tmp_path / sub))


def test_the_three_goals_are_loaded_and_their_questions_are_plain():
    goals = all_goals()
    assert {"sewer_inspect", "water_rescue", "air_search"} <= set(goals)
    for g in goals.values():
        assert g.platform in ("ground", "air", "water") and 1 <= len(g.questions) <= 4 and g.keywords
        for q in g.questions:
            assert q.text.endswith("?") and len(q.choices) >= 2 and 0 <= q.default < len(q.choices)


def test_bad_answers_fall_back_to_the_defaults_and_a_broken_goal_file_is_skipped(tmp_path):
    g = get_goal("sewer_inspect")
    assert g.params({"pipe": 99, "distance": "far", "nope": 1}) == g.params({})
    assert g.params({"pipe": 2})["pipe_d"] == 0.9
    (tmp_path / "goals").mkdir()
    (tmp_path / "goals" / "broken.yaml").write_text("id: [unclosed", encoding="utf-8")
    (tmp_path / "goals" / "five.yaml").write_text("id: x\ntitle: x\nplatform: ground\ntask: pipe_short\nquestions: " + json.dumps(
        [{"id": str(i), "text": "q?", "choices": [{"label": "a"}]} for i in range(5)]), encoding="utf-8")
    assert {"sewer_inspect", "water_rescue", "air_search"} <= set(all_goals()) and "x" not in all_goals()


@pytest.mark.parametrize("text,goal", [
    ("find cracks in a sewer pipe", "sewer_inspect"), ("I need to inspect drains for leaks", "sewer_inspect"),
    ("a robot for checking a culvert", "sewer_inspect"), ("a boat for rescue operations", "water_rescue"),
    ("save a swimmer in a lake", "water_rescue"), ("flood rescue vessel", "water_rescue"),
    ("a drone that watches an area", "air_search"), ("quadcopter for aerial surveillance", "air_search"),
    ("UAV to patrol and observe", "air_search"), ("SEWER PIPES!!!", "sewer_inspect")])
def test_sentences_find_their_goal(text, goal):
    m = match(text)
    assert m and m[0]["goal"] == goal and m[0]["matched"]


def test_nonsense_matches_nothing_so_the_user_is_asked():
    assert match("make me a sandwich") == [] and match("") == [] and match("123 !!!") == [] and match(None) == []


def test_every_goal_gets_checked_proposals_quickly():
    for gid in ("sewer_inspect", "water_rescue", "air_search"):
        t0 = time.time()
        r = propose(gid, owned={})
        assert time.time() - t0 < 30 and r["scanned"] > 5 and r["proposals"]
        json.dumps(r)                                                       # everything the screen needs is plain data
        labels = [p["label"] for p in r["proposals"]]
        assert len(labels) == len(set(labels))
        for p in r["proposals"]:
            ev = evaluate(p["build"], get_goal(gid).task, suggest=False)
            assert ev["ok"], (gid, ev["findings"])                          # a proposal is never a build the Builder would refuse
            assert p["verdict"]["light"] in ("green", "amber", "red") and p["verdict"]["headline"]
            assert set(p["to_buy"]) | set(p["owned"]) == set(p["parts"])
            if r["meets_goal"]:
                assert not p["unmet"] and all(c["ok"] for c in p["checks"])


def test_wider_pipes_and_longer_runs_change_the_machine_and_what_you_own_comes_first():
    small = propose("sewer_inspect", {"pipe": 0}, owned={})["proposals"][0]["build"]["chassis"]["width"]
    big = propose("sewer_inspect", {"pipe": 2}, owned={})["proposals"][0]["build"]["chassis"]["width"]
    assert small < big
    mine = {"tt_motor_6v": 2, "wheel_65mm_tt": 2}
    best = propose("sewer_inspect", {"pipe": 2}, owned=mine)["proposals"][0]
    assert "tt_motor_6v" in best["owned"] and best["build"]["drive"]["motor"] == "tt_motor_6v"
    assert "tt_motor_6v" not in best["to_buy"]


def test_when_the_library_cannot_do_it_the_answer_says_so_instead_of_pretending():
    r = propose("water_rescue", {"sea": 2, "time": 2, "carry": 1}, owned={})              # rough, three hours, a lifebuoy
    assert not r["meets_goal"] and r["note"] and len(r["proposals"]) == 1
    p = r["proposals"][0]
    assert p["verdict"]["light"] == "red" and p["unmet"] and any(not c["ok"] for c in p["checks"])
    air = propose("air_search", {"time": 2, "wind": 2, "carry": 1}, owned={})
    assert not air["meets_goal"] and air["proposals"][0]["verdict"]["light"] == "red"


def test_a_harder_wind_asks_for_more_thrust():
    def need(wind_idx):
        p = propose("air_search", {"wind": wind_idx, "time": 0}, owned={})["proposals"][0]
        return next(c for c in p["checks"] if c["id"] == "wind")["detail"]
    assert need(0) != need(2)


def test_the_verdict_is_a_traffic_light_with_reasons():
    ok = [{"id": "a", "text": "Does a", "ok": True, "detail": "fine"}]
    no = [{"id": "b", "text": "Does b", "ok": False, "detail": "not enough"}]
    warn = [{"severity": "warn", "title": "Careful.", "detail": "Something is marginal. More text."}]
    assert verdict(ok, [], None)["light"] == "green"
    v = verdict(ok, warn, None)
    assert v["light"] == "amber" and "Careful." in v["reasons"][0] and "More text" not in v["reasons"][0]
    assert verdict(no, [], None)["light"] == "red" and "not enough" in verdict(no, [], None)["reasons"][0]
    assert verdict(ok, [{"severity": "error", "title": "Broken", "detail": "x"}], None)["light"] == "red"
    assert finding_line({"title": "Same.", "detail": "Same."}) == "Same."
