"""Picklable environment factory for vectorised training.

Kept free of Stable-Baselines3 / PyTorch imports on purpose: on Windows every worker process re-imports the module
that defines its factory, and importing PyTorch eight times costs seconds and gigabytes.
"""
from __future__ import annotations

from ..envs.gym_env import SimEnv


class EnvFactory:
    def __init__(self, vehicle: dict, task: dict, backend: str = "mujoco", space: str | None = None, seed: int = 0):
        self.args = (vehicle, task, backend, space, seed)

    def __call__(self) -> SimEnv:
        vehicle, task, backend, space, seed = self.args
        return SimEnv(vehicle, task, backend, space=space, seed=seed)
