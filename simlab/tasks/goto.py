"""goto: drive a ground vehicle to a goal circle across an arena with random obstacles."""
from __future__ import annotations

import math

import numpy as np

from ..backends.base import Observation
from ..registry import TASKS
from ..spec.params import Issue, Param, apply_schema
from ..spec.sensors import SensorSpec
from ..spec.vehicle import SpecError
from .base import Episode, Item, StepResult, Task, World

PARAMS = (
    Param("goal_min_dist", "float", 3.0, 0.2, 500.0, "m", "Closest a goal is placed to the start"),
    Param("goal_max_dist", "float", 8.0, 0.5, 1000.0, "m", "Farthest a goal is placed from the start"),
    Param("goal_radius", "float", 0.0, 0.0, 50.0, "m", "Reaching distance; 0 = auto (0.6 x vehicle length, at least 0.25)"),
    Param("obstacles", "int", 6, 0, 200, "", "Number of movable obstacles"),
    Param("obstacle_min", "float", 0.3, 0.05, 20.0, "m", "Smallest obstacle half-size"),
    Param("obstacle_max", "float", 1.0, 0.1, 40.0, "m", "Largest obstacle half-size"),
    Param("rays", "int", 9, 1, 64, "", "Range rays across the front 180 degrees"),
    Param("terminate_on_collision", "bool", False, doc="End the episode on the first contact"),
    Param("auto_time", "bool", True, doc="Episode limit = 2.5 x farthest goal / top speed + 8 s instead of episode.max_time"),
)
WORLD = (
    Param("width", "float", 14.0, 2.0, 2000.0, "m", "Arena size along x"),
    Param("depth", "float", 14.0, 2.0, 2000.0, "m", "Arena size along y"),
)
REWARD = {"progress": 1.0, "reached": 10.0, "collision": -0.5, "time": -0.05, "action": -0.01}
OBS_FIXED = 8            # goal (body frame x, y, distance, cos/sin bearing) + body velocity (vx, vy, yaw rate)
WALL, OBSTACLE_HEIGHT = 0.3, 0.7


class GotoTask(Task):
    name = "goto"
    title = "Go to the goal"
    description = "Reach a goal circle in an arena with random obstacles, without hitting them."
    kinds = ("ground_wheeled",)

    def __init__(self, spec, vehicle):
        if vehicle.kind != "ground_wheeled":
            raise SpecError([Issue("error", "kind", f"task 'goto' drives ground vehicles; {vehicle.name} is a "
                                                    f"{vehicle.kind} (use 'waypoints' for drones)")])
        super().__init__(spec, vehicle)
        self.p, i1 = apply_schema(PARAMS, spec.params, "params")
        self.wp, i2 = apply_schema(WORLD, spec.world, "world")
        issues = i1 + i2 + [Issue("error", f"reward.{k}", f"unknown term; known: {sorted(REWARD)}")
                            for k in set(spec.reward) - set(REWARD)]
        if self.p["goal_max_dist"] < self.p["goal_min_dist"]:
            issues.append(Issue("error", "params.goal_max_dist", "must be at least goal_min_dist"))
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        b = vehicle.body
        self.rew = {**REWARD, **spec.reward}
        self.radius = 0.5 * math.hypot(b["length"], b["width"])
        self.goal_r = self.p["goal_radius"] or max(0.25, 0.6 * b["length"])
        self.vmax, self.wmax = vehicle.params["max_speed"], vehicle.params["max_yaw_rate"]
        self.ref = max(self.p["goal_max_dist"], 1.0)
        self.max_range = min(12.0, max(2.0, 12 * b["length"]))
        self.max_time = ((2.5 * self.p["goal_max_dist"] / self.vmax + 8.0) if self.p["auto_time"]
                         else spec.episode["max_time"])
        self._sizes = np.random.default_rng(0)
        self.level = 1.0

    def set_difficulty(self, level: float) -> None:
        """Easy: short goals, no obstacles. Hard (the configured task): full distance range and obstacle count."""
        self.level = max(0.0, min(1.0, level))

    # ---------------------------------------------------------------- world
    def required_sensors(self):
        if self.vehicle.sensor("rangefinder", "range"):
            return []
        return [SensorSpec("rangefinder", "range", {"rays": self.p["rays"], "fov_deg": 180.0, "max_range": self.max_range,
                                                    "direction": "forward", "noise": 0.0, "rate_hz": 20.0})]

    def build_world(self) -> World:
        w, d = self.wp["width"], self.wp["depth"]
        items = []
        for name, (x, y, hx, hy) in {"wall_n": (0, d / 2 + WALL / 2, w / 2 + WALL, WALL / 2), "wall_s": (0, -d / 2 - WALL / 2, w / 2 + WALL, WALL / 2),
                                     "wall_e": (w / 2 + WALL / 2, 0, WALL / 2, d / 2), "wall_w": (-w / 2 - WALL / 2, 0, WALL / 2, d / 2)}.items():
            items.append(Item(name, "box", (hx, hy, 0.3), (x, y, 0.3), rgba=(0.42, 0.44, 0.5, 1)))
        lo, hi = self.p["obstacle_min"], self.p["obstacle_max"]
        self._obstacle_r = []
        for i in range(self.p["obstacles"]):
            if self._sizes.random() < 0.5:
                hx, hy = self._sizes.uniform(lo, hi, 2)
                items.append(Item(f"obstacle{i}", "box", (hx, hy, OBSTACLE_HEIGHT / 2), (0, 0, -10.0),
                                  rest_z=OBSTACLE_HEIGHT / 2, movable=True, rgba=(0.78, 0.36, 0.3, 1)))
                self._obstacle_r.append(math.hypot(hx, hy))
            else:
                r = self._sizes.uniform(lo, hi)
                items.append(Item(f"obstacle{i}", "cylinder", (r, OBSTACLE_HEIGHT / 2), (0, 0, -10.0),
                                  rest_z=OBSTACLE_HEIGHT / 2, movable=True, rgba=(0.3, 0.5, 0.78, 1)))
                self._obstacle_r.append(r)
        items.append(Item("goal", "cylinder", (self.goal_r, 0.01), (0, 0, -10.0), rest_z=0.01, rgba=(0.15, 0.85, 0.3, 0.55),
                          collide=False, movable=True, label="goal"))
        return World(items, (w, d))

    def reset(self, rng: np.random.Generator) -> Episode:
        w, d = self.wp["width"], self.wp["depth"]
        margin = 2 * self.radius + 0.4
        mx, my = max(0.0, w / 2 - margin), max(0.0, d / 2 - margin)
        lo = self.p["goal_min_dist"]
        hi = lo + self.level * (self.p["goal_max_dist"] - lo)
        active = round(self.level * self.p["obstacles"])
        for attempt in range(200):
            sx, sy = rng.uniform(-mx, mx), rng.uniform(-my, my)
            dist, ang = rng.uniform(lo, hi), rng.uniform(0, 2 * math.pi)
            gx, gy = sx + dist * math.cos(ang), sy + dist * math.sin(ang)
            if abs(gx) <= mx and abs(gy) <= my:
                break
        else:                                   # arena too small for the requested distance: take the opposite corner
            gx, gy = -math.copysign(mx, sx), -math.copysign(my, sy)
        # (x, y, clearance): a new obstacle of radius r must sit more than r + clearance away from every entry
        placements, taken = {"goal": (gx, gy, 0.0)}, [(sx, sy, self.radius + 0.4), (gx, gy, self.goal_r + self.radius + 0.3)]
        for i in range(self.p["obstacles"]):
            r = self._obstacle_r[i]
            if i >= active:
                placements[f"obstacle{i}"] = (0.0, 0.0, 0.0, -10.0)
                continue
            for _ in range(80):
                x, y = rng.uniform(-mx, mx), rng.uniform(-my, my)
                if all(math.hypot(x - ox, y - oy) > r + c for ox, oy, c in taken):
                    placements[f"obstacle{i}"] = (x, y, rng.uniform(0, math.pi))
                    taken.append((x, y, r + 2 * self.radius + 0.2))     # leave room for the vehicle to pass between
                    break
            else:
                placements[f"obstacle{i}"] = (0.0, 0.0, 0.0, -10.0)     # no room: park it under the floor
        spawn = (sx, sy, 0.0, rng.uniform(-math.pi, math.pi))
        return Episode(spawn, placements, {"goal": (gx, gy), "goal_r": self.goal_r, "max_time": self.max_time,
                                           "prev_dist": math.hypot(gx - sx, gy - sy), "collisions": 0, "start": (sx, sy)})

    # ---------------------------------------------------------------- policy interface
    def observation_dim(self) -> int:
        return OBS_FIXED + self.p["rays"]

    def observe(self, obs: Observation, ep: Episode) -> np.ndarray:
        st = obs.state
        dx, dy = ep.info["goal"][0] - st.pos[0], ep.info["goal"][1] - st.pos[1]
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        bx, by = c * dx + s * dy, -s * dx + c * dy
        dist = math.hypot(dx, dy)
        bearing = math.atan2(by, bx)
        rays = obs.sensors["range"] / self.max_range
        vec = np.concatenate([[bx / self.ref, by / self.ref, min(dist / self.ref, 2.0), math.cos(bearing), math.sin(bearing),
                               st.vel_body[0] / self.vmax, st.vel_body[1] / self.vmax, st.omega_body[2] / self.wmax], rays])
        return np.clip(vec, -2.0, 2.0).astype(np.float32)

    def step(self, obs, action, ep, t) -> StepResult:
        st = obs.state
        dist = math.hypot(ep.info["goal"][0] - st.pos[0], ep.info["goal"][1] - st.pos[1])
        reached = dist < self.goal_r
        if st.collided:
            ep.info["collisions"] += 1
        a = np.asarray(action, float)
        terms = {"progress": self.rew["progress"] * (ep.info["prev_dist"] - dist),
                 "reached": self.rew["reached"] if reached else 0.0,
                 "collision": self.rew["collision"] if st.collided else 0.0,
                 "time": self.rew["time"] * self.policy_dt,
                 "action": self.rew["action"] * float(np.mean(a * a))}
        ep.info["prev_dist"] = dist
        terminated = reached or (st.collided and self.p["terminate_on_collision"])
        truncated = not terminated and t >= ep.info["max_time"]
        return StepResult(sum(terms.values()), terminated, truncated,
                          {"terms": terms, "success": reached, "dist": dist, "collisions": ep.info["collisions"]})

    def baseline(self):
        from ..control.ground import GotoController
        return GotoController(self)

    def markers(self, ep):
        gx, gy = ep.info["goal"]
        return [{"kind": "goal", "pos": [gx, gy, 0.0], "r": self.goal_r}]


TASKS.add(GotoTask)
