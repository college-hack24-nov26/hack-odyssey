"""RL environment, evaluation, training runs, job manager and the dashboard's HTTP API."""
import json
import pickle
import threading
import time

import numpy as np
import pytest

pytest.importorskip("mujoco")
pytest.importorskip("stable_baselines3")

from simlab import get_task_spec, get_vehicle  # noqa: E402
from simlab.control import make_policy  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.envs.gym_env import SimEnv  # noqa: E402
from simlab.train.envs import EnvFactory  # noqa: E402
from simlab.train.evaluate import compare, evaluate, wilson  # noqa: E402
from simlab.train.jobs import JobManager  # noqa: E402

TINY = {"total_steps": 2048, "n_envs": 1, "eval_every": 1024, "eval_episodes": 2, "final_episodes": 3, "net_arch": [32, 32],
        "ppo": {"n_steps": 256, "batch_size": 64, "n_epochs": 2}}


# ---------------------------------------------------------------- gymnasium contract
@pytest.mark.parametrize("vehicle,task", [("mid_4wd", "goto_open"), ("quad_250", "waypoints_easy"), ("hex_450", "land_pad")])
def test_env_follows_the_gymnasium_contract(vehicle, task):
    from gymnasium.utils.env_checker import check_env
    env = SimEnv(vehicle, task, seed=1)
    check_env(env, skip_render_check=True)
    assert env.observation_space.shape == (env.sim.obs_dim,) and env.action_space.shape == (env.sim.space.dim,)
    env.close()


def test_env_episodes_are_reproducible_and_distinct():
    a, b = SimEnv("mid_4wd", "goto_open", seed=3), SimEnv("mid_4wd", "goto_open", seed=3)
    o1, _ = a.reset()
    o2, _ = b.reset()
    assert np.array_equal(o1, o2)
    a.step(a.action_space.sample()), b.step(b.action_space.sample())
    o3, _ = a.reset()
    assert not np.array_equal(o1, o3)                                  # the second episode is a different scenario
    o1b, _ = a.reset(seed=3)
    assert np.array_equal(o1, o1b)                                     # reseeding restarts the sequence
    a.close(), b.close()


def test_factory_pickles_for_worker_processes():
    import cloudpickle
    f = EnvFactory(get_vehicle("mid_4wd").to_dict(), get_task_spec("goto_open").to_dict(), "mujoco", None, 5)
    for dumps in (pickle.dumps, cloudpickle.dumps):
        env = pickle.loads(dumps(f))()
        assert env.reset()[0].shape == env.observation_space.shape
        env.close()


def test_curriculum_makes_the_task_easier_then_restores_it():
    env = SimEnv("mid_4wd", "goto_clutter", seed=1)
    def sample(level):
        env.set_difficulty(level)
        dists, placed = [], []
        for _ in range(20):
            env.reset()
            ep = env.sim.episode
            dists.append(np.hypot(ep.info["goal"][0] - ep.spawn[0], ep.info["goal"][1] - ep.spawn[1]))
            placed.append(sum(1 for k, p in ep.placements.items() if k.startswith("obstacle") and len(p) == 3))
        return max(dists), np.mean(placed)
    easy, full = sample(0.0), sample(1.0)
    t = get_task_spec("goto_clutter").params
    assert easy[1] == 0 and easy[0] <= t["goal_min_dist"] + 1e-6
    assert full[1] > 5 and full[0] > easy[0] + 2
    env.close()


def test_air_curriculum_shrinks_the_waypoint_area():
    env = SimEnv("quad_250", "waypoints_long", seed=1)
    def reach(level):
        env.set_difficulty(level)
        out = []
        for _ in range(15):
            env.reset()
            out.append(max(abs(w[0]) for w in env.sim.episode.info["wps"]))
        return max(out)
    assert reach(0.0) < 0.5 * reach(1.0)
    env.close()


# ---------------------------------------------------------------- evaluation
def test_wilson_interval_is_sane_at_the_edges():
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.32
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and 0.68 < lo < 0.75
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.42 and 0.58 < hi < 0.60
    assert wilson(0, 0) == (0.0, 1.0)


def test_evaluation_separates_a_working_policy_from_doing_nothing():
    sim = Sim(get_vehicle("mid_4wd"), get_task_spec("goto_empty"), seed=1)
    good = evaluate(sim, make_policy("baseline", sim), 6, seed0=500)
    idle = evaluate(sim, make_policy("idle", sim), 3, seed0=500)
    assert good["success_rate"] >= 5 / 6 and good["success_ci95"][0] > 0.4
    assert 0 < good["path_efficiency"] <= 1.0 + 1e-6 and good["mean_time_to_success"] > 0
    assert idle["success_rate"] == 0 and idle["timeouts"] == 3 and idle["mean_time_to_success"] is None
    assert len(good["per_episode"]) == 6 and {"seed", "success", "t", "return", "path"} <= set(good["per_episode"][0])
    again = evaluate(sim, make_policy("baseline", sim), 6, seed0=500)
    assert [e["t"] for e in again["per_episode"]] == [e["t"] for e in good["per_episode"]]      # identical scenarios, identical result
    sim.close()


def test_compare_runs_every_policy_on_the_same_scenarios():
    res = compare("quad_250", "waypoints_easy", {"base": "baseline", "idle": "idle"}, episodes=3, seed0=77)
    assert set(res) == {"base", "idle"} and res["base"]["success_rate"] >= 2 / 3 and res["idle"]["success_rate"] == 0
    assert all(a["seed"] == b["seed"] for a, b in zip(res["base"]["per_episode"], res["idle"]["per_episode"]))


# ---------------------------------------------------------------- training
def test_a_short_training_run_leaves_a_complete_reproducible_record(tmp_path):
    from simlab.control.policy import Model
    from simlab.train.rl import train
    out = train({"vehicle": "mid_4wd", "task": "goto_empty", "curriculum_fraction": 0.5, **TINY}, tmp_path / "run")
    files = {p.name for p in out.iterdir()}
    assert {"config.json", "progress.json", "metrics.jsonl", "model_best.zip", "model_latest.zip", "baseline.json", "final_eval.json"} <= files
    prog = json.loads((out / "progress.json").read_text())
    assert prog["status"] == "done" and prog["steps"] >= TINY["total_steps"]
    rows = [json.loads(l) for l in (out / "metrics.jsonl").read_text().splitlines()]
    kinds = {r["kind"] for r in rows}
    assert kinds == {"train", "eval"} and all(r["steps"] > 0 for r in rows)
    diff = [r["difficulty"] for r in rows if r["kind"] == "train"]
    assert diff == sorted(diff) and diff[-1] == 1.0                    # the curriculum ramped up and reached the full task
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["vehicle"]["name"] == "mid_4wd" and cfg["task"]["name"] == "goto_empty"      # specs are stored, not just names
    final = json.loads((out / "final_eval.json").read_text())
    assert set(final) == {"trained", "baseline"} and final["trained"]["episodes"] == TINY["final_episodes"]
    sim = Sim(get_vehicle("mid_4wd"), get_task_spec("goto_empty"), seed=1)
    sim.reset(seed=1)
    cmd = Model(str(out / "model_best.zip")).act(sim)
    assert cmd.shape == (2,) and np.all(np.abs(cmd) <= [sim.space.hi[0] + 1e-9, sim.space.hi[1] + 1e-9])
    sim.close()


def test_a_stop_request_ends_the_run_cleanly(tmp_path):
    from simlab.train.rl import train
    out = tmp_path / "stopme"
    cfg = {"vehicle": "mid_4wd", "task": "goto_open", "total_steps": 2_000_000, "n_envs": 1, "eval_every": 10 ** 9,
           "eval_episodes": 2, "final_episodes": 2, "ppo": {"n_steps": 256, "batch_size": 64, "n_epochs": 1}}
    threading.Timer(4.0, lambda: (out / "stop.flag").write_text("x")).start()
    t0 = time.time()
    train(cfg, out)
    prog = json.loads((out / "progress.json").read_text())
    assert prog["status"] == "stopped" and prog["steps"] < 2_000_000 and time.time() - t0 < 60
    assert (out / "model_best.zip").exists()


def test_training_reports_a_bad_task_instead_of_hanging(tmp_path):
    from simlab.train.rl import train
    with pytest.raises(Exception):
        train({"vehicle": "quad_250", "task": "goto_open", **TINY}, tmp_path / "bad")


# ---------------------------------------------------------------- job manager (a real subprocess)
def test_job_manager_runs_a_job_in_a_subprocess_and_reports_it(tmp_path):
    jm = JobManager(tmp_path)
    cfg = {"vehicle": get_vehicle("mid_4wd").to_dict(), "task": get_task_spec("goto_empty").to_dict(), "algo": "ppo", **TINY}
    run_id = jm.start(cfg)
    assert jm.status(run_id) in ("starting", "running")
    with pytest.raises(ValueError, match="stop the run"):
        jm.delete(run_id)
    t0 = time.time()
    while jm.status(run_id) in ("starting", "running", "evaluating") and time.time() - t0 < 120:
        time.sleep(0.5)
    assert jm.status(run_id) == "done", jm.details(run_id)["log"]
    d = jm.details(run_id)
    assert d["model"] and d["final_eval"]["trained"]["episodes"] == 3 and d["train"] and d["eval"] and d["baseline"]
    assert jm.list()[0]["id"] == run_id and jm.models()[0]["kind"] == "ground_wheeled"
    jm.delete(run_id)
    assert jm.list() == []


def test_job_manager_rejects_path_tricks_and_flags_dead_runs(tmp_path):
    jm = JobManager(tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    (tmp_path / "precious.txt").write_text("keep me")
    for bad in ("../x", "a/b", "", "..", ".", "C:\\Windows", ".hidden"):
        for op in (jm.details, jm.stop, jm.delete):
            with pytest.raises(KeyError):
                op(bad)
    assert (tmp_path / "runs").is_dir() and (tmp_path / "precious.txt").exists()      # nothing outside the runs folder was touched
    ghost = tmp_path / "runs" / "ghost"
    ghost.mkdir()
    (ghost / "progress.json").write_text(json.dumps({"status": "running", "steps": 5}))
    import os
    os.utime(ghost / "progress.json", (time.time() - 600, time.time() - 600))
    assert jm.status("ghost") == "crashed"                             # "running" but silent for minutes, no process known


# ---------------------------------------------------------------- HTTP API
@pytest.fixture
def client(tmp_path, monkeypatch):
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    router_mod = importlib.import_module("simlab.dashboard.router")      # (the package attribute 'router' is the APIRouter object)
    monkeypatch.setattr(router_mod, "jobs", JobManager(tmp_path))
    app = FastAPI()
    app.include_router(router_mod.router)
    with TestClient(app) as c:
        yield c
    router_mod.runner.stop()


def test_catalog_lists_vehicles_tasks_kinds_and_schemas(client):
    c = client.get("/api/lab/catalog").json()
    assert {"mid_4wd", "quad_250", "hex_450", "large_6wd"} <= set(c["vehicles"])
    assert c["vehicles"]["quad_250"]["ok"] and c["vehicles"]["quad_250"]["derived"] and "velocity" in c["vehicles"]["quad_250"]["spaces"]
    assert {"goto_open", "waypoints_easy", "land_pad"} <= set(c["tasks"]) and c["tasks"]["land_pad"]["kinds"] == ["multirotor"]
    assert c["kinds"]["multirotor"]["schema"][0]["name"] == "layout" and "camera" in c["sensors"] and c["models"] == []


def test_validate_endpoint_reports_problems_as_data(client):
    spec = get_vehicle("quad_250").to_dict()
    spec["params"]["thrust_max"] = 0.5
    r = client.post("/api/lab/validate", json={"vehicle": spec}).json()
    assert r["ok"] is False and any("hover" in i["msg"] for i in r["issues"]) and r["derived"][0]["key"] == "twr"
    spec["params"]["warp"] = 1
    r = client.post("/api/lab/validate", json={"vehicle": spec}).json()
    assert r["ok"] is False and r["spec"] is None and any(i["field"] == "params.warp" for i in r["issues"])


def test_train_endpoints_validate_and_handle_missing_runs(client):
    assert client.post("/api/train/start", json={"vehicle": "quad_250", "task": "goto_open"}).status_code == 422
    assert client.post("/api/train/start", json={"vehicle": "mid_4wd", "task": "goto_open", "algo": "dqn"}).status_code == 422
    assert client.post("/api/train/start", json={"vehicle": "mid_4wd", "task": "goto_open", "n_envs": 99}).status_code == 422
    assert client.post("/api/train/start", json={"vehicle": "nope", "task": "goto_open"}).status_code == 404
    assert client.get("/api/train/run/does_not_exist").status_code == 404
    assert client.post("/api/train/stop", json={"id": "nope"}).status_code == 404
    assert client.get("/api/train/runs").json() == {"runs": []}


def test_lab_start_rejects_a_mismatched_pair_and_stop_is_always_safe(client):
    r = client.post("/api/lab/start", json={"vehicle": "mid_4wd", "task": "waypoints_easy"})
    assert r.status_code == 422 and "flies multirotors" in r.json()["detail"]
    assert client.post("/api/lab/start", json={"vehicle": "mid_4wd", "task": "no_such_task"}).status_code == 404
    assert client.get("/api/lab/state").json()["status"] == "idle"
    assert client.post("/api/lab/stop").status_code == 200 and client.get("/api/lab/scene").status_code == 404
    ok = client.post("/api/lab/start", json={"vehicle": "mid_4wd", "task": "goto_empty", "speed": 0})
    assert ok.status_code == 200
    t0 = time.time()
    while client.get("/api/lab/state").json().get("status") != "running" and time.time() - t0 < 10:
        time.sleep(0.1)
    assert client.get("/api/lab/scene").json()["vehicle"] == "mid_4wd" and client.post("/api/lab/command", json={"u": {"v": 0.1}}).json() == {"policy": "manual"}
