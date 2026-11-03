"""Sim: one vehicle + one task + one backend, stepped the same way by the dashboard, baselines and RL training."""
from __future__ import annotations

import numpy as np

from ..registry import BACKENDS, TASKS
from ..spec.task import TaskSpec
from ..spec.vehicle import VehicleSpec


class Sim:
    def __init__(self, vehicle: VehicleSpec, task: TaskSpec, backend: str = "mujoco", *, space: str | None = None,
                 seed: int = 0, hazards: dict | None = None, **backend_opts):
        task_cls = TASKS.get(task.task)
        needed = task_cls(task, vehicle).required_sensors()              # a probe: tasks may need sensors the vehicle lacks
        self.vehicle = vehicle.with_sensors(needed) if needed else vehicle
        self.task = task_cls(task, self.vehicle)
        self.backend = BACKENDS.get(backend)(self.vehicle, self.task.build_world(),
                                             policy_hz=task.episode["policy_hz"], seed=seed, **backend_opts)
        self.hazards = dict(hazards or {})
        self.backend.hazards = self.hazards                 # backends that model hazards read it at reset (see hazards/)
        self.space_name = space or self.vehicle.default_space
        self.space = self.vehicle.space(self.space_name)
        self.rng = np.random.default_rng(seed)
        self.episode = self.obs = self.vec = None
        self.t, self.steps, self.ret = 0.0, 0, 0.0

    @property
    def obs_dim(self) -> int:
        return self.task.observation_dim()

    def set_hazards(self, hazards: dict | None) -> None:
        """Fixed, repeatable stress conditions (wind speed, wave height, a dead motor ...). Takes effect at the next reset."""
        self.hazards = dict(hazards or {})
        self.backend.hazards = self.hazards

    def reset(self, seed: int | None = None) -> np.ndarray:
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self.episode = self.task.reset(self.rng)
        self.obs = self.backend.reset(self.episode.spawn, self.episode.placements, self.task.spec.randomize, rng=self.rng)
        self.t, self.steps, self.ret = 0.0, 0, 0.0
        self.vec = self.task.observe(self.obs, self.episode)
        return self.vec

    def step(self, action) -> tuple[np.ndarray, float, bool, bool, dict]:
        """Advance one policy step with a normalised action in [-1, 1] per channel (what an RL policy emits)."""
        a = np.clip(np.nan_to_num(np.asarray(action, float).reshape(-1)), -1.0, 1.0)
        return self._advance(self.space.denormalise(a), a)

    def step_physical(self, command) -> tuple[np.ndarray, float, bool, bool, dict]:
        """Advance with a command in physical units (what a classical controller or a human emits)."""
        cmd = self.space.vector(command)
        return self._advance(cmd, self.space.normalise(cmd))

    def _advance(self, cmd, a_norm):
        self.obs = self.backend.step(cmd, self.space_name)
        self.t, self.steps = self.obs.state.t, self.steps + 1
        res = self.task.step(self.obs, a_norm, self.episode, self.t)
        self.ret += res.reward
        self.vec = self.task.observe(self.obs, self.episode)
        return self.vec, res.reward, res.terminated, res.truncated, {**res.info, "t": self.t, "ret": self.ret}

    def close(self):
        self.backend.close()
