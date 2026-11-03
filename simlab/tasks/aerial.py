"""Tasks for multirotors: fly through waypoints (also hover-and-hold, takeoff) and land on a pad."""
from __future__ import annotations

import math

import numpy as np

from ..backends.base import Observation
from ..registry import TASKS
from ..spec.params import Issue, Param, apply_schema
from ..spec.vehicle import SpecError
from ..vehicles.multirotor import euler
from .base import Episode, Item, StepResult, Task, World

WORLD = (
    Param("width", "float", 24.0, 4.0, 2000.0, "m", "Flight area size along x"),
    Param("depth", "float", 24.0, 4.0, 2000.0, "m", "Flight area size along y"),
    Param("ceiling", "float", 15.0, 2.0, 500.0, "m", "Flying above this ends the episode"),
)
WP_PARAMS = (
    Param("n_points", "int", 3, 1, 12, "", "Waypoints to visit, in order"),
    Param("spread", "float", 5.0, 0.5, 200.0, "m", "Waypoints are placed within this distance of the origin"),
    Param("min_leg", "float", 2.0, 0.2, 100.0, "m", "Minimum distance between consecutive waypoints"),
    Param("alt_min", "float", 1.0, 0.3, 100.0, "m", "Lowest waypoint altitude"),
    Param("alt_max", "float", 3.0, 0.3, 100.0, "m", "Highest waypoint altitude"),
    Param("radius", "float", 0.0, 0.0, 20.0, "m", "Reaching distance; 0 = auto (0.8 x the vehicle's size, at least 0.3)"),
    Param("hold_time", "float", 0.0, 0.0, 600.0, "s", "Time to stay inside the last waypoint's radius"),
    Param("start", "enum", "air", doc="Begin hovering in the air, or sitting on the ground (takeoff first)", choices=("air", "ground")),
    Param("crash_speed", "float", 2.5, 0.2, 50.0, "m/s", "Hitting the ground faster than this is a crash"),
    Param("auto_time", "bool", True, doc="Episode limit from the route length and the vehicle's speed, instead of episode.max_time"),
)
LAND_PARAMS = (
    Param("spread", "float", 3.0, 0.0, 100.0, "m", "Start position offset from the pad"),
    Param("alt_min", "float", 2.0, 0.5, 100.0, "m"), Param("alt_max", "float", 4.0, 0.5, 100.0, "m"),
    Param("pad_radius", "float", 0.0, 0.0, 20.0, "m", "0 = auto (1.2 x the vehicle's size, at least 0.5)"),
    Param("land_speed", "float", 1.0, 0.1, 20.0, "m/s", "Touchdown faster than this is a hard landing"),
    Param("crash_speed", "float", 2.5, 0.2, 50.0, "m/s"),
    Param("auto_time", "bool", True, doc="Episode limit from the start altitude instead of episode.max_time"),
)
REWARD_WP = {"progress": 1.0, "reached": 5.0, "done": 10.0, "crash": -10.0, "time": -0.02, "action": -0.01}
REWARD_LAND = {"progress": 1.0, "landed": 20.0, "crash": -10.0, "time": -0.02, "action": -0.01}


def _heading(vec, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([c * vec[0] + s * vec[1], -s * vec[0] + c * vec[1], vec[2]])


class AerialTask(Task):
    kinds = ("multirotor",)
    PARAMS: tuple = ()
    REWARD: dict = {}

    def __init__(self, spec, vehicle):
        if vehicle.kind != "multirotor":
            raise SpecError([Issue("error", "kind", f"task {self.name!r} flies multirotors; {vehicle.name} is a {vehicle.kind}")])
        super().__init__(spec, vehicle)
        self.p, i1 = apply_schema(self.PARAMS, spec.params, "params")
        self.wp, i2 = apply_schema(WORLD, spec.world, "world")
        issues = i1 + i2 + [Issue("error", f"reward.{k}", f"unknown term; known: {sorted(self.REWARD)}")
                            for k in set(spec.reward) - set(self.REWARD)]
        issues += self.check_params()
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        b, v = vehicle.body, vehicle.params
        self.rew = {**self.REWARD, **spec.reward}
        self.size = max(b["length"], b["width"])
        self.vmax, self.vup, self.vdown = v["max_speed"], v["max_climb"], v["max_descent"]
        self.tilt = math.radians(v["max_tilt_deg"])
        self.ref = max(self.wp["width"], self.wp["depth"]) / 2
        self.ceiling = self.wp["ceiling"]

    def check_params(self) -> list[Issue]:
        return []

    level = 1.0

    def set_difficulty(self, level: float) -> None:
        """Easy: waypoints close to the start. Hard (the configured task): the full spread."""
        self.level = max(0.0, min(1.0, level))

    def _crash(self, st, ep) -> str | None:
        """Why this step ended the flight badly, or None."""
        roll, pitch, _ = euler(st.quat)
        if st.collided:
            return "collision"
        if st.extra.get("on_ground") and ep.info["prev_vz"] < -self.p["crash_speed"]:
            return "hard impact"
        if max(abs(roll), abs(pitch)) > math.radians(80) and st.pos[2] > 0.25:
            return "flipped"
        if abs(st.pos[0]) > self.wp["width"] / 2 or abs(st.pos[1]) > self.wp["depth"] / 2 or st.pos[2] > self.ceiling:
            return "out of bounds"
        if st.extra.get("battery_empty"):
            return "battery empty"
        return None


class WaypointsTask(AerialTask):
    name = "waypoints"
    title = "Fly through waypoints"
    description = "Visit a list of 3D waypoints in order; can also be used to hover in place or take off."
    PARAMS, REWARD = WP_PARAMS, REWARD_WP

    def check_params(self):
        out = []
        if self.p["alt_max"] < self.p["alt_min"]:
            out.append(Issue("error", "params.alt_max", "must be at least alt_min"))
        return out

    def __init__(self, spec, vehicle):
        super().__init__(spec, vehicle)
        self.radius = self.p["radius"] or max(0.3, 0.8 * self.size)
        n, leg = self.p["n_points"], self.p["spread"] * 1.5
        self.max_time = (n * leg / (0.35 * self.vmax) + self.p["hold_time"] + 12.0) if self.p["auto_time"] else spec.episode["max_time"]

    def build_world(self) -> World:
        items = [Item(f"wp{i}", "sphere", (self.radius,), (0, 0, -10.0), rest_z=1.0, rgba=(0.2, 0.85, 0.95, 0.3),
                      collide=False, movable=True, label="waypoint") for i in range(self.p["n_points"])]
        return World(items, (self.wp["width"], self.wp["depth"]))

    def reset(self, rng: np.random.Generator) -> Episode:
        p, h = self.p, self.vehicle.body["height"]
        sx, sy = rng.uniform(-1, 1, 2) * min(p["spread"], self.wp["width"] / 2 - 1) * 0.3
        sz = rng.uniform(p["alt_min"], p["alt_max"]) if p["start"] == "air" else h / 2
        wps, prev = [], np.array([sx, sy, sz])
        lim = min(p["spread"] * (0.4 + 0.6 * self.level), self.wp["width"] / 2 - 0.5, self.wp["depth"] / 2 - 0.5)
        for _ in range(p["n_points"]):
            for _try in range(60):
                cand = np.array([rng.uniform(-lim, lim), rng.uniform(-lim, lim), rng.uniform(p["alt_min"], p["alt_max"])])
                if np.linalg.norm(cand - prev) >= p["min_leg"]:
                    break
            wps.append(cand)
            prev = cand
        placements = {f"wp{i}": (w[0], w[1], 0.0, w[2]) for i, w in enumerate(wps)}
        spawn = (sx, sy, sz, rng.uniform(-math.pi, math.pi))
        return Episode(spawn, placements, {"wps": wps, "idx": 0, "prev_dist": float(np.linalg.norm(wps[0] - [sx, sy, sz])),
                                           "hold": 0.0, "max_time": self.max_time, "prev_vz": 0.0, "yaw0": spawn[3]})

    def observation_dim(self) -> int:
        return 17

    def observe(self, obs: Observation, ep: Episode) -> np.ndarray:
        st, wps = obs.state, ep.info["wps"]
        i = min(ep.info["idx"], len(wps) - 1)
        r0 = _heading(wps[i] - st.pos, st.yaw)
        r1 = _heading(wps[i + 1] - st.pos, st.yaw) if i + 1 < len(wps) else np.zeros(3)
        roll, pitch, _ = euler(st.quat)
        vh = _heading(st.vel, st.yaw)
        remaining = (len(wps) - ep.info["idx"]) / len(wps)
        vec = np.concatenate([r0 / self.ref, [np.linalg.norm(r0) / self.ref], vh / self.vmax, [roll / self.tilt, pitch / self.tilt],
                              st.omega_body / 6.0, [st.pos[2] / self.ceiling, remaining], r1 / self.ref])
        return np.clip(vec, -3.0, 3.0).astype(np.float32)

    def step(self, obs, action, ep, t) -> StepResult:
        st, info = obs.state, ep.info
        wps = info["wps"]
        dist = float(np.linalg.norm(wps[info["idx"]] - st.pos))
        a = np.asarray(action, float)
        terms = dict.fromkeys(self.rew, 0.0)
        terms["progress"] = self.rew["progress"] * (info["prev_dist"] - dist)
        terms["time"], terms["action"] = self.rew["time"] * self.policy_dt, self.rew["action"] * float(np.mean(a * a))
        done = False
        if dist < self.radius:
            if info["idx"] == len(wps) - 1:
                info["hold"] += self.policy_dt
                done = info["hold"] >= self.p["hold_time"]
            else:
                info["idx"] += 1
                terms["reached"] = self.rew["reached"]
                dist = float(np.linalg.norm(wps[info["idx"]] - st.pos))
        else:
            info["hold"] = 0.0
        info["prev_dist"] = dist
        crash = self._crash(st, ep)
        info["prev_vz"] = float(st.vel[2])
        if done:
            terms["done"] = self.rew["done"]
        if crash:
            terms["crash"] = self.rew["crash"]
        terminated = done or crash is not None
        return StepResult(sum(terms.values()), terminated, not terminated and t >= info["max_time"],
                          {"terms": terms, "success": done, "dist": dist, "waypoint": info["idx"], "crash": crash,
                           "collisions": int(crash == "collision")})

    def baseline(self):
        from ..control.aerial import WaypointController
        return WaypointController(self)

    def markers(self, ep):
        return [{"kind": "waypoint", "body": f"wp{i}", "pos": [float(x) for x in w], "r": self.radius, "active": i == ep.info["idx"]}
                for i, w in enumerate(ep.info["wps"])]


class LandTask(AerialTask):
    name = "land"
    title = "Land on the pad"
    description = "Descend from altitude and touch down gently on a marked pad."
    PARAMS, REWARD = LAND_PARAMS, REWARD_LAND

    def check_params(self):
        return [Issue("error", "params.alt_max", "must be at least alt_min")] if self.p["alt_max"] < self.p["alt_min"] else []

    def __init__(self, spec, vehicle):
        super().__init__(spec, vehicle)
        self.pad_r = self.p["pad_radius"] or max(0.5, 1.2 * self.size)
        self.max_time = (self.p["alt_max"] / 0.35 + self.p["spread"] / (0.3 * self.vmax) + 15.0) if self.p["auto_time"] else spec.episode["max_time"]

    def build_world(self) -> World:
        return World([Item("pad", "cylinder", (self.pad_r, 0.01), (0, 0, -10.0), rest_z=0.01, rgba=(0.95, 0.95, 0.4, 0.9),
                           collide=False, movable=True, label="pad")], (self.wp["width"], self.wp["depth"]))

    def reset(self, rng: np.random.Generator) -> Episode:
        p, lim = self.p, min(self.wp["width"], self.wp["depth"]) / 2 - 2.0
        px, py = rng.uniform(-1, 1, 2) * max(lim - p["spread"], 0.0)
        ang, off = rng.uniform(0, 2 * math.pi), rng.uniform(0.3, 1.0) * p["spread"]
        alt = rng.uniform(p["alt_min"], p["alt_max"])
        spawn = (px + off * math.cos(ang), py + off * math.sin(ang), alt, rng.uniform(-math.pi, math.pi))
        pad = np.array([px, py, 0.0])
        return Episode(spawn, {"pad": (px, py, 0.0)}, {"pad": pad, "prev_dist": float(np.linalg.norm(pad - spawn[:3])),
                                                       "max_time": self.max_time, "prev_vz": 0.0})

    def observation_dim(self) -> int:
        return 13

    def observe(self, obs, ep):
        st = obs.state
        r = _heading(ep.info["pad"] - st.pos, st.yaw)
        roll, pitch, _ = euler(st.quat)
        vec = np.concatenate([r / self.ref, [np.linalg.norm(r) / self.ref], _heading(st.vel, st.yaw) / self.vmax,
                              [roll / self.tilt, pitch / self.tilt], st.omega_body / 6.0, [st.pos[2] / self.ceiling]])
        return np.clip(vec, -3.0, 3.0).astype(np.float32)

    def step(self, obs, action, ep, t) -> StepResult:
        st, info = obs.state, ep.info
        dist = float(np.linalg.norm(info["pad"] - st.pos))
        a = np.asarray(action, float)
        terms = dict.fromkeys(self.rew, 0.0)
        terms["progress"] = self.rew["progress"] * (info["prev_dist"] - dist)
        terms["time"], terms["action"] = self.rew["time"] * self.policy_dt, self.rew["action"] * float(np.mean(a * a))
        info["prev_dist"] = dist
        landed = crash = None
        if st.extra.get("on_ground") and st.pos[2] < 0.5 * self.vehicle.body["height"] + 0.15:
            on_pad = math.hypot(*(st.pos[:2] - info["pad"][:2])) <= self.pad_r
            gentle = abs(info["prev_vz"]) <= self.p["land_speed"]
            roll, pitch, _ = euler(st.quat)
            if on_pad and gentle and max(abs(roll), abs(pitch)) < math.radians(20):
                landed = True
            else:
                crash = "hard landing" if on_pad else "missed the pad"
        crash = crash or self._crash(st, ep)
        info["prev_vz"] = float(st.vel[2])
        if landed:
            terms["landed"] = self.rew["landed"]
        if crash:
            terms["crash"] = self.rew["crash"]
        terminated = bool(landed) or crash is not None
        return StepResult(sum(terms.values()), terminated, not terminated and t >= info["max_time"],
                          {"terms": terms, "success": bool(landed), "dist": dist, "crash": crash,
                           "collisions": int(crash == "collision")})

    def baseline(self):
        from ..control.aerial import LandController
        return LandController(self)

    def markers(self, ep):
        return [{"kind": "pad", "pos": [float(x) for x in ep.info["pad"]], "r": self.pad_r}]


TASKS.add(WaypointsTask)
TASKS.add(LandTask)
