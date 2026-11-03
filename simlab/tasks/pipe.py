"""pipe_inspect: drive a ground vehicle through a sewer network in MuJoCo, fast enough to train on.

The world is built from a map in maps/ (textured pipes and chambers, planted cracks and leaks that carry a perception label,
collision boxes behind the walls, and the vehicle's own lamp as the only light). Webots (task sewer_drive) shows the same maps
with higher visual fidelity but runs one simulation at a time; this task runs many in parallel.

Episode: start at the map's entry, drive the route to a goal post. Observation, reward and success follow `goto`, with the next
waypoint of the route in place of the goal.
"""
from __future__ import annotations

import math

import numpy as np

from ..backends.base import Observation
from ..registry import TASKS
from ..spec.params import Issue, Param, apply_schema
from ..spec.sensors import SensorSpec
from ..spec.vehicle import SpecError
from .base import Episode, Requirement, StepResult, Task, World
from .sewer import map_fit

PARAMS = (
    Param("map", "str", "straight_t", doc="Name of a map in maps/ (straight_t, star_five_goals, large_loop, or your own .yaml)"),
    Param("goal", "str", "", doc="Goal post id to drive to; empty = the first goal of the map"),
    Param("defect_seed", "int", -1, -1, 1000000, doc="-1 keeps the map's own defects; any other number plants a different random set"),
    Param("rays", "int", 5, 1, 64, doc="Range rays across the front 180 degrees"),
    Param("terminate_on_collision", "bool", False, doc="End the episode on the first contact"),
    Param("auto_time", "bool", True, doc="Episode limit = 3 x route length / top speed + 15 s instead of episode.max_time"),
)
REWARD = {"progress": 1.0, "reached": 10.0, "collision": -0.5, "time": -0.02, "action": -0.01}
OBS_FIXED = 8
GOAL_R = 0.3


def _reach(m, route: list[str], leg: int) -> float:
    """How close to a node counts as having reached it (the same rule the sewer path follower uses)."""
    node = m.nodes[route[leg + 1]]
    return max(0.3, 0.5 * m.chambers[node.id].apothem) if leg + 1 < len(route) - 1 else 0.28


class PipeInspectTask(Task):
    name = "pipe_inspect"
    title = "Drive through a sewer (MuJoCo, fast)"
    description = "Drive the route to a goal post through a pipe network. Textured walls, planted cracks and leaks, lit only by your own lamp."
    kinds = ("ground_wheeled",)
    needs_camera = True

    def __init__(self, spec, vehicle):
        if vehicle.kind != "ground_wheeled":
            raise SpecError([Issue("error", "kind", f"task 'pipe_inspect' drives ground vehicles; {vehicle.name} is a {vehicle.kind}")])
        super().__init__(spec, vehicle)
        self.p, issues = apply_schema(PARAMS, spec.params, "params")
        issues += [Issue("error", f"reward.{k}", f"unknown term; known: {sorted(REWARD)}") for k in set(spec.reward) - set(REWARD)]
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        from sewerbot.core.mapspec import load_map
        from tools.gen_world import ROOT
        path = ROOT / "maps" / f"{self.p['map']}.yaml"
        if not path.is_file():
            raise SpecError([Issue("error", "params.map", f"no map file maps/{self.p['map']}.yaml")])
        seed = self.p["defect_seed"]
        self.map = load_map(path, None if seed < 0 else seed, None)
        goals = self.map.goals
        gid = self.p["goal"] or (next(iter(goals)) if goals else "")
        if gid not in goals:
            raise SpecError([Issue("error", "params.goal", f"map {self.map.name!r} has no goal {gid!r}; goals: {sorted(goals)}")])
        self.goal_id, self.goal_node = gid, goals[gid].node
        self.route = self.map.route(self.map.start_node, self.goal_node)
        self.pts = np.array([self.map.nodes[n].pos for n in self.route], float)
        self.legs = np.hypot(*(self.pts[1:] - self.pts[:-1]).T) if len(self.pts) > 1 else np.zeros(0)
        self.rew = {**REWARD, **spec.reward}
        self.vmax, self.wmax = vehicle.params["max_speed"], vehicle.params["max_yaw_rate"]
        self.ref = max(float(self.legs.max()) if len(self.legs) else 1.0, 1.0)
        self.max_range = 4.0
        total = float(self.legs.sum())
        self.max_time = (3.0 * total / self.vmax + 15.0) if self.p["auto_time"] else spec.episode["max_time"]
        self._world = None
        self.profile = None

    # ---------------------------------------------------------------- world
    def required_sensors(self):
        if self.vehicle.sensor("rangefinder", "range"):
            return []
        return [SensorSpec("rangefinder", "range", {"rays": self.p["rays"], "fov_deg": 180.0, "max_range": self.max_range, "direction": "forward",
                                                      "noise": 0.0, "rate_hz": 20.0})]

    def requirements(self) -> list[Requirement]:
        out = [Requirement("sensor", "a rangefinder (the simulator adds an ideal one for you)", {"type": "rangefinder", "ideal": True}),
               Requirement("sensor", "a camera to find cracks and leaks", {"type": "camera", "ideal": False, "min_fps": 5.0, "min_width": 320}),
               Requirement("light", "a lamp: the pipes are dark", {}),
               Requirement("endurance", f"run for the whole episode ({self.max_time:.0f} s)", {"seconds": float(self.max_time)})]
        fit = map_fit(self.vehicle, self.p["map"])
        if fit:
            out.append(Requirement("fit", f"fit the pipes of map {self.p['map']}", fit))
        return out

    def build_world(self) -> World:
        from ..world.pipe_geometry import build_pipe_world
        self._world = pw = build_pipe_world(self.map)
        x0, y0, x1, y1 = pw.bounds
        return World(list(pw.items), (x1 - x0, y1 - y0), ground_friction=1.0, theme="pipe", meshes=list(pw.meshes), assets=dict(pw.assets))

    @property
    def defects(self) -> list[dict]:
        """Ground truth of the planted defects (valid after build_world)."""
        return self._world.defects if self._world else []

    def reset(self, rng: np.random.Generator) -> Episode:
        x, y, yaw = self._world.start if self._world else (*self.map.nodes[self.map.start_node].pos, 0.0)
        return Episode((x, y, 0.0, yaw), {}, {"max_time": self.max_time, "leg": 0, "collisions": 0, "route": list(self.route), "goal": self.goal_id})

    # ---------------------------------------------------------------- policy interface
    def _advance(self, ep: Episode, p: np.ndarray) -> None:
        leg = ep.info["leg"]
        while leg < len(self.pts) - 1 and math.hypot(*(p - self.pts[leg + 1])) < _reach(self.map, self.route, leg):
            leg += 1
        ep.info["leg"] = leg

    def _remaining(self, ep: Episode, p: np.ndarray) -> float:
        leg = min(ep.info["leg"], len(self.pts) - 2)
        return float(math.hypot(*(p - self.pts[leg + 1])) + self.legs[leg + 1:].sum()) if len(self.pts) > 1 else 0.0

    def observation_dim(self) -> int:
        return OBS_FIXED + self.p["rays"]

    def observe(self, obs: Observation, ep: Episode) -> np.ndarray:
        st = obs.state
        p = np.array([st.pos[0], st.pos[1]])
        self._advance(ep, p)
        tgt = self.pts[min(ep.info["leg"] + 1, len(self.pts) - 1)]
        dx, dy = tgt[0] - p[0], tgt[1] - p[1]
        c, s = math.cos(st.yaw), math.sin(st.yaw)
        bx, by = c * dx + s * dy, -s * dx + c * dy
        dist, bearing = math.hypot(dx, dy), math.atan2(by, bx)
        vec = np.concatenate([[bx / self.ref, by / self.ref, min(dist / self.ref, 2.0), math.cos(bearing), math.sin(bearing),
                               st.vel_body[0] / self.vmax, st.vel_body[1] / self.vmax, st.omega_body[2] / self.wmax], obs.sensors["range"] / self.max_range])
        return np.clip(vec, -2.0, 2.0).astype(np.float32)

    def step(self, obs: Observation, action, ep: Episode, t: float) -> StepResult:
        st = obs.state
        p = np.array([st.pos[0], st.pos[1]])
        self._advance(ep, p)
        remaining = self._remaining(ep, p)
        reached = ep.info["leg"] >= len(self.pts) - 1 or (len(self.pts) > 1 and math.hypot(*(p - self.pts[-1])) < GOAL_R)
        if st.collided:
            ep.info["collisions"] += 1
        a = np.asarray(action, float)
        prev = ep.info.get("prev_remaining", remaining)
        terms = {"progress": self.rew["progress"] * (prev - remaining), "reached": self.rew["reached"] if reached else 0.0,
                 "collision": self.rew["collision"] if st.collided else 0.0, "time": self.rew["time"] * self.policy_dt,
                 "action": self.rew["action"] * float(np.mean(a * a))}
        ep.info["prev_remaining"] = remaining
        terminated = reached or (st.collided and self.p["terminate_on_collision"])
        truncated = not terminated and t >= ep.info["max_time"]
        return StepResult(sum(terms.values()), terminated, truncated, {"terms": terms, "success": reached, "dist": remaining, "collisions": ep.info["collisions"]})

    def capture_groups(self) -> list[str]:
        return sorted(d["id"] for d in self.defects) + [f"clear_{e}" for e in self.map.edges]

    def viewpoints(self, group: str, rng: np.random.Generator, n: int) -> list[tuple[float, float, float, float]]:
        """Poses inside a pipe: facing a planted defect from 0.6-2.8 m away (a quarter of them from the other side), or anywhere along a
        pipe (the 'clear_<pipe>' groups, which teach the detector what a pipe without a defect looks like)."""
        m = self.map
        poses = []
        d = next((x for x in self.defects if x["id"] == group), None)
        e = m.edges[d["edge"] if d else group.removeprefix("clear_")]
        o, f, rt = m.edge_frame(e.id)
        heading = math.atan2(f[1], f[0])
        half = 0.25 * e.section.floor_width
        for _ in range(n):
            back = d is not None and rng.random() < 0.25
            if d:
                s = d["s"] + (rng.uniform(0.6, 2.8) if back else -rng.uniform(0.6, 2.8))
            else:
                s = rng.uniform(e.s_min + 0.5, e.s_max - 0.5)
            s = float(np.clip(s, e.s_min + 0.3, e.s_max - 0.3))
            p = o + f * s + rt * rng.uniform(-half, half)
            poses.append((float(p[0]), float(p[1]), 0.0, float(heading + (math.pi if back else 0.0) + rng.normal(0, 0.12))))
        return poses

    def baseline(self):
        from ..control.pipe import PipeController
        return PipeController(self)

    def markers(self, ep):
        gx, gy = self.pts[-1]
        return [{"kind": "goal", "pos": [float(gx), float(gy), 0.0], "r": GOAL_R}]


TASKS.add(PipeInspectTask)
