"""Evaluate policies on identical scenarios and report honest numbers (success rate with a confidence interval)."""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

from ..control.policy import Policy, make_policy
from ..envs.core import Sim


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval for a success rate k/n (well behaved at 0 % and 100 %, unlike the normal one)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def run_episode(sim: Sim, policy: Policy, seed: int) -> dict:
    sim.reset(seed=seed)
    policy.reset(sim)
    start = np.array(sim.obs.state.pos[:3])
    path, prev = 0.0, start.copy()
    while True:
        _, _, term, trunc, info = sim.step_physical(policy.act(sim))
        pos = np.array(sim.obs.state.pos[:3])
        path += float(np.linalg.norm(pos - prev))
        prev = pos
        if term or trunc:
            break
    goal = sim.episode.info.get("goal")
    if isinstance(goal, (str, bytes)):                       # a named node (pipe tasks), not a point: no straight-line distance
        goal = None
    # an episode ends on entering the goal circle, so the shortest possible path is to its edge, not its centre
    straight = max(float(np.linalg.norm(np.array(goal) - start[:2])) - sim.episode.info.get("goal_r", 0.0), 1e-6) if goal is not None else None
    return {"seed": seed, "success": bool(info.get("success")), "t": float(info["t"]), "return": float(info["ret"]),
            "collisions": int(info.get("collisions", 0)), "crash": info.get("crash"), "timeout": bool(trunc),
            "path": path, "straight": straight}


def summarise(eps: list[dict]) -> dict:
    n = len(eps)
    wins = [e for e in eps if e["success"]]
    lo, hi = wilson(len(wins), n)
    eff = [e["straight"] / e["path"] for e in wins if e["straight"] and e["path"] > 1e-6]
    crashes = {}
    for e in eps:
        if e["crash"]:
            crashes[e["crash"]] = crashes.get(e["crash"], 0) + 1
    return {"episodes": n, "success_rate": len(wins) / n if n else 0.0, "success_ci95": [lo, hi],
            "mean_time_to_success": float(np.mean([e["t"] for e in wins])) if wins else None,
            "mean_return": float(np.mean([e["return"] for e in eps])) if eps else 0.0,
            "contact_steps_per_episode": float(np.mean([e["collisions"] for e in eps])) if eps else 0.0,
            "timeouts": sum(e["timeout"] for e in eps), "crashes": crashes,
            "path_efficiency": float(np.mean(eff)) if eff else None}


def evaluate(sim: Sim, policy: Policy, episodes: int = 20, seed0: int = 10_000,
             progress: Callable[[int, int], None] | None = None) -> dict:
    """Run `episodes` episodes with seeds seed0, seed0+1, ...; the same seeds reproduce the same scenarios per policy."""
    eps = []
    for i in range(episodes):
        eps.append(run_episode(sim, policy, seed0 + i))
        if progress:
            progress(i + 1, episodes)
    return {**summarise(eps), "per_episode": eps}


def compare(vehicle, task, policies: dict[str, str], episodes: int = 50, seed0: int = 10_000, backend: str = "mujoco",
            space: str | None = None) -> dict:
    """{label: policy name ('baseline' | 'model:<path>' | 'idle')} -> {label: evaluation} on identical scenarios."""
    from ..envs.gym_env import as_task, as_vehicle
    sim = Sim(as_vehicle(vehicle), as_task(task), backend, space=space, seed=seed0)
    out = {}
    try:
        for label, name in policies.items():
            out[label] = evaluate(sim, make_policy(name, sim), episodes, seed0)
    finally:
        sim.close()
    return out
