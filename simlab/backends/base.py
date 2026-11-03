"""The contract between the brain (tasks, policies, trainers, dashboard) and a physics/visual backend.

A backend owns one vehicle in one world. It never decides what the vehicle should do (policies do) or what counts as
success (tasks do); it only advances physics and reports state and sensors, so MuJoCo, Webots or real hardware are
interchangeable behind it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from ..spec.caps import Caps


@dataclass
class State:
    """Ground-truth kinematic state of the vehicle (what a task needs to score it)."""
    t: float
    pos: np.ndarray                  # world position of the body origin, m
    quat: np.ndarray                 # (w, x, y, z)
    yaw: float                       # heading, rad (0 = +x, counter-clockwise)
    vel: np.ndarray                  # world-frame velocity, m/s
    vel_body: np.ndarray             # body-frame velocity, m/s (x forward, y left, z up)
    omega_body: np.ndarray           # body-frame angular velocity, rad/s
    collided: bool = False           # touched something other than the ground during the last step
    extra: dict = field(default_factory=dict)   # kind-specific: wheel speeds, rotor thrusts, battery ...


@dataclass
class Observation:
    state: State
    sensors: dict = field(default_factory=dict)   # sensor name -> value (pose: array, imu: dict, rangefinder: array)


class Backend:
    """Abstract backend. Subclasses implement everything below and register in simlab.registry.BACKENDS."""
    name = ""
    caps: Caps

    # -- lifecycle
    def reset(self, spawn: Sequence[float], placements: Mapping[str, Sequence[float]] | None = None,
              randomize: Mapping[str, float] | None = None, rng: np.random.Generator | None = None) -> Observation:
        """Put the vehicle at spawn=(x, y, z, yaw) and the movable items at placements[name]=(x, y, yaw)."""
        raise NotImplementedError

    def step(self, command, space: str | None = None) -> Observation:
        """Apply `command` (physical units, in `space`, default = the vehicle's) for one policy period."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    # -- visuals
    def render(self, camera: str, segmentation: bool = False) -> np.ndarray:
        """RGB uint8 HxWx3 from a named camera sensor (segmentation: int32 HxWx2 of [geom id, object type])."""
        raise NotImplementedError

    def scene(self) -> dict:
        """Static, JSON-serialisable description of every body/geom, for the 3D viewer."""
        raise NotImplementedError

    def poses(self) -> np.ndarray:
        """(n_bodies, 7) float32 [x y z qw qx qy qz] for the bodies listed in scene()['bodies']."""
        raise NotImplementedError

    @property
    def time(self) -> float:
        raise NotImplementedError
