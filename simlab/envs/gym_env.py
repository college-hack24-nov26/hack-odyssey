"""Gymnasium wrapper around Sim: the interface Stable-Baselines3 (and any other RL library) trains against."""
from __future__ import annotations

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from ..catalog import get_vehicle
from ..spec.task import TaskSpec
from ..spec.vehicle import VehicleSpec
from ..tasks import get_task_spec
from .core import Sim


def as_vehicle(v) -> VehicleSpec:
    return v if isinstance(v, VehicleSpec) else VehicleSpec.from_dict(v) if isinstance(v, dict) else get_vehicle(v)


def as_task(t) -> TaskSpec:
    return t if isinstance(t, TaskSpec) else TaskSpec.from_dict(t) if isinstance(t, dict) else get_task_spec(t)


class SimEnv(gym.Env):
    """Observations are the task's vector, actions are normalised to [-1, 1] per command channel.

    Episode k of an env seeded s always starts from the same scenario, so training runs are reproducible.
    """
    metadata = {"render_modes": []}

    def __init__(self, vehicle, task, backend: str = "mujoco", space: str | None = None, seed: int = 0, **backend_opts):
        super().__init__()
        self.sim = Sim(as_vehicle(vehicle), as_task(task), backend, space=space, seed=seed, **backend_opts)
        self.observation_space = spaces.Box(-3.0, 3.0, (self.sim.obs_dim,), np.float32)
        self.action_space = spaces.Box(-1.0, 1.0, (self.sim.space.dim,), np.float32)
        self._seed, self._episode = seed, 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self._seed, self._episode = seed, 0
        obs = self.sim.reset(seed=self._seed * 100_003 + self._episode)
        self._episode += 1
        return obs, {}

    def step(self, action):
        obs, reward, terminated, truncated, info = self.sim.step(action)
        return obs, float(reward), terminated, truncated, info

    def set_difficulty(self, level: float) -> None:
        """Curriculum: 0 = easiest version of the task, 1 = the full task. Takes effect at the next reset."""
        self.sim.task.set_difficulty(float(level))

    def close(self):
        self.sim.close()
