"""bag_mission: bags and a box on a flat floor, seen only through the rover's own camera.

The task builds a named flat-floor environment, places one object per entry of its layout (each object is a *target* from
`simlab.missions.targets`: a bag, a box ... with a real size and a colour learned from photos), and scores a run. It does not
drive: a mission (`simlab.missions`) does, from camera frames and the gyro only, through `MissionSim`. The mission's progress
reaches the task as `episode.info["mission_status"]` ("running" | "done" | "failed"), so any mission can be scored the same way.

World data (all optional; the named environment fills in whatever is missing):

    world:
      environment: tile_floor            # see ENVIRONMENTS
      objects:                           # what is on the floor, in metres
        - {target: bag_a, at: [1.2, 0.0]}
        - {target: bag_b, at: [0.5, 1.5], yaw_deg: 30}
        - {target: box,   at: [-1.0, 1.0]}
        - {target: bag_a, at: [2.0, -1.0], path: [[2.0, 1.0]], speed: 0.05}      # a bag that is carried along a path
      spawn: [0.0, 0.0, 0.0]             # x, y, heading in degrees
"""
from __future__ import annotations

import math

import numpy as np

from ..backends.base import Observation
from ..missions.targets import TargetError, TargetRegistry
from ..registry import TASKS
from ..spec.params import Issue, Param, apply_schema
from ..spec.vehicle import SpecError
from .base import Episode, Item, Requirement, StepResult, Task, World

ENVIRONMENTS: dict[str, dict] = {
    "tile_floor": {
        "description": "A tiled floor in a 5 x 5 m room with walls: two bags and a box.",
        "width": 5.0, "depth": 5.0, "spawn": (0.0, 0.0, 0.0),
        "objects": [{"target": "bag_a", "at": [1.2, 0.0]}, {"target": "bag_b", "at": [0.5, 1.5]}, {"target": "box", "at": [-1.0, 1.0]}],
    },
    "open_floor": {
        "description": "A large open floor, 8 x 8 m: one bag straight ahead, for following and laps.",
        "width": 8.0, "depth": 8.0, "spawn": (0.0, 0.0, 0.0),
        "objects": [{"target": "bag_a", "at": [1.5, 0.0]}],
    },
    "small_room": {
        "description": "A 3.5 x 3.5 m room: a bag and the goal box.",
        "width": 3.5, "depth": 3.5, "spawn": (-1.0, -1.0, 0.0),
        "objects": [{"target": "bag_a", "at": [0.5, -1.0]}, {"target": "box", "at": [0.5, 1.0]}],
    },
}
WORLD = (
    Param("environment", "enum", "tile_floor", doc="Named floor and default layout", choices=tuple(ENVIRONMENTS)),
    Param("width", "float", 0.0, 0.0, 200.0, "m", "Floor size along x; 0 = the environment's"),
    Param("depth", "float", 0.0, 0.0, 200.0, "m", "Floor size along y; 0 = the environment's"),
)
PARAMS = (Param("jitter_cm", "float", 0.0, 0.0, 100.0, "cm", "Random shift of every object each episode"),)
REWARD = {"done": 10.0, "failed": -5.0, "time": -0.02, "collision": -0.5}
EXTRA_KEYS = ("objects", "spawn")
WALL = 0.15
OBS_SLOTS = 4                    # privileged observation: the first four objects' position relative to the rover


class BagMissionTask(Task):
    name = "bag_mission"
    title = "Bags and box"
    description = "Follow, circle or visit bags on a flat floor and stop at the goal box, using only the onboard camera and gyro."
    kinds = ("ground_wheeled",)
    needs_camera = True

    def __init__(self, spec, vehicle):
        if vehicle.kind != "ground_wheeled":
            raise SpecError([Issue("error", "kind", f"task 'bag_mission' drives ground vehicles; {vehicle.name} is a {vehicle.kind}")])
        super().__init__(spec, vehicle)
        world = {k: v for k, v in spec.world.items() if k not in EXTRA_KEYS}
        self.wp, i1 = apply_schema(WORLD, world, "world")
        self.p, i2 = apply_schema(PARAMS, spec.params, "params")
        issues = i1 + i2 + [Issue("error", f"reward.{k}", f"unknown term; known: {sorted(REWARD)}") for k in set(spec.reward) - set(REWARD)]
        if vehicle.sensor("camera") is None:
            issues.append(Issue("error", "sensors", f"{vehicle.name} has no camera: this task is about seeing the bags"))
        env = ENVIRONMENTS[self.wp["environment"]]
        self.env = env
        self.width, self.depth = self.wp["width"] or env["width"], self.wp["depth"] or env["depth"]
        self.layout = [dict(o) for o in (spec.world.get("objects") if spec.world.get("objects") is not None else env["objects"])]
        self.spawn = tuple(spec.world.get("spawn") or env["spawn"])
        self.targets = TargetRegistry()
        self._look: list = []
        for i, o in enumerate(self.layout):
            try:
                tgt = self.targets.get(str(o.get("target")))
                at = o.get("at")
                if not (isinstance(at, (list, tuple)) and len(at) == 2) or abs(at[0]) > self.width / 2 or abs(at[1]) > self.depth / 2:
                    issues.append(Issue("error", f"world.objects[{i}].at", f"needs [x, y] inside the {self.width:g} x {self.depth:g} m floor"))
                self._look.append(tgt)
            except (KeyError, TargetError) as e:
                issues.append(Issue("error", f"world.objects[{i}].target", str(e).strip("'\"")))
        if len(self.spawn) != 3:
            issues.append(Issue("error", "world.spawn", "needs [x, y, heading in degrees]"))
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        self.rew = {**REWARD, **spec.reward}
        self.vmax, self.wmax = vehicle.params["max_speed"], vehicle.params["max_yaw_rate"]
        self.max_time = spec.episode["max_time"]
        self.ref = max(self.width, self.depth)

    def requirements(self) -> list[Requirement]:
        out = super().requirements()
        out = [r for r in out if not (r.kind == "sensor" and r.params.get("type") == "camera")]
        out.append(Requirement("sensor", "a camera that can see bags and a box (96 x 96 is enough at short range)",
                               {"type": "camera", "ideal": False, "optional": False, "min_fps": 5.0, "min_width": 96}))
        out.append(Requirement("sensor", "a gyro or IMU for heading and lap counting", {"type": "imu", "ideal": False, "optional": True}))
        return out

    # ---------------------------------------------------------------- world
    def build_world(self) -> World:
        w, d = self.width, self.depth
        items = [Item(n, "box", (hx, hy, 0.3), (x, y, 0.3), rgba=(0.42, 0.44, 0.5, 1)) for n, (x, y, hx, hy) in {
            "wall_n": (0, d / 2 + WALL, w / 2 + 0.3, WALL), "wall_s": (0, -d / 2 - WALL, w / 2 + 0.3, WALL),
            "wall_e": (w / 2 + WALL, 0, WALL, d / 2), "wall_w": (-w / 2 - WALL, 0, WALL, d / 2)}.items()]
        for i, tgt in enumerate(self._look):
            shape, rgba, size = tgt.look()
            hz = size[2] if shape == "box" else size[1]
            items.append(Item(f"obj{i}", shape, size, (0, 0, -10.0), rest_z=hz, rgba=rgba, movable=True, label=tgt.id))
        return World(items, (w, d))

    def reset(self, rng: np.random.Generator) -> Episode:
        j = self.p["jitter_cm"] / 100.0
        objs, placements = [], {}
        for i, (o, tgt) in enumerate(zip(self.layout, self._look)):
            x, y = (float(c) + (rng.uniform(-j, j) if j else 0.0) for c in o["at"])
            yaw = math.radians(float(o.get("yaw_deg", 0.0)))
            path = [(x, y)] + [(float(p[0]), float(p[1])) for p in o.get("path", [])]
            objs.append({"name": f"obj{i}", "target": tgt.id, "pos": (x, y), "yaw": yaw, "path": path, "speed": float(o.get("speed", 0.05)),
                         "footprint": tgt.footprint_m})
            placements[f"obj{i}"] = (x, y, yaw)
        sx, sy, sh = self.spawn
        return Episode((float(sx), float(sy), 0.0, math.radians(float(sh))), placements,
                       {"objects": objs, "max_time": self.max_time, "mission_status": "running", "collisions": 0})

    # ---------------------------------------------------------------- objects that move
    @staticmethod
    def object_pos(obj: dict, t: float) -> tuple[float, float]:
        """Where an object is at time `t`: it walks its path at its speed and then stays at the end."""
        path, left = obj["path"], obj["speed"] * t
        for a, b in zip(path, path[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if left <= seg:
                f = left / seg if seg else 1.0
                return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])
            left -= seg
        return path[-1]

    # ---------------------------------------------------------------- policy interface (ground truth: for scoring and RL; a mission uses the camera)
    def observation_dim(self) -> int:
        return 3 * OBS_SLOTS + 2

    def observe(self, obs: Observation, ep: Episode) -> np.ndarray:
        st = obs.state
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        vec = []
        for k in range(OBS_SLOTS):
            if k < len(ep.info["objects"]):
                ox, oy = ep.info["objects"][k]["pos"]
                dx, dy = ox - st.pos[0], oy - st.pos[1]
                vec += [(c * dx + s * dy) / self.ref, (-s * dx + c * dy) / self.ref, min(math.hypot(dx, dy) / self.ref, 2.0)]
            else:
                vec += [0.0, 0.0, 2.0]
        vec += [st.vel_body[0] / self.vmax, st.omega_body[2] / self.wmax]
        return np.clip(np.array(vec, float), -2.0, 2.0).astype(np.float32)

    def step(self, obs, action, ep, t) -> StepResult:
        st, info = obs.state, ep.info
        if st.collided:
            info["collisions"] += 1
        status = info["mission_status"]
        done, failed = status == "done", status == "failed"
        terms = {"done": self.rew["done"] if done else 0.0, "failed": self.rew["failed"] if failed else 0.0,
                 "time": self.rew["time"] * self.policy_dt, "collision": self.rew["collision"] if st.collided else 0.0}
        dists = {f"dist_{o['target']}": float(np.hypot(o["pos"][0] - st.pos[0], o["pos"][1] - st.pos[1])) for o in info["objects"]}
        terminated = done or failed
        return StepResult(sum(terms.values()), terminated, not terminated and t >= info["max_time"],
                          {"terms": terms, "success": done, "status": status, "collisions": info["collisions"], **dists})

    def markers(self, ep):
        return [{"kind": "target", "pos": [o["pos"][0], o["pos"][1], 0.0], "found": False} for o in ep.info["objects"]]


TASKS.add(BagMissionTask)
