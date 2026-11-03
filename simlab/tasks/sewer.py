"""sewer_drive: the Webots sewer world seen through the generic Task contract (drive a rover through a sewer map).

The sewer pipeline proper (goal posts, defect tracking, the report) stays in the Sewer tab; this task exists so the same
Sim / policy / environment code can also drive a rover inside Webots. It has no reward: inspection quality is measured by
the Sewer tab's evaluation against the planted defects.
"""
from __future__ import annotations

import numpy as np

from ..registry import TASKS
from ..spec.params import Issue, Param, apply_schema
from ..spec.vehicle import SpecError
from .base import Episode, Requirement, StepResult, Task, World


def map_fit(vehicle, map_name: str) -> dict:
    """For each pipe of a sewer map: can this vehicle's footprint drive (and turn) in it? {} if the map cannot be read."""
    try:
        from sewerbot.core.geometry import fit_check
        from sewerbot.core.mapspec import load_map
        from tools.gen_world import ROOT
        m = load_map(ROOT / "maps" / f"{map_name}.yaml")
    except Exception:                                                         # a missing map or a broken one is reported by the task itself
        return {}
    b = vehicle.body
    fits = {e.id: fit_check(b["width"], b["length"], b["height"], e.section) for e in m.edges.values()}
    start = [e.id for e in m.incident(m.start_node)]
    return {"map": map_name, "fits": fits, "start_edges": start, "start_ok": any(fits[i]["fits"] for i in start)}


PARAMS = (Param("map", "str", "straight_t", doc="Name of a map in maps/ (straight_t, star_five_goals, large_loop, or your own .yaml)"),)


class SewerDriveTask(Task):
    name = "sewer_drive"
    title = "Drive through a sewer (Webots)"
    description = "A rover in a generated sewer map, simulated by Webots. Slow to start; use the Sewer tab for inspection missions."
    kinds = ("ground_wheeled",)
    backends = ("webots",)
    needs_camera = True

    def __init__(self, spec, vehicle):
        if vehicle.kind != "ground_wheeled":
            raise SpecError([Issue("error", "kind", f"task 'sewer_drive' drives ground vehicles; {vehicle.name} is a {vehicle.kind}")])
        super().__init__(spec, vehicle)
        self.p, issues = apply_schema(PARAMS, spec.params, "params")
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        from tools.gen_world import ROOT
        if not (ROOT / "maps" / f"{self.p['map']}.yaml").is_file():
            raise SpecError([Issue("error", "params.map", f"no map file maps/{self.p['map']}.yaml")])
        self.vmax, self.wmax = vehicle.params["max_speed"], vehicle.params["max_yaw_rate"]

    def requirements(self) -> list[Requirement]:
        out = [Requirement("sensor", "a camera to find cracks and leaks", {"type": "camera", "ideal": False, "min_fps": 5.0, "min_width": 320}),
               Requirement("light", "a lamp: the pipes are dark", {}),
               Requirement("endurance", f"run for the whole episode ({self.spec.episode['max_time']:.0f} s)", {"seconds": float(self.spec.episode["max_time"])})]
        fit = map_fit(self.vehicle, self.p["map"])
        if fit:
            out.append(Requirement("fit", f"fit the pipes of map {self.p['map']}", fit))
        return out

    def build_world(self) -> World:
        return World([], (0.0, 0.0), theme=f"sewer:{self.p['map']}")

    def reset(self, rng: np.random.Generator) -> Episode:
        return Episode((0.0, 0.0, 0.0, 0.0), {}, {"max_time": self.spec.episode["max_time"]})     # the map decides where the rover starts

    def observation_dim(self) -> int:
        return 5

    def observe(self, obs, ep) -> np.ndarray:
        st, rays = obs.state, obs.sensors["range"]
        return np.clip(np.array([st.vel_body[0] / self.vmax, st.omega_body[2] / self.wmax, *(rays / 4.0)]), -3, 3).astype(np.float32)

    def step(self, obs, action, ep, t) -> StepResult:
        return StepResult(0.0, False, t >= ep.info["max_time"], {"terms": {}, "success": False, "collisions": 0})


TASKS.add(SewerDriveTask)
