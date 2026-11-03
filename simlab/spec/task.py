"""TaskSpec: a scenario plus its objective as plain data. The named task plug-in interprets `world` and `params`."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

from .params import Issue, Param, apply_schema
from .vehicle import NAME_RE, SpecError

EPISODE = (
    Param("max_time", "float", 30.0, 0.1, 3600.0, "s", "Episode time limit"),
    Param("policy_hz", "float", 20.0, 1.0, 200.0, "Hz", "How often the policy acts"),
)
RANDOMIZE = (
    Param("mass", "float", 0.0, 0.0, 0.9, "", "Fractional spread of vehicle mass"),
    Param("motor_strength", "float", 0.0, 0.0, 0.9, "", "Fractional spread of actuator gain"),
    Param("friction", "float", 0.0, 0.0, 0.9, "", "Fractional spread of ground friction"),
    Param("wind", "float", 0.0, 0.0, 30.0, "m/s", "Peak steady wind speed (air vehicles)"),
    Param("sensor_noise", "float", 0.0, 0.0, 5.0, "", "Extra sensor noise: every noise level is multiplied by 1 + this"),
    Param("latency", "float", 0.0, 0.0, 1.0, "s", "Extra actuation delay"),
)
TOP_KEYS = ("name", "task", "description", "world", "params", "reward", "episode", "randomize", "labels")


@dataclass
class TaskSpec:
    name: str
    task: str                                    # name of the task plug-in (goto, waypoints, ...)
    description: str = ""
    world: dict = field(default_factory=dict)    # arena size, obstacles, goals/targets: read by the task plug-in
    params: dict = field(default_factory=dict)   # task-specific knobs (goal radius, hover altitude, ...)
    reward: dict = field(default_factory=dict)   # term name -> weight
    episode: dict = field(default_factory=dict)
    randomize: dict = field(default_factory=dict)
    labels: list = field(default_factory=list)   # object classes the camera should detect (perception training)

    @classmethod
    def parse(cls, d: Mapping) -> tuple["TaskSpec | None", list[Issue]]:
        d = dict(d or {})
        issues = [Issue("error", k, "unknown top-level key") for k in d if k not in TOP_KEYS]
        name, task = str(d.get("name", "")).strip(), str(d.get("task", "")).strip()
        if not NAME_RE.fullmatch(name):
            issues.append(Issue("error", "name", "needs a name made of letters, digits, _ . -"))
        if not task:
            issues.append(Issue("error", "task", "names the task plug-in to run"))
        episode, i1 = apply_schema(EPISODE, d.get("episode"), "episode")
        randomize, i2 = apply_schema(RANDOMIZE, d.get("randomize"), "randomize")
        issues += i1 + i2
        reward = {}
        for k, v in dict(d.get("reward") or {}).items():
            try:
                reward[str(k)] = Param(str(k), "float", 0.0).coerce(v)
            except ValueError as e:
                issues.append(Issue("error", f"reward.{k}", str(e)))
        labels = [str(x) for x in (d.get("labels") or [])]
        if len(set(labels)) != len(labels) or not all(re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in labels):
            issues.append(Issue("error", "labels", "must be unique names of letters, digits, _ -"))
        for key in ("world", "params"):
            if not isinstance(d.get(key) or {}, Mapping):
                issues.append(Issue("error", key, "must be a mapping"))
        if any(i.level == "error" for i in issues):
            return None, issues
        spec = cls(name, task, str(d.get("description", "")), dict(d.get("world") or {}), dict(d.get("params") or {}),
                   reward, episode, randomize, labels)
        return spec, issues

    @classmethod
    def from_dict(cls, d: Mapping) -> "TaskSpec":
        spec, issues = cls.parse(d)
        if spec is None:
            raise SpecError(issues)
        return spec

    @classmethod
    def load(cls, path: str | Path) -> "TaskSpec":
        with open(path, "r", encoding="utf-8-sig") as f:                       # -sig: tolerate a Windows BOM
            return cls.from_dict(yaml.safe_load(f) or {})

    def to_dict(self) -> dict:
        return {"name": self.name, "task": self.task, "description": self.description, "world": dict(self.world),
                "params": dict(self.params), "reward": dict(self.reward), "episode": dict(self.episode),
                "randomize": dict(self.randomize), "labels": list(self.labels)}

    def dump(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False)
