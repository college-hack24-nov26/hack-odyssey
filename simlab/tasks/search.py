"""search: find every target object in an arena by looking at it, among lookalike distractors.

The task knows where targets are (it scores the run); a policy only gets the *perceived* target: what a camera plus a
detector would report. During training that is computed from geometry (field of view and range), and the same
interface can be fed by a real detector. Works for ground vehicles and multirotors.
"""
from __future__ import annotations

import math

import numpy as np

from ..backends.base import Observation
from ..registry import TASKS
from ..spec.params import Issue, Param, apply_schema
from ..spec.sensors import SensorSpec
from ..spec.vehicle import SpecError
from ..vehicles.multirotor import euler
from .base import Episode, Item, StepResult, Task, World

PARAMS = (
    Param("n_targets", "int", 3, 1, 12, "", "Labelled objects to find"),
    Param("n_distractors", "int", 6, 0, 40, "", "Unlabelled lookalikes (similar shapes, other colours)"),
    Param("find_range", "float", 4.0, 0.5, 50.0, "m", "A target counts as found when seen from within this distance"),
    Param("altitude", "float", 2.5, 0.5, 50.0, "m", "Cruise altitude for flying vehicles"),
    Param("auto_time", "bool", True, doc="Episode limit from the arena size and the vehicle's speed"),
)
WORLD = (Param("width", "float", 20.0, 4.0, 500.0, "m"), Param("depth", "float", 20.0, 4.0, 500.0, "m"))
REWARD = {"found": 5.0, "done": 10.0, "time": -0.02, "action": -0.01, "collision": -0.5, "crash": -10.0}
# class name -> (shape, size, rgba): distinct in colour AND shape, so a camera can tell them apart
LOOKS = {"crate": ("box", (0.3, 0.3, 0.3), (0.85, 0.15, 0.15, 1)), "barrel": ("cylinder", (0.25, 0.45), (0.15, 0.35, 0.9, 1)),
         "ball": ("sphere", (0.3,), (0.95, 0.85, 0.1, 1))}
EXTRA = [("box", (0.25, 0.4, 0.35), (0.9, 0.5, 0.1, 1)), ("cylinder", (0.3, 0.3), (0.6, 0.2, 0.7, 1)), ("sphere", (0.25,), (0.2, 0.7, 0.3, 1))]
DISTRACTORS = [("box", (0.3, 0.3, 0.3), (0.5, 0.5, 0.52, 1)), ("cylinder", (0.25, 0.45), (0.45, 0.4, 0.3, 1)),
               ("box", (0.35, 0.2, 0.25), (0.3, 0.45, 0.35, 1)), ("cylinder", (0.3, 0.25), (0.6, 0.6, 0.6, 1))]


def _half_height(shape: str, size: tuple) -> float:
    return size[2] if shape == "box" else size[1] if shape == "cylinder" else size[0]


class SearchTask(Task):
    name = "search"
    title = "Find the targets"
    description = "Look around an arena and find every target object among lookalike distractors."
    kinds = ("ground_wheeled", "multirotor")
    needs_camera = True

    def __init__(self, spec, vehicle):
        super().__init__(spec, vehicle)
        self.p, i1 = apply_schema(PARAMS, spec.params, "params")
        self.wp, i2 = apply_schema(WORLD, spec.world, "world")
        issues = i1 + i2 + [Issue("error", f"reward.{k}", f"unknown term; known: {sorted(REWARD)}") for k in set(spec.reward) - set(REWARD)]
        self.classes = list(spec.labels) or list(LOOKS)
        if len(self.classes) < 1:
            issues.append(Issue("error", "labels", "needs at least one class"))
        cam = vehicle.sensor("camera")
        if cam is None:
            issues.append(Issue("error", "sensors", f"{vehicle.name} has no camera: this task is about seeing the targets"))
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        b, v = vehicle.body, vehicle.params
        self.rew = {**REWARD, **spec.reward}
        self.air = vehicle.kind == "multirotor"
        self.radius = 0.5 * math.hypot(b["length"], b["width"])
        self.vmax = v["max_speed"]
        self.wmax = v["max_yaw_rate"]
        self.vup, self.vdown = v.get("max_climb", 0.0), v.get("max_descent", 0.0)
        self.tilt = math.radians(v["max_tilt_deg"]) if self.air else 0.0
        self.fov = math.radians(cam.options["fov_deg"])
        self.sense = 2.0 * self.p["find_range"]
        self.ceiling = max(2 * self.p["altitude"], 5.0)
        self.max_range = min(12.0, max(2.0, 12 * b["length"]))
        area = (self.wp["width"] + self.wp["depth"]) / 2
        self.max_time = (6.0 * area / (0.4 * self.vmax) + 20.0) if self.p["auto_time"] else spec.episode["max_time"]
        self._sizes = np.random.default_rng(0)

    def required_sensors(self):
        if self.air or self.vehicle.sensor("rangefinder", "range"):
            return []
        return [SensorSpec("rangefinder", "range", {"rays": 9, "fov_deg": 180.0, "max_range": self.max_range, "direction": "forward",
                                                    "noise": 0.0, "rate_hz": 20.0})]

    # ---------------------------------------------------------------- world
    def _look(self, cls_index: int):
        name = self.classes[cls_index]
        return LOOKS.get(name) or EXTRA[cls_index % len(EXTRA)]

    def build_world(self) -> World:
        w, d = self.wp["width"], self.wp["depth"]
        items = [Item(n, "box", (hx, hy, 0.3), (x, y, 0.3), rgba=(0.42, 0.44, 0.5, 1)) for n, (x, y, hx, hy) in {
            "wall_n": (0, d / 2 + 0.15, w / 2 + 0.3, 0.15), "wall_s": (0, -d / 2 - 0.15, w / 2 + 0.3, 0.15),
            "wall_e": (w / 2 + 0.15, 0, 0.15, d / 2), "wall_w": (-w / 2 - 0.15, 0, 0.15, d / 2)}.items()]
        self._extent = []                                         # footprint radius of each placeable item
        for i in range(self.p["n_targets"]):
            shape, size, rgba = self._look(i % len(self.classes))
            items.append(Item(f"target{i}", shape, size, (0, 0, -10.0), rest_z=_half_height(shape, size), rgba=rgba, movable=True,
                              label=self.classes[i % len(self.classes)]))
            self._extent.append(max(size[:2]) * (1.42 if shape == "box" else 1.0))
        for i in range(self.p["n_distractors"]):
            shape, size, rgba = DISTRACTORS[i % len(DISTRACTORS)]
            items.append(Item(f"distractor{i}", shape, size, (0, 0, -10.0), rest_z=_half_height(shape, size), rgba=rgba, movable=True))
            self._extent.append(max(size[:2]) * (1.42 if shape == "box" else 1.0))
        return World(items, (w, d))

    def reset(self, rng: np.random.Generator) -> Episode:
        w, d, n_t = self.wp["width"], self.wp["depth"], self.p["n_targets"]
        margin = 1.0 + self.radius
        mx, my = w / 2 - margin, d / 2 - margin
        sx, sy = rng.uniform(-mx, mx), rng.uniform(-my, my)
        placements, taken, targets = {}, [(sx, sy, self.radius + 1.5)], []
        names = [f"target{i}" for i in range(n_t)] + [f"distractor{i}" for i in range(self.p["n_distractors"])]
        for k, name in enumerate(names):
            r = self._extent[k]
            for _ in range(100):
                x, y = rng.uniform(-mx, mx), rng.uniform(-my, my)
                if all(math.hypot(x - ox, y - oy) > r + c for ox, oy, c in taken):
                    placements[name] = (x, y, rng.uniform(0, math.pi))
                    taken.append((x, y, r + 0.9))
                    if name.startswith("target"):
                        targets.append((x, y))
                    break
            else:
                placements[name] = (0.0, 0.0, 0.0, -10.0)
                if name.startswith("target"):
                    targets.append((None, None))
        spawn = (sx, sy, self.p["altitude"] if self.air else 0.0, rng.uniform(-math.pi, math.pi))
        return Episode(spawn, placements, {"targets": targets, "found": [t[0] is None for t in targets], "max_time": self.max_time,
                                           "collisions": 0, "prev_vz": 0.0})

    # ---------------------------------------------------------------- perception (what the policy is told)
    def perceive(self, st, ep: Episode) -> tuple[float, float, float, int] | None:
        """(bearing, range, found-flag-index) of the nearest unfound target inside the camera's view, else None."""
        best = None
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        for i, (tx, ty) in enumerate(ep.info["targets"]):
            if ep.info["found"][i] or tx is None:
                continue
            dx, dy = tx - st.pos[0], ty - st.pos[1]
            rng_ = math.hypot(dx, dy)
            bearing = math.atan2(-s * dx + c * dy, c * dx + s * dy)
            if rng_ <= self.sense and abs(bearing) <= self.fov / 2 and (best is None or rng_ < best[1]):
                best = (bearing, rng_, i)
        return best

    def observation_dim(self) -> int:
        return 10 + (0 if self.air else 9)           # 10 own/perceived values, plus 9 range rays for ground vehicles

    def observe(self, obs: Observation, ep: Episode) -> np.ndarray:
        """[body velocity (3), yaw rate, altitude, target seen?, cos/sin of its bearing, its range, fraction found, (rays)]."""
        st = obs.state
        seen = self.perceive(st, ep)
        vis = [1.0, math.cos(seen[0]), math.sin(seen[0]), seen[1] / self.sense] if seen else [0.0, 0.0, 0.0, 1.0]
        vec = [*(st.vel_body / self.vmax), st.omega_body[2] / self.wmax, st.pos[2] / self.ceiling if self.air else 0.0, *vis,
               sum(ep.info["found"]) / len(ep.info["found"])]
        if not self.air:
            vec += list(obs.sensors["range"] / self.max_range)
        return np.clip(np.array(vec, float), -3.0, 3.0).astype(np.float32)

    # ---------------------------------------------------------------- scoring
    def step(self, obs, action, ep, t) -> StepResult:
        st, info = obs.state, ep.info
        terms = dict.fromkeys(self.rew, 0.0)
        for i, (tx, ty) in enumerate(info["targets"]):
            if info["found"][i] or tx is None:
                continue
            dx, dy = tx - st.pos[0], ty - st.pos[1]
            bearing = math.atan2(-math.sin(st.yaw) * dx + math.cos(st.yaw) * dy, math.cos(st.yaw) * dx + math.sin(st.yaw) * dy)
            if math.hypot(dx, dy) <= self.p["find_range"] and abs(bearing) <= self.fov / 2:
                info["found"][i] = True
                terms["found"] += self.rew["found"]
        done = all(info["found"])
        crash = None
        if st.collided and self.air:
            crash = "collision"
        elif self.air:
            roll, pitch, _ = euler(st.quat)
            if max(abs(roll), abs(pitch)) > math.radians(80) and st.pos[2] > 0.25:
                crash = "flipped"
            elif st.extra.get("on_ground") and info["prev_vz"] < -2.5:
                crash = "hard impact"
            elif abs(st.pos[0]) > self.wp["width"] / 2 or abs(st.pos[1]) > self.wp["depth"] / 2 or st.pos[2] > self.ceiling:
                crash = "out of bounds"
        elif st.collided:
            info["collisions"] += 1
            terms["collision"] = self.rew["collision"]
        info["prev_vz"] = float(st.vel[2])
        terms["time"] = self.rew["time"] * self.policy_dt
        terms["action"] = self.rew["action"] * float(np.mean(np.square(np.asarray(action, float))))
        if done:
            terms["done"] = self.rew["done"]
        if crash:
            terms["crash"] = self.rew["crash"]
        terminated = done or crash is not None
        return StepResult(sum(terms.values()), terminated, not terminated and t >= info["max_time"],
                          {"terms": terms, "success": done, "found": int(sum(info["found"])), "of": len(info["found"]), "crash": crash,
                           "collisions": info["collisions"]})

    def baseline(self):
        from ..control.search import SearchController
        return SearchController(self)

    def markers(self, ep):
        return [{"kind": "target", "pos": [tx, ty, 0.0], "found": bool(ep.info["found"][i])}
                for i, (tx, ty) in enumerate(ep.info["targets"]) if tx is not None]


TASKS.add(SearchTask)
