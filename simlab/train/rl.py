"""Train a control policy with Stable-Baselines3 on any (vehicle, task, backend), with live metrics for the dashboard.

A run directory holds everything needed to reproduce and replay it:
    config.json      the full vehicle and task specs and every setting
    progress.json    status, steps, speed (rewritten every rollout; the dashboard polls it)
    metrics.jsonl    one line per rollout: training return / success, and per evaluation: deterministic success rate
    model_latest.zip, model_best.zip
    baseline.json, final_eval.json   trained policy vs the task's classical baseline on identical scenarios
    stop.flag        create it to stop the run cleanly
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from ..control.policy import Policy
from ..envs.core import Sim
from ..envs.gym_env import as_task, as_vehicle
from .envs import EnvFactory
from .evaluate import compare, evaluate

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs" / "train"

DEFAULTS = {
    "algo": "ppo", "total_steps": 200_000, "n_envs": 8, "seed": 0, "space": None, "backend": "mujoco", "device": "cpu",
    "net_arch": [128, 128], "eval_every": 20_000, "eval_episodes": 20, "final_episodes": 50,
    "curriculum_fraction": 0.0,           # ramp difficulty from easy to full over this fraction of the steps (0 = off)
    "ppo": {"n_steps": 512, "batch_size": 256, "n_epochs": 10, "learning_rate": 3e-4, "gamma": 0.99, "gae_lambda": 0.95,
            "ent_coef": 0.0, "clip_range": 0.2},
    "sac": {"learning_rate": 3e-4, "batch_size": 256, "buffer_size": 300_000, "learning_starts": 5_000, "gamma": 0.99},
}


def merge(cfg: dict) -> dict:
    out = {**DEFAULTS, **cfg}
    for k in ("ppo", "sac"):
        out[k] = {**DEFAULTS[k], **cfg.get(k, {})}
    return out


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, default=float), encoding="utf-8")
    os.replace(tmp, path)


class _LivePolicy(Policy):
    """Adapter so an in-training SB3 model can be evaluated like any other policy."""

    def __init__(self, model):
        self.model = model

    def act(self, sim):
        a, _ = self.model.predict(sim.vec, deterministic=True)
        return sim.space.denormalise(a)


def train(cfg: dict, out_dir: str | Path | None = None) -> Path:
    """Run a whole training job in this process; returns the run directory."""
    from stable_baselines3 import PPO, SAC
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv, VecMonitor
    import torch

    torch.set_num_threads(2)                       # the MLPs are tiny; leave the cores to the simulation workers

    cfg = merge(cfg)
    vehicle, task = as_vehicle(cfg["vehicle"]), as_task(cfg["task"])
    out = Path(out_dir) if out_dir else RUNS / time.strftime(f"%Y%m%d_%H%M%S_{vehicle.name}_{task.name}_{cfg['algo']}")
    out.mkdir(parents=True, exist_ok=True)
    full = {**cfg, "vehicle": vehicle.to_dict(), "task": task.to_dict()}
    _write(out / "config.json", full)
    state = {"status": "starting", "steps": 0, "total_steps": cfg["total_steps"], "fps": 0.0, "started": time.time(),
             "algo": cfg["algo"], "vehicle": vehicle.name, "task": task.name, "kind": vehicle.kind}
    _write(out / "progress.json", state)
    (out / "stop.flag").unlink(missing_ok=True)

    class Lab(BaseCallback):
        def __init__(self):
            super().__init__()
            self.t0, self.next_eval, self.best = time.time(), cfg["eval_every"], (-1.0, -1e18)
            self.last_report = 0.0
            self.sim_eval = None

        def _on_step(self) -> bool:
            return not (out / "stop.flag").exists()

        def _on_rollout_end(self) -> None:
            if time.time() - self.last_report >= 2.0:        # off-policy algorithms end a "rollout" every step: throttle
                self._report()

        def _report(self, final: bool = False) -> None:
            self.last_report = time.time()
            buf = self.model.ep_info_buffer
            steps = int(self.num_timesteps)
            elapsed = time.time() - self.t0
            row = {"steps": steps, "time": elapsed, "fps": steps / max(elapsed, 1e-9),
                   "train_return": float(np.mean([e["r"] for e in buf])) if buf else None,
                   "train_length": float(np.mean([e["l"] for e in buf])) if buf else None,
                   "train_success": float(np.mean([e.get("success", 0.0) for e in buf])) if buf else None}
            if cfg["curriculum_fraction"] > 0:
                level = min(1.0, steps / max(cfg["curriculum_fraction"] * cfg["total_steps"], 1.0))
                self.training_env.env_method("set_difficulty", level)
                row["difficulty"] = level
            with open(out / "metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "train", **row}, default=float) + "\n")
            state.update(status="running", steps=steps, fps=row["fps"], train_return=row["train_return"],
                         train_success=row["train_success"], best_eval=self.best[0] if self.best[0] >= 0 else None)
            _write(out / "progress.json", state)
            if steps >= self.next_eval and not final:
                self.next_eval += cfg["eval_every"]
                self._evaluate(steps)

        def _evaluate(self, steps: int) -> None:
            if self.sim_eval is None:
                self.sim_eval = Sim(vehicle, task, cfg["backend"], space=cfg["space"], seed=999)
            res = evaluate(self.sim_eval, _LivePolicy(self.model), cfg["eval_episodes"], seed0=50_000)
            row = {"kind": "eval", "steps": steps, "success": res["success_rate"], "success_ci95": res["success_ci95"],
                   "return": res["mean_return"], "time": res["mean_time_to_success"]}
            with open(out / "metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=float) + "\n")
            self.model.save(out / "model_latest")
            score = (res["success_rate"], res["mean_return"])
            if score > self.best:
                self.best = score
                self.model.save(out / "model_best")
            state.update(best_eval=self.best[0], last_eval=res["success_rate"])
            _write(out / "progress.json", state)

        def close(self):
            if self.sim_eval is not None:
                self.sim_eval.close()

    env = None
    cb = Lab()
    try:
        vfn = [EnvFactory(full["vehicle"], full["task"], cfg["backend"], cfg["space"], cfg["seed"] * 1000 + i) for i in range(cfg["n_envs"])]
        env = VecMonitor(SubprocVecEnv(vfn, start_method="spawn") if cfg["n_envs"] > 1 else DummyVecEnv(vfn),
                         info_keywords=("success",))
        env.seed(cfg["seed"])
        if cfg["curriculum_fraction"] > 0:
            env.env_method("set_difficulty", 0.0)
        algo = {"ppo": PPO, "sac": SAC}[cfg["algo"]]
        hp = dict(cfg[cfg["algo"]])
        if cfg["algo"] == "ppo" and "n_steps" in hp:
            hp["batch_size"] = min(hp["batch_size"], hp["n_steps"] * cfg["n_envs"])
        model = algo("MlpPolicy", env, seed=cfg["seed"], device=cfg["device"], verbose=0, policy_kwargs={"net_arch": cfg["net_arch"]},
                     tensorboard_log=str(out / "tb"), **hp)
        # the classical baseline on the same scenarios the evaluations use, so the dashboard can draw it as a reference line
        base = _baseline(vehicle, task, cfg)
        if base:
            _write(out / "baseline.json", {k: v for k, v in base.items() if k != "per_episode"})
        model.learn(cfg["total_steps"], callback=cb, progress_bar=False)
        cb._report(final=True)
        cb._evaluate(int(model.num_timesteps))
        model.save(out / "model_latest")
        stopped = (out / "stop.flag").exists()
        state.update(status="stopped" if stopped else "evaluating", steps=int(model.num_timesteps))
        _write(out / "progress.json", state)
        pol = {"trained": f"model:{out / 'model_best.zip'}"}
        if base:
            pol["baseline"] = "baseline"
        final = compare(vehicle, task, pol, cfg["final_episodes"], seed0=10_000, backend=cfg["backend"], space=cfg["space"])
        _write(out / "final_eval.json", {k: {kk: vv for kk, vv in v.items() if kk != "per_episode"} for k, v in final.items()})
        state.update(status="stopped" if stopped else "done", finished=time.time())
    except Exception as e:  # noqa: BLE001 - leave a readable record for the dashboard, then re-raise for the log
        state.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        _write(out / "progress.json", state)
        cb.close()
        if env is not None:
            env.close()
    return out


def _baseline(vehicle, task, cfg) -> dict | None:
    sim = Sim(vehicle, task, cfg["backend"], space=cfg["space"], seed=1)
    try:
        if sim.task.baseline() is None:
            return None
        from ..control.policy import make_policy
        return evaluate(sim, make_policy("baseline", sim), cfg["eval_episodes"], seed0=50_000)
    finally:
        sim.close()
