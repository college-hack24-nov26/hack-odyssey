"""Policies turn what the Sim sees into a command in the vehicle's command space (physical units)."""
from __future__ import annotations

import time

import numpy as np


class Policy:
    name = ""

    def reset(self, sim) -> None:
        pass

    def act(self, sim) -> np.ndarray:
        raise NotImplementedError


class Idle(Policy):
    name = "idle"

    def act(self, sim):
        return sim.space.zeros()


class Manual(Policy):
    """Holds the last command a human sent; stops 0.5 s after the last one (the same failsafe the real rover has)."""
    name = "manual"
    TIMEOUT = 0.5

    def __init__(self):
        self._cmd, self._at = None, 0.0

    def set(self, cmd) -> None:
        self._cmd, self._at = cmd, time.monotonic()

    def act(self, sim):
        if self._cmd is None or time.monotonic() - self._at > self.TIMEOUT:
            return sim.space.zeros()
        return sim.space.vector(self._cmd)


class Baseline(Policy):
    """The task's own classical controller."""
    name = "baseline"

    def __init__(self, sim):
        self.ctl = sim.task.baseline()
        if self.ctl is None:
            raise ValueError(f"task {sim.task.name!r} has no baseline controller")

    def reset(self, sim):
        if hasattr(self.ctl, "reset"):
            self.ctl.reset()

    def act(self, sim):
        return sim.space.clip(self.ctl(sim.obs, sim.episode, sim.space_name))


class Model(Policy):
    """A trained Stable-Baselines3 policy (a .zip saved by simlab.train)."""
    name = "model"

    def __init__(self, path: str):
        from stable_baselines3 import PPO, SAC
        self.model = None
        for algo in (PPO, SAC):
            try:
                self.model = algo.load(path, device="cpu")
                break
            except Exception:  # noqa: BLE001 - wrong algorithm class for this file; try the next one
                continue
        if self.model is None:
            raise ValueError(f"cannot load a PPO or SAC policy from {path}")

    def act(self, sim):
        a, _ = self.model.predict(sim.vec, deterministic=True)
        return sim.space.denormalise(a)


def make_policy(name: str, sim):
    """'idle', 'manual', 'baseline' or 'model:<path to .zip>'."""
    if name == "idle":
        return Idle()
    if name == "manual":
        return Manual()
    if name == "baseline":
        return Baseline(sim)
    if name.startswith("model:"):
        return Model(name.split(":", 1)[1])
    raise ValueError(f"unknown policy {name!r}; use idle, manual, baseline or model:<path>")
