"""sandbox: an arena with a few fixed obstacles and nothing to achieve. For driving, watching and testing any machine."""
from __future__ import annotations

import math

import numpy as np

from ..backends.base import Observation
from ..registry import TASKS
from ..spec.params import Param, apply_schema
from ..spec.vehicle import SpecError
from .base import Episode, Item, StepResult, Task, World

WORLD = (
    Param("width", "float", 24.0, 2.0, 2000.0, "m"), Param("depth", "float", 24.0, 2.0, 2000.0, "m"),
    Param("theme", "enum", "grid", doc="Floor: tiles, or open water (for boats)", choices=("grid", "water")),
)
PARAMS = (Param("pillars", "int", 4, 0, 60, "", "Fixed pillars to drive around"),)


class SandboxTask(Task):
    name = "sandbox"
    title = "Sandbox"
    description = "An open arena with a few pillars. No goal: place any machine and drive it yourself."

    def __init__(self, spec, vehicle):
        super().__init__(spec, vehicle)
        self.wp, i1 = apply_schema(WORLD, spec.world, "world")
        self.p, i2 = apply_schema(PARAMS, spec.params, "params")
        if any(i.level == "error" for i in i1 + i2):
            raise SpecError(i1 + i2)
        self.max_time = spec.episode["max_time"]

    def build_world(self) -> World:
        w, d = self.wp["width"], self.wp["depth"]
        items = [Item(n, "box", (hx, hy, 0.3), (x, y, 0.3), rgba=(0.42, 0.44, 0.5, 1)) for n, (x, y, hx, hy) in {
            "wall_n": (0, d / 2 + 0.15, w / 2 + 0.3, 0.15), "wall_s": (0, -d / 2 - 0.15, w / 2 + 0.3, 0.15),
            "wall_e": (w / 2 + 0.15, 0, 0.15, d / 2), "wall_w": (-w / 2 - 0.15, 0, 0.15, d / 2)}.items()]
        rng = np.random.default_rng(1)
        for i in range(self.p["pillars"]):
            a = 2 * math.pi * i / max(self.p["pillars"], 1)
            r = 0.3 * min(w, d)
            items.append(Item(f"pillar{i}", "cylinder", (0.35, 0.8), (r * math.cos(a), r * math.sin(a), 0.8),
                              rgba=tuple(float(c) for c in (*rng.uniform(0.35, 0.8, 3), 1.0))))
        return World(items, (w, d), theme=self.wp["theme"])

    def reset(self, rng: np.random.Generator) -> Episode:
        return Episode((0.0, 0.0, 0.0, 0.0), {}, {"max_time": self.max_time})

    def observation_dim(self) -> int:
        return 9

    def observe(self, obs: Observation, ep: Episode) -> np.ndarray:
        st = obs.state
        ref = max(self.wp["width"], self.wp["depth"]) / 2
        return np.clip(np.concatenate([st.pos / ref, st.vel_body / 5.0, st.omega_body / 3.0]), -3.0, 3.0).astype(np.float32)

    def step(self, obs, action, ep, t) -> StepResult:
        return StepResult(0.0, False, t >= ep.info["max_time"], {"terms": {}, "success": False, "collisions": 0})


TASKS.add(SandboxTask)
