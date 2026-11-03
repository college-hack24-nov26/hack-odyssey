"""water_rescue: take a boat to a person in the water and stay beside them, with optional floating debris.

The person's position is given to the boat (ground truth). Finding a person is a detection problem the simulator does not pose here:
this task asks whether the *boat* can get there and stay afloat. Failures are scored as what they are: capsized, swamped (water over
the deck), stranded with a flat battery, or out of time.
"""
from __future__ import annotations

import math

import numpy as np

from ..backends.base import Observation
from ..registry import TASKS
from ..spec.params import Issue, Param, apply_schema
from ..spec.vehicle import SpecError
from .base import Episode, Item, StepResult, Task, World

PARAMS = (
    Param("range_m", "float", 150.0, 5.0, 5000.0, "m", "Distance from the start to the person"),
    Param("reach_radius", "float", 4.0, 0.5, 100.0, "m", "Close enough to throw a line or lifebuoy"),
    Param("hold_s", "float", 2.0, 0.0, 60.0, "s", "How long to stay within reach, slowly"),
    Param("max_speed_at_person", "float", 1.5, 0.1, 20.0, "m/s", "Must be slower than this when it arrives"),
    Param("debris", "int", 0, 0, 100, "", "Floating boxes scattered along the way"),
    Param("auto_time", "bool", True, doc="Episode limit from the distance and the boat's speed instead of episode.max_time"),
)
REWARD = {"progress": 0.2, "reached": 10.0, "collision": -0.5, "time": -0.01, "action": -0.01}
CAPSIZE_DEG, SWAMP_M, SWAMP_S = 70.0, 0.05, 3.0


class WaterRescueTask(Task):
    name = "water_rescue"
    title = "Reach a person in the water"
    description = "Go to a person floating in open water, slow down beside them and stay there; debris, waves, wind and failures can be added."
    kinds = ("boat",)

    def __init__(self, spec, vehicle):
        if vehicle.kind != "boat":
            raise SpecError([Issue("error", "kind", f"task 'water_rescue' drives boats; {vehicle.name} is a {vehicle.kind}")])
        super().__init__(spec, vehicle)
        self.p, issues = apply_schema(PARAMS, spec.params, "params")
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        self.rew = {**REWARD, **spec.reward}
        self.vmax = max(vehicle.params.get("thrust_max", 1.0) ** 0.5, 1.0)           # a scale for observations only
        self.ref = max(self.p["range_m"], 10.0)
        self.max_time = (3.0 * self.p["range_m"] / 2.0 + 45.0) if self.p["auto_time"] else spec.episode["max_time"]

    # ---------------------------------------------------------------- world
    def build_world(self) -> World:
        r = self.p["range_m"]
        size = 2.0 * r + 60.0
        items = [Item("person", "cylinder", (0.3, 0.45), (0, 0, -10.0), rest_z=0.45, movable=True, collide=False, label="person",
                      rgba=(1.0, 0.45, 0.1, 1.0))]
        for i in range(self.p["debris"]):
            items.append(Item(f"debris{i}", "box", (0.3, 0.3, 0.12), (0, 0, -10.0), rest_z=0.0, movable=True, rgba=(0.45, 0.32, 0.2, 1.0)))
        return World(items, (size, size), theme="water")

    def reset(self, rng: np.random.Generator) -> Episode:
        r = self.p["range_m"]
        bearing = rng.uniform(-0.9, 0.9)                                     # somewhere ahead; the boat starts facing +x
        gx, gy = r * math.cos(bearing), r * math.sin(bearing)
        placements = {"person": (gx, gy, 0.0)}
        for i in range(self.p["debris"]):
            f, off = rng.uniform(0.15, 0.9), rng.uniform(-6.0, 6.0)          # scattered around the straight line to the person
            placements[f"debris{i}"] = (f * gx - off * math.sin(bearing), f * gy + off * math.cos(bearing), rng.uniform(0, math.pi))
        return Episode((0.0, 0.0, 0.0, 0.0), placements, {"goal": (gx, gy), "goal_r": self.p["reach_radius"], "max_time": self.max_time,
                                                          "prev_dist": r, "collisions": 0, "hold": 0.0, "wet": 0.0, "start": (0.0, 0.0)})

    # ---------------------------------------------------------------- policy interface
    def observation_dim(self) -> int:
        return 9

    def observe(self, obs: Observation, ep: Episode) -> np.ndarray:
        st = obs.state
        dx, dy = ep.info["goal"][0] - st.pos[0], ep.info["goal"][1] - st.pos[1]
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        bx, by = c * dx + s * dy, -s * dx + c * dy
        bearing = math.atan2(by, bx)
        vec = [bx / self.ref, by / self.ref, min(math.hypot(dx, dy) / self.ref, 2.0), math.cos(bearing), math.sin(bearing),
               st.vel_body[0] / 5.0, st.vel_body[1] / 5.0, st.omega_body[2] / 2.0, st.extra.get("roll_deg", 0.0) / 45.0]
        return np.clip(np.array(vec), -2.0, 2.0).astype(np.float32)

    def step(self, obs, action, ep, t) -> StepResult:
        st = obs.state
        dist = math.hypot(ep.info["goal"][0] - st.pos[0], ep.info["goal"][1] - st.pos[1])
        speed = float(np.linalg.norm(st.vel[:2]))
        if st.collided:
            ep.info["collisions"] += 1
        near = dist < self.p["reach_radius"] and speed < self.p["max_speed_at_person"]
        ep.info["hold"] = ep.info["hold"] + self.policy_dt if near else 0.0
        reached = ep.info["hold"] >= self.p["hold_s"]
        ep.info["wet"] = ep.info["wet"] + self.policy_dt if st.extra.get("deck_wet", 0.0) > SWAMP_M else max(0.0, ep.info["wet"] - self.policy_dt)
        crash = None
        if abs(st.extra.get("roll_deg", 0.0)) > CAPSIZE_DEG:
            crash = "capsized"
        elif ep.info["wet"] > SWAMP_S:
            crash = "swamped"
        elif st.extra.get("soc", 1.0) <= 0.0 and speed < 0.2 and not reached:
            crash = "battery empty"
        a = np.asarray(action, float)
        terms = {"progress": self.rew["progress"] * (ep.info["prev_dist"] - dist), "reached": self.rew["reached"] if reached else 0.0,
                 "collision": self.rew["collision"] if st.collided else 0.0, "time": self.rew["time"] * self.policy_dt,
                 "action": self.rew["action"] * float(np.mean(a * a))}
        ep.info["prev_dist"] = dist
        terminated = reached or crash is not None
        truncated = not terminated and t >= ep.info["max_time"]
        return StepResult(sum(terms.values()), terminated, truncated,
                          {"terms": terms, "success": reached, "dist": dist, "collisions": ep.info["collisions"], "crash": crash})

    def baseline(self):
        from ..control.rescue import RescueController
        return RescueController(self)

    def markers(self, ep):
        gx, gy = ep.info["goal"]
        return [{"kind": "goal", "pos": [gx, gy, 0.0], "r": self.p["reach_radius"]}]


TASKS.add(WaterRescueTask)
