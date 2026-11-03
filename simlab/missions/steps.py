"""A mission as data: a list of steps from a closed vocabulary, checked like every other spec in simlab (typed, ranged, no unknown keys).

    {"name": "bag to bag to box", "steps": [
        {"op": "go_to", "target": "bag_a", "stop_at_cm": 30},
        {"op": "go_to", "target": "bag_b", "stop_at_cm": 30},
        {"op": "stop_at", "target": "box", "within_cm": 35}]}

The vocabulary is `STEP_OPS`; a chat request or a planner can only produce steps that this table accepts. Every step carries
a time limit and a path-length limit (0 = a sensible default derived from the step; see `Step.limits`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

from ..spec.params import Issue, Param, apply_schema

LIMITS = (
    Param("max_time_s", "float", 0.0, 0.0, 3600.0, "s", "Give up when the step takes longer than this (0 = automatic)"),
    Param("max_distance_cm", "float", 0.0, 0.0, 20000.0, "cm", "Give up when the rover has driven farther than this during the step (0 = automatic)"),
)
TARGET = Param("target", "str", "", doc="Id of a target (config/targets/<id>/ or a built-in stand-in)")

STEP_OPS: dict[str, tuple[str, tuple[Param, ...]]] = {
    "go_to": ("Drive up to the target and stop in front of it.",
              (TARGET, Param("stop_at_cm", "float", 30.0, 15.0, 300.0, "cm", "Stop when the camera is this far from the target's near surface"), *LIMITS)),
    "follow": ("Keep a gap behind a target that moves, for a while.",
               (TARGET, Param("gap_cm", "float", 40.0, 15.0, 300.0, "cm", "Distance to keep from the target's near surface"),
                Param("duration_s", "float", 20.0, 2.0, 600.0, "s", "How long to keep following"), *LIMITS)),
    "orbit": ("Circle the target at a fixed distance from its centre; laps are counted from the gyro heading (360 degrees = 1 lap).",
              (TARGET, Param("radius_cm", "float", 40.0, 20.0, 300.0, "cm", "Distance from the target's centre to the rover"),
               Param("laps", "float", 1.0, 0.25, 10.0, "", "Laps to run"),
               Param("direction", "enum", "ccw", doc="ccw keeps the target on the rover's left", choices=("ccw", "cw")), *LIMITS)),
    "stop_at": ("End the mission standing still in front of the goal (must be the last step).",
                (TARGET, Param("within_cm", "float", 35.0, 15.0, 300.0, "cm", "The goal must be seen this close or closer"), *LIMITS)),
}
MAX_STEPS = 12


class MissionError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues if i.level == "error") or "invalid mission")


@dataclass(frozen=True)
class Step:
    op: str
    args: Mapping = field(default_factory=dict)       # every parameter of the op, defaults filled in

    def __getattr__(self, name: str):
        try:
            return self.__dict__["args"][name]
        except KeyError:
            raise AttributeError(name) from None

    def to_dict(self) -> dict:
        return {"op": self.op, **self.args}

    def limits(self) -> tuple[float, float]:
        """(seconds, metres) this step may use: the stated limits, or defaults worked out from the step."""
        a = self.args
        if self.op == "orbit":
            auto_t = 120.0 + 130.0 * a["laps"]
            auto_d = 3.0 * (2 * math.pi * a["radius_cm"] / 100.0) * a["laps"] + 3.0
        elif self.op == "follow":
            auto_t, auto_d = a["duration_s"] + 40.0, 8.0
        else:
            auto_t, auto_d = 120.0, 6.0
        return (a["max_time_s"] or auto_t), (a["max_distance_cm"] / 100.0 or auto_d)


def parse_step(raw: Mapping, where: str = "step") -> tuple[Step | None, list[Issue]]:
    raw = dict(raw or {})
    op = raw.pop("op", None)
    if op not in STEP_OPS:
        return None, [Issue("error", f"{where}.op", f"unknown step {op!r}; the vocabulary is {sorted(STEP_OPS)}")]
    args, issues = apply_schema(STEP_OPS[op][1], raw, where)
    if not args.get("target"):
        issues.append(Issue("error", f"{where}.target", "needs the id of a target"))
    return (None if any(i.level == "error" for i in issues) else Step(op, args)), issues


def step(op: str, **args) -> Step:
    """Build one validated step: `step("go_to", target="bag_a", stop_at_cm=30)`."""
    s, issues = parse_step({"op": op, **args})
    if s is None:
        raise MissionError(issues)
    return s


@dataclass(frozen=True)
class Mission:
    name: str
    steps: tuple[Step, ...]

    # ---- data in / out
    @classmethod
    def parse(cls, d: Mapping, registry=None) -> tuple["Mission | None", list[Issue]]:
        """Never raises: (mission, issues). With a target registry the target ids and roles are checked too."""
        issues: list[Issue] = []
        if not isinstance(d, Mapping):
            return None, [Issue("error", "mission", "must be a mapping with a name and a list of steps")]
        extra = set(d) - {"name", "steps"}
        issues += [Issue("error", k, "unknown top-level key; a mission has only name and steps") for k in sorted(extra)]
        raw = d.get("steps")
        if not isinstance(raw, (list, tuple)) or not raw:
            return None, issues + [Issue("error", "steps", "needs a list with at least one step")]
        if len(raw) > MAX_STEPS:
            issues.append(Issue("error", "steps", f"at most {MAX_STEPS} steps"))
        steps = []
        for i, r in enumerate(raw):
            s, iss = parse_step(r if isinstance(r, Mapping) else {}, f"steps[{i}]")
            issues += iss
            if s is not None:
                steps.append(s)
        for i, s in enumerate(steps):
            if s.op == "stop_at" and i != len(steps) - 1:
                issues.append(Issue("error", f"steps[{i}]", "stop_at ends the mission, so it must be the last step"))
        if registry is not None:
            for i, s in enumerate(steps):
                if s.target not in registry:
                    issues.append(Issue("error", f"steps[{i}].target", f"no target {s.target!r}; known: {registry.ids()}"))
                    continue
                role = registry.get(s.target).role
                if s.op == "stop_at" and role != "goal":
                    issues.append(Issue("warn", f"steps[{i}].target", f"{s.target!r} is a {role}, not a goal"))
                if s.op == "follow" and role == "goal":
                    issues.append(Issue("warn", f"steps[{i}].target", f"{s.target!r} is a goal object: following it is unusual"))
        if any(i.level == "error" for i in issues):
            return None, issues
        return cls(str(d.get("name") or "mission"), tuple(steps)), issues

    @classmethod
    def from_dict(cls, d: Mapping, registry=None) -> "Mission":
        m, issues = cls.parse(d, registry)
        if m is None:
            raise MissionError(issues)
        return m

    def to_dict(self) -> dict:
        return {"name": self.name, "steps": [s.to_dict() for s in self.steps]}

    def targets(self) -> list[str]:
        return list(dict.fromkeys(s.target for s in self.steps))

    def describe(self) -> list[str]:
        """One plain-language line per step."""
        out = []
        for s in self.steps:
            a = s.args
            out.append({"go_to": f"go to {s.target} and stop {a.get('stop_at_cm', 0):g} cm from it",
                        "follow": f"follow {s.target} at a {a.get('gap_cm', 0):g} cm gap for {a.get('duration_s', 0):g} s",
                        "orbit": f"circle {s.target} at {a.get('radius_cm', 0):g} cm for {a.get('laps', 0):g} lap(s), {a.get('direction')}",
                        "stop_at": f"stop at {s.target} (within {a.get('within_cm', 0):g} cm)"}[s.op])
        return out
