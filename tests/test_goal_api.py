"""The Goal tab's HTTP API: matching, proposing, the build sheet and the background stress test."""
import time

import pytest

pytest.importorskip("mujoco")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import importlib  # noqa: E402

gr = importlib.import_module("simlab.dashboard.goal_router")        # the package re-exports the router under this name
router = gr.router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inventory.yaml"), ("SIMLAB_GOALS", "goals"), ("SIMLAB_BUILDS", "builds")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    real_write = gr.write_report
    monkeypatch.setattr(gr, "write_report", lambda *a, **k: real_write(*a, root=tmp_path / "runs" / "reports", **k))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def test_goals_and_kits_are_listed(client):
    r = client.get("/api/goal/goals").json()
    assert {g["id"] for g in r["goals"]} >= {"sewer_inspect", "water_rescue", "air_search"} and r["kits"][0]["id"] == "my_rover"
    assert all(len(g["questions"]) >= 2 and g["card"]["title"] for g in r["goals"])


def test_matching_never_errors_and_unknown_text_matches_nothing(client):
    assert client.post("/api/goal/match", json={"text": "find cracks in a sewer"}).json()["matches"][0]["goal"] == "sewer_inspect"
    assert client.post("/api/goal/match", json={"text": "make me a sandwich"}).json()["matches"] == []
    assert client.post("/api/goal/match", json={}).json()["matches"] == []


def test_propose_uses_the_kit_and_refuses_unknown_goals_and_kits(client):
    r = client.post("/api/goal/propose", json={"goal": "sewer_inspect", "answers": {"pipe": 2}, "kit": "my_rover"})
    assert r.status_code == 200
    body = r.json()
    best = body["proposals"][0]
    assert "tt_motor_6v" in best["owned"] and body["owned_used"]["tt_motor_6v"] == 2
    assert client.post("/api/goal/propose", json={"goal": "nope"}).status_code == 404
    assert client.post("/api/goal/propose", json={"goal": "sewer_inspect", "kit": "nope"}).status_code == 404
    bad = client.post("/api/goal/propose", json={"goal": "sewer_inspect", "answers": {"pipe": 99, "x": 1}})
    assert bad.status_code == 200                                              # bad answers fall back to the defaults


def test_the_build_sheet_has_wiring_for_the_rover_and_an_honest_note_for_the_rest(client, tmp_path):
    rover = client.post("/api/goal/propose", json={"goal": "sewer_inspect", "kit": "my_rover"}).json()["proposals"][0]["build"]
    r = client.post("/api/goal/sheet", json={"goal": "sewer_inspect", "build": rover, "owned": {"tt_motor_6v": 2}}).json()
    assert r["sheet"]["wiring"]["generated"] and any("GPIO26" in str(t) for t in r["sheet"]["wiring"]["tables"])
    assert (tmp_path / r["url"].replace("/reports/", "runs/reports/")).is_file()
    assert any(p["have"] for p in r["sheet"]["parts"]) and r["sheet"]["safety"]
    boat = client.post("/api/goal/propose", json={"goal": "water_rescue"}).json()["proposals"][0]["build"]
    r2 = client.post("/api/goal/sheet", json={"goal": "water_rescue", "build": boat}).json()
    assert not r2["sheet"]["wiring"]["generated"] and "datasheets" in r2["sheet"]["wiring"]["reason"]
    assert client.post("/api/goal/sheet", json={"goal": "water_rescue", "build": {"name": "../x"}}).status_code == 422


def test_a_stress_test_runs_in_the_background_reports_progress_and_can_be_stopped(client, monkeypatch):
    tiny = {"id": "sewer", "platform": "ground", "task": "pipe_short", "task_params": {}, "threshold": 0.8, "baseline": "test",
            "hazards": [{"id": "grip", "title": "Slippery floor", "plain": "p", "unit": "grip", "knob": "grip_mu", "levels": [0.8, 0.3], "source": "s", "fix": "f"}],
            "faults": [], "not_tested": ["a thing"]}
    monkeypatch.setattr(gr, "load_catalogue", lambda name: tiny)
    build = client.post("/api/goal/propose", json={"goal": "sewer_inspect", "kit": "my_rover"}).json()["proposals"][0]["build"]
    jid = client.post("/api/goal/sweep/start", json={"goal": "sewer_inspect", "build": build, "episodes": 1}).json()["id"]
    assert client.post("/api/goal/sweep/start", json={"goal": "sewer_inspect", "build": build}).status_code in (409, 200)    # one at a time (or already finished)
    t0 = time.time()
    while time.time() - t0 < 60:
        j = client.get("/api/goal/sweep/status", params={"id": jid}).json()
        if j["state"] != "running":
            break
        time.sleep(0.3)
    assert j["state"] == "done", j
    assert j["result"]["not_tested"] == ["a thing"] and j["report"].startswith("/reports/") and j["done"] == j["total"] == 2
    assert client.get("/api/goal/sweep/status", params={"id": "zzz"}).status_code == 404
    assert client.post("/api/goal/sweep/stop", params={"id": "zzz"}).status_code == 404
    bad = client.post("/api/goal/sweep/start", json={"goal": "sewer_inspect", "build": {"name": "x"}, "episodes": 1}).json()["id"]
    time.sleep(2.0)
    assert client.get("/api/goal/sweep/status", params={"id": bad}).json()["state"] == "error"                          # shown, not lost in the thread
