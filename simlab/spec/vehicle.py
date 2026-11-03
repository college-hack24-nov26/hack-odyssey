"""VehicleSpec: a machine described as plain data (YAML/JSON), checked by its kind's schema."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..registry import KINDS
from .caps import Caps
from .commands import CommandSpace
from .params import Issue, Param, apply_schema
from .sensors import SENSOR_SCHEMAS, SensorSpec

BODY_SCHEMA = (
    Param("mass", "float", 1.0, 0.001, 5000.0, "kg", "Total mass including payload and battery"),
    Param("length", "float", 0.3, 0.005, 50.0, "m", "Envelope along the forward axis (x)"),
    Param("width", "float", 0.2, 0.005, 50.0, "m", "Envelope across the vehicle (y)"),
    Param("height", "float", 0.1, 0.005, 50.0, "m", "Envelope height (z)"),
)
BODY_KEYS = tuple(p.name for p in BODY_SCHEMA)
TOP_KEYS = ("name", "kind", "description", "body", "params", "sensors", "default_space")
NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")


def body_schema(kind) -> tuple[Param, ...]:
    """The common body schema with the kind's own defaults (a quadrotor and a crawler differ in size and mass)."""
    d = kind.body_defaults
    return tuple(Param(p.name, p.type, d.get(p.name, p.default), p.min, p.max, p.unit, p.doc) for p in BODY_SCHEMA)


def _parse_sensors(raw: Any) -> tuple[list[SensorSpec], list[Issue]]:
    sensors, issues, seen = [], [], set()
    if not isinstance(raw, (list, tuple)):
        return sensors, [Issue("error", "sensors", "must be a list")]
    for i, item in enumerate(raw):
        where = f"sensors[{i}]"
        if not isinstance(item, Mapping):
            issues.append(Issue("error", where, "must be a mapping with 'type' and 'name'"))
            continue
        item = dict(item)
        typ, name = item.pop("type", None), str(item.pop("name", ""))
        if typ not in SENSOR_SCHEMAS:
            issues.append(Issue("error", f"{where}.type", f"unknown sensor type {typ!r}; known: {sorted(SENSOR_SCHEMAS)}"))
            continue
        if not name or not NAME_RE.fullmatch(name):
            issues.append(Issue("error", f"{where}.name", "needs a name made of letters, digits, _ . -"))
            continue
        if name in seen:
            issues.append(Issue("error", f"{where}.name", f"duplicate sensor name {name!r}"))
            continue
        seen.add(name)
        opts, iss = apply_schema(SENSOR_SCHEMAS[typ], item, where)
        issues += iss
        sensors.append(SensorSpec(typ, name, opts))
    return sensors, issues


class SpecError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues if i.level == "error") or "invalid spec")


@dataclass
class VehicleSpec:
    name: str
    kind: str
    description: str = ""
    body: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    sensors: list[SensorSpec] = field(default_factory=list)
    default_space: str = ""

    # ---- construction ---------------------------------------------------------
    @classmethod
    def parse(cls, d: Mapping) -> tuple["VehicleSpec | None", list[Issue]]:
        """Never raises: returns (spec, issues); spec is None when there are errors."""
        d = dict(d or {})
        issues = [Issue("error", k, "unknown top-level key") for k in d if k not in TOP_KEYS]
        name = str(d.get("name", "")).strip()
        if not NAME_RE.fullmatch(name):
            issues.append(Issue("error", "name", "needs a name made of letters, digits, _ . -"))
        kind_name = d.get("kind")
        if kind_name not in KINDS:
            issues.append(Issue("error", "kind", f"unknown vehicle kind {kind_name!r}; known: {KINDS.names()}"))
            return None, issues
        kind = KINDS.get(kind_name)
        body, i1 = apply_schema(body_schema(kind), d.get("body"), "body")
        params, i2 = apply_schema(kind.schema, d.get("params"), "params")
        sensors, i3 = _parse_sensors(d.get("sensors") or [])
        issues += i1 + i2 + i3
        spec = cls(name, kind_name, str(d.get("description", "")), body, params, sensors,
                   str(d.get("default_space") or kind.default_space))
        if not any(i.level == "error" for i in issues):
            if spec.default_space not in spec.spaces():
                issues.append(Issue("error", "default_space",
                                    f"{spec.default_space!r} is not a command space of {kind_name}; "
                                    f"has {list(spec.spaces())}"))
        errors = any(i.level == "error" for i in issues)
        return (None if errors else spec), issues

    @classmethod
    def from_dict(cls, d: Mapping) -> "VehicleSpec":
        spec, issues = cls.parse(d)
        if spec is None:
            raise SpecError(issues)
        return spec

    @classmethod
    def load(cls, path: str | Path) -> "VehicleSpec":
        with open(path, "r", encoding="utf-8-sig") as f:                       # -sig: tolerate a Windows BOM
            return cls.from_dict(yaml.safe_load(f) or {})

    def dump(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False)

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "description": self.description,
                "body": dict(self.body), "params": dict(self.params),
                "sensors": [s.to_dict() for s in self.sensors], "default_space": self.default_space}

    def override(self, **kw) -> "VehicleSpec":
        """Change body / kind parameters by bare name; `cam_*` edits the first camera (as RoverProfile.override)."""
        d = copy.deepcopy(self.to_dict())
        kind_params = {p.name for p in self.kind_plugin().schema}
        for k, v in kw.items():
            if k.startswith("cam_"):
                cams = [s for s in d["sensors"] if s["type"] == "camera"]
                if not cams:
                    raise ValueError(f"{self.name} has no camera to set {k!r} on")
                cams[0][k[4:]] = v
            elif k in BODY_KEYS:
                d["body"][k] = v
            elif k in kind_params:
                d["params"][k] = v
            elif k in ("name", "description", "default_space"):
                d[k] = v
            else:
                raise ValueError(f"unknown override {k!r}; body: {list(BODY_KEYS)}, {self.kind}: {sorted(kind_params)}")
        return VehicleSpec.from_dict(d)

    def with_sensors(self, extra: list[SensorSpec]) -> "VehicleSpec":
        """A copy that also carries `extra` sensors (those whose name is already taken are skipped)."""
        have = {s.name for s in self.sensors}
        d = copy.deepcopy(self.to_dict())
        d["sensors"] += [s.to_dict() for s in extra if s.name not in have]
        return VehicleSpec.from_dict(d)

    # ---- behaviour (delegated to the kind) ------------------------------------
    def kind_plugin(self):
        return KINDS.get(self.kind)

    def spaces(self) -> dict[str, CommandSpace]:
        return self.kind_plugin().spaces(self)

    def space(self, name: str | None = None) -> CommandSpace:
        spaces = self.spaces()
        key = name or self.default_space
        if key not in spaces:
            raise KeyError(f"{self.name}: no command space {key!r}; has {list(spaces)}")
        return spaces[key]

    def sensor(self, type_: str, name: str | None = None) -> SensorSpec | None:
        for s in self.sensors:
            if s.type == type_ and (name is None or s.name == name):
                return s
        return None

    def derived(self) -> list:
        return self.kind_plugin().derived(self)

    def validate(self, caps: Caps | None = None) -> list[Issue]:
        _, issues = VehicleSpec.parse(self.to_dict())
        return issues + self.kind_plugin().validate(self, caps)

    def check(self, caps: Caps | None = None) -> "VehicleSpec":
        issues = self.validate(caps)
        if any(i.level == "error" for i in issues):
            raise SpecError(issues)
        return self
