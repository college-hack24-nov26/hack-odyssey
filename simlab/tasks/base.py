"""A task = world + objective: what exists in the scene, how each episode is set up, what the policy sees, how it is scored."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ..backends.base import Observation
from ..spec.task import TaskSpec
from ..spec.vehicle import VehicleSpec


@dataclass
class Requirement:
    """What a task asks of the hardware (the Builder checks a build against these).

    kind: sensor (params: type camera|rangefinder|imu|pose, ideal, min_fps, min_width) | light | endurance (seconds) |
          speed (min_mps) | slope (deg) | fit (the pipes a vehicle must fit into)
    """
    kind: str
    title: str
    params: dict = field(default_factory=dict)


@dataclass
class Item:
    """One thing in the scene. A `movable` item can be re-placed every episode without rebuilding the physics model."""
    name: str
    shape: str                          # box | cylinder | sphere | capsule
    size: tuple                         # MuJoCo half-sizes: box (hx, hy, hz), cylinder (r, half_height), sphere (r,)
    pos: tuple = (0.0, 0.0, 0.0)        # where it starts (a movable item may start parked out of the way)
    rest_z: float | None = None         # height of its centre once placed by an episode; default = pos[2]
    yaw: float = 0.0
    rgba: tuple = (0.6, 0.6, 0.62, 1.0)
    collide: bool = True
    movable: bool = False
    label: str | None = None            # perception class this item represents (segmentation ground truth)
    quat: tuple | None = None           # full orientation (w, x, y, z) instead of just `yaw`; static items only
    hidden: bool = False                # collision only: solid and seen by range rays, but never drawn by the onboard camera


@dataclass
class MeshItem:
    """A fixed visual surface made of triangles, optionally textured (pipe walls, defect patches). It never collides:
    pair it with box Items for the physics. Vertices are per triangle (no sharing), so `uv` is per vertex."""
    name: str
    verts: object                                   # (N, 3) array, world frame
    faces: object                                   # (M, 3) int array of vertex indices
    uv: object | None = None                        # (N, 2) texture coordinates; values outside 0..1 tile the texture
    texture: str | None = None                      # key into World.assets (a PNG)
    rgba: tuple = (0.6, 0.6, 0.62, 1.0)             # colour when untextured, and the colour the 3D viewer draws
    label: str | None = None                        # perception class (segmentation ground truth)


@dataclass
class World:
    items: list[Item] = field(default_factory=list)
    arena: tuple[float, float] = (20.0, 20.0)      # ground extent (x, y), m, centred on the origin
    ground_friction: float = 1.0
    theme: str = "grid"                            # "grid" (a tiled floor), "water" (a flat blue surface at z = 0) or "pipe" (dark, lit by the vehicle's lamp)
    meshes: list[MeshItem] = field(default_factory=list)
    assets: dict[str, bytes] = field(default_factory=dict)       # texture files: name -> PNG bytes


@dataclass
class Episode:
    spawn: tuple[float, float, float, float]       # x, y, z, yaw
    placements: dict[str, tuple] = field(default_factory=dict)   # item -> (x, y, yaw) or (x, y, yaw, z)
    info: dict = field(default_factory=dict)       # task bookkeeping (goal position, waypoint list, ...)


@dataclass
class StepResult:
    reward: float
    terminated: bool
    truncated: bool
    info: dict = field(default_factory=dict)       # includes "terms": reward breakdown, "success": bool


class Task:
    """Subclass, set `name`, register with TASKS.add(MyTask). One instance serves one vehicle."""
    name = ""
    title = ""
    description = ""
    kinds: tuple = ()                   # vehicle kinds this task can drive; empty = any
    backends: tuple = ("mujoco",)       # simulators whose worlds this task can build
    needs_camera = False                # True when the task cannot be done without seeing (search, inspection)

    def __init__(self, spec: TaskSpec, vehicle: VehicleSpec):
        self.spec = spec
        self.vehicle = vehicle
        self.policy_dt = 1.0 / spec.episode["policy_hz"]

    def required_sensors(self) -> list:
        """SensorSpecs the task needs and will add to the vehicle when it has none of that name."""
        return []

    def requirements(self) -> list[Requirement]:
        """What this task needs from the hardware. The default follows from what the task already declares; override to add more."""
        out = [Requirement("sensor", f"a {s.type} (the simulator adds an ideal one for you)", {"type": s.type, "ideal": True}) for s in self.required_sensors()]
        if self.spec.labels:
            out.append(Requirement("sensor", "a camera to see " + ", ".join(self.spec.labels) + ("" if self.needs_camera else " (optional here)"),
                                   {"type": "camera", "ideal": False, "optional": not self.needs_camera, "min_fps": 5.0, "min_width": 320}))
        seconds = float(getattr(self, "max_time", 0.0) or self.spec.episode["max_time"])
        out.append(Requirement("endurance", f"run for the whole episode ({seconds:.0f} s)", {"seconds": seconds}))
        return out

    # world and episode
    def build_world(self) -> World:
        raise NotImplementedError

    def reset(self, rng: np.random.Generator) -> Episode:
        raise NotImplementedError

    # what the policy sees / how it is scored
    def observation_dim(self) -> int:
        raise NotImplementedError

    def observe(self, obs: Observation, episode: Episode) -> np.ndarray:
        """Flat float32 vector in roughly [-1, 1]; the same vector feeds RL policies and classical controllers."""
        raise NotImplementedError

    def step(self, obs: Observation, action: Sequence[float], episode: Episode, t: float) -> StepResult:
        raise NotImplementedError

    def baseline(self):
        """A classical controller for this task (simlab.control), or None."""
        return None

    def set_difficulty(self, level: float) -> None:
        """Curriculum hook: scale the task between trivial (0) and full (1) from the next reset on. Default: no-op."""

    def capture_groups(self) -> list[str] | None:
        """For worlds that are the same every episode (a pipe network): independent things to photograph, so that dataset capture can
        keep each one in a single split. None = the task re-arranges its objects every episode and capture splits by scene."""
        return None

    def viewpoints(self, group: str, rng: np.random.Generator, n: int) -> list[tuple[float, float, float, float]]:
        """`n` camera poses (x, y, z, yaw) for one of capture_groups()."""
        raise NotImplementedError

    def markers(self, episode: Episode) -> list[dict]:
        """Extra things for the viewer to draw: [{"kind": "goal", "pos": [x, y, z], "r": 0.3}, ...]."""
        return []
