"""finish_from_plan is the part of run_plan that needs no model. The chat re-finishes a plan with it after an edit (zero requests); run_plan calls it too and must
behave exactly as before: same result, same number of requests, same 'questions' state."""
import copy
import json

import pytest

pytest.importorskip("mujoco")
from simlab.llm import OpenRouter  # noqa: E402
from simlab.llm.planner import MAX_CALLS, finish_from_plan, run_plan  # noqa: E402
from simlab.llm.schema import validate_plan  # noqa: E402
from simlab.parts.templates import example_builds  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402

KEY = "sk-or-v1-" + "z9y8x7w6" * 6
BOAT = example_builds()["small_boat"]
SAME = ("goal", "params", "plan", "raw_plan", "watch", "proposals", "scanned", "meets_goal", "note")


@pytest.fixture()
def fake():
    f = FakeOpenRouter()
    yield f
    f.stop()


@pytest.fixture(autouse=True)
def private_settings(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_LLM", "llm.json"), ("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inv.yaml"), ("SIMLAB_GOALS", "goals")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def client(fake):
    return OpenRouter(key=KEY, base_url=fake.url, sleep=lambda s: None)


def plan(**over):
    p = {"understanding": {"summary": "Flood rescue boat", "platform": "water", "why": "a person in the water", "assumptions": ["daylight"]},
         "params": {"range_m": 300, "minutes": 60, "wave_hs": 0.3, "wind_ms": 8}, "questions": [],
         "scenarios": [{"name": "Waves", "why": "swamping", "hazard": "waves"}, {"name": "Rain on the camera", "why": "blind", "hazard": None}],
         "requirements": [{"fact": "runtime", "op": ">=", "value": 60, "text": "an hour out", "source": "user"}],
         "task": {"preset": "water_rescue", "params": {"range_m": 100}},
         "stress": {"hazards": [{"id": "waves", "levels": [0, 0.3, 0.6], "ask": 0.3}], "faults": ["thruster_out"]},
         "designs": [{"label": "Small boat", "why": "light and cheap", "build": copy.deepcopy(BOAT)}]}
    p.update(over)
    return p


def reply(obj, **kw):
    return {"chunks": [json.dumps(obj)], **kw}


def weak_plan():
    weak = copy.deepcopy(BOAT)
    weak["chassis"]["hull"] = "hull_box_3m"                       # a tall hull with one small thruster is blown away by 8 m/s wind
    return plan(designs=[{"label": "Big hull", "why": "stable", "build": weak}])


def test_finishing_a_plan_gives_exactly_what_run_plan_gave(fake):
    fake.script.append(reply(plan()))
    r = run_plan("A boat to rescue people in a flood", {}, {}, client=client(fake))
    assert r["state"] == "done" and r["calls"] == 1
    n = len(fake.chat_requests())
    f = finish_from_plan(copy.deepcopy(r["raw_plan"]), {})
    for k in SAME:
        assert f[k] == r[k], k
    assert len(fake.chat_requests()) == n                         # finishing makes no request
    assert f["proposals"] and f["goal"]["id"] == "plan" and f["watch"]


def test_finishing_is_repeatable_and_does_not_change_the_plan_it_is_given(fake):
    raw = validate_plan(plan())
    before = copy.deepcopy(raw)
    a, b = finish_from_plan(raw, {}), finish_from_plan(raw, {})
    assert raw == before and a["proposals"] == b["proposals"] and a["meets_goal"] == b["meets_goal"]


def test_a_changed_job_number_re_finishes_locally_with_zero_requests(fake):
    raw = validate_plan(plan())
    raw["params"] = {**raw["params"], "wave_hs": 0.9, "range_m": 500}
    f = finish_from_plan(raw, {})
    assert f["params"]["wave_hs"] == 0.9 and f["proposals"] and fake.chat_requests() == []
    assert any("0.9" in c["text"] for c in f["proposals"][0]["checks"])             # the requirement text follows the new number


def test_without_a_revise_function_a_failing_design_is_never_sent_back(fake):
    raw = validate_plan(weak_plan())
    f = finish_from_plan(raw, {})
    assert fake.chat_requests() == [] and f["proposals"]                          # the library search finds a verified machine instead
    asked = []
    f2 = finish_from_plan(copy.deepcopy(raw), {}, revise=lambda problems: asked.append(problems) or None)
    assert len(asked) == 1 and any("wind" in p for p in asked[0]) and f2["proposals"] == f["proposals"]
    better = validate_plan(plan())["designs"]
    f3 = finish_from_plan(copy.deepcopy(raw), {}, revise=lambda problems: better)
    assert any(p["label"].startswith("Small boat") for p in f3["proposals"])


def test_a_good_design_is_not_revised_even_when_a_revise_function_is_given():
    raw = validate_plan(plan())
    asked = []
    finish_from_plan(raw, {}, revise=lambda problems: asked.append(problems))
    assert asked == []


def test_run_plan_makes_the_same_requests_as_before_for_a_failing_design(fake):
    fake.script += [reply(weak_plan()), reply({"designs": [{"label": "Small hull", "why": "less wind", "build": BOAT}]})]
    r = run_plan("a rescue boat", {}, {}, client=client(fake))
    assert r["calls"] == 2 and len(fake.chat_requests()) == 2 <= MAX_CALLS and any(p["label"] == "Small hull (AI)" for p in r["proposals"])
    again = finish_from_plan(copy.deepcopy(r["raw_plan"]), {}, revise=lambda problems: None)
    assert [p["label"] for p in again["proposals"]]                                  # the same raw plan finishes without the model


def test_a_bad_revision_changes_nothing_and_the_search_still_finds_a_machine(fake):
    fake.script += [reply(weak_plan()), {"chunks": ["not json at all"]}]
    r = run_plan("a rescue boat", {}, {}, client=client(fake))
    assert r["calls"] == 2 and r["state"] == "done" and r["proposals"] and r["scanned"] > 0


def test_questions_are_still_a_state_for_run_plan_and_final_never_stops_at_one(fake):
    ask = plan(questions=[{"id": "q1", "text": "How wet?", "kind": "choice", "choices": ["dry", "wet"]}])
    fake.script.append(reply(ask))
    r = run_plan("a rescue boat", {}, {}, client=client(fake))
    assert r["state"] == "questions" and r["calls"] == 1 and "proposals" not in r
    fake.script.append(reply(ask))
    r2 = run_plan("a rescue boat", {}, {}, client=client(fake), final=True)
    assert r2["state"] == "done" and r2["proposals"] and r2["calls"] == 1


def test_the_offline_and_unsupported_paths_never_reach_finish(fake):
    r = run_plan("inspect a sewer pipe for cracks", {}, {}, client=OpenRouter(base_url=fake.url))
    assert r["source"] == "offline" and fake.chat_requests() == []
    fake.script.append(reply({"understanding": {"summary": "x", "platform": "unsupported", "why": "needs a submarine"}}))
    u = run_plan("map a cave", {}, {}, client=client(fake))
    assert u["state"] == "unsupported" and u["proposals"] == []
