"""The AI planner's HTTP API: the key box, the test button, the planning job and a stress test that uses the AI's plan."""
import copy
import importlib
import json
import time

import pytest

pytest.importorskip("mujoco")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from simlab.llm import OpenRouter  # noqa: E402
from simlab.parts.templates import example_builds  # noqa: E402
from tests.fake_openrouter import FakeOpenRouter  # noqa: E402

gr = importlib.import_module("simlab.dashboard.goal_router")
KEY = "sk-or-v1-" + "q1w2e3r4" * 6
ROVER = example_builds()["esp32_pipe_rover"]


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_LLM", "llm.json"), ("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inv.yaml"), ("SIMLAB_GOALS", "goals"), ("SIMLAB_BUILDS", "builds")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake = FakeOpenRouter()
    monkeypatch.setattr(gr, "make_client", lambda: OpenRouter(base_url=fake.url, sleep=lambda s: None))
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    real_write = gr.write_report
    monkeypatch.setattr(gr, "write_report", lambda *a, **k: real_write(*a, root=tmp_path / "runs" / "reports", **k))
    app = FastAPI()
    app.include_router(gr.router)
    with TestClient(app) as c:
        yield c, fake
    fake.stop()


def wait_for(c, path, jid, states=("done", "error", "questions", "unsupported", "stopped"), limit=60):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = c.get(path, params={"id": jid}).json()
        if j["state"] in states:
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def rover_plan(**over):
    p = {"understanding": {"summary": "Field rover", "platform": "ground", "why": "a rover", "assumptions": []}, "params": {"distance_m": 20},
         "questions": [], "scenarios": [{"name": "Mud", "why": "stuck", "hazard": "resistance"}, {"name": "Hail", "why": "damage", "hazard": None}],
         "requirements": [], "task": {"preset": "goto_open", "params": {}},
         "stress": {"hazards": [{"id": "grip", "levels": [0.8, 0.05], "ask": 0.2}], "faults": ["lost_commands"]},
         "designs": [{"label": "Pipe rover", "why": "known to work", "build": copy.deepcopy(ROVER)}]}
    p.update(over)
    return p


# ------------------------------------------------------------------ the key box
def test_the_key_is_saved_privately_and_never_comes_back(rig, tmp_path):
    c, fake = rig
    assert c.get("/api/goal/llm/status").json()["configured"] is False
    r = c.post("/api/goal/llm/settings", json={"api_key": KEY, "model": "thinkingmachines/inkling:free"})
    assert r.status_code == 200 and KEY not in r.text and r.json()["configured"] is True
    st = c.get("/api/goal/llm/status")
    assert KEY not in st.text and st.json()["usage"]["free_requests_left"] == 47
    assert (tmp_path / "llm.json").is_file()
    assert c.post("/api/goal/llm/settings", json={"api_key": "short"}).status_code == 422
    assert c.post("/api/goal/llm/settings", json={"model": "two words"}).status_code == 422
    assert c.post("/api/goal/llm/settings", json={"api_key": ""}).json()["configured"] is False


def test_the_test_button_makes_one_small_request_and_reports_the_model(rig):
    c, fake = rig
    c.post("/api/goal/llm/settings", json={"api_key": KEY})
    fake.script.append({"chunks": ["OK"], "model": "thinkingmachines/inkling:free"})
    r = c.post("/api/goal/llm/test").json()
    assert r["ok"] and r["reply"] == "OK" and r["model"] == "thinkingmachines/inkling:free" and len(fake.chat_requests()) == 1
    fake.script.append({"status": 401, "error": f"nope {KEY}"})
    bad = c.post("/api/goal/llm/test")
    assert bad.status_code == 502 and KEY not in bad.text


# ------------------------------------------------------------------ planning job
def test_a_plan_runs_in_the_background_and_reports_its_phase(rig):
    c, fake = rig
    c.post("/api/goal/llm/settings", json={"api_key": KEY})
    fake.script.append({"chunks": [json.dumps(rover_plan())[i:i + 40] for i in range(0, 4000, 40) if json.dumps(rover_plan())[i:i + 40]], "delay": 0.005})
    jid = c.post("/api/goal/plan/start", json={"text": "a rover for a muddy field", "kit": "my_rover"}).json()["id"]
    j = wait_for(c, "/api/goal/plan/status", jid)
    assert j["state"] == "done" and j["result"]["source"] == "ai" and j["result"]["proposals"] and j["result"]["plan_id"] and KEY not in json.dumps(j)
    assert j["chars"] > 100 and j["seconds"] >= 0 and "raw_plan" not in j["result"]
    assert [s["simulated"] for s in j["result"]["plan"]["scenarios"]] == [True, False]


def test_no_text_one_plan_at_a_time_and_stop(rig):
    c, fake = rig
    c.post("/api/goal/llm/settings", json={"api_key": KEY})
    assert c.post("/api/goal/plan/start", json={"text": "   "}).status_code == 422
    fake.script.append({"chunks": [str(i) for i in range(200)], "delay": 0.05})
    jid = c.post("/api/goal/plan/start", json={"text": "a boat"}).json()["id"]
    assert c.post("/api/goal/plan/start", json={"text": "another"}).status_code == 409
    time.sleep(0.5)
    c.post("/api/goal/plan/stop", params={"id": jid})
    assert wait_for(c, "/api/goal/plan/status", jid)["state"] == "stopped"
    assert c.get("/api/goal/plan/status", params={"id": "nope"}).status_code == 404


def test_without_a_key_the_plan_job_still_answers_from_the_offline_planner(rig):
    c, fake = rig
    jid = c.post("/api/goal/plan/start", json={"text": "inspect a sewer pipe for cracks"}).json()["id"]
    j = wait_for(c, "/api/goal/plan/status", jid)
    assert j["state"] == "done" and j["result"]["source"] == "offline" and j["result"]["proposals"] and fake.chat_requests() == []


# ------------------------------------------------------------------ a stress test that uses the AI's plan
def test_the_stress_test_runs_the_ais_plan_and_lists_what_it_could_not_test(rig):
    c, fake = rig
    c.post("/api/goal/llm/settings", json={"api_key": KEY})
    fake.script.append({"chunks": [json.dumps(rover_plan())]})
    res = wait_for(c, "/api/goal/plan/status", c.post("/api/goal/plan/start", json={"text": "a muddy field rover", "kit": "my_rover"}).json()["id"])["result"]
    build = res["proposals"][0]["build"]
    jid = c.post("/api/goal/sweep/start", json={"goal": "plan", "build": build, "plan_id": res["plan_id"], "episodes": 1}).json()["id"]
    j = wait_for(c, "/api/goal/sweep/status", jid)
    assert j["state"] == "done", j
    r = j["result"]
    assert r["task"] == "goto_open" and [h["id"] for h in r["hazards"]] == ["grip"] and r["hazards"][0]["asked"] == 0.2 and [f["id"] for f in r["faults"]] == ["lost_commands"]
    assert any("Hail" in t for t in r["not_tested"]) and "not a guarantee" in r["statement"]
    assert c.post("/api/goal/sweep/start", json={"goal": "plan", "build": build, "plan_id": "gone"}).status_code == 404
