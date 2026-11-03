"""Build: a machine assembled from parts (YAML). `Build.parse` never raises, like `VehicleSpec.parse`.

A build names parts from the library and says how they are put together (how many motors, how many cells in series, where each sensor is
mounted). `simlab.parts.derive` turns it into a `VehicleSpec` plus predicted performance. Three platforms share one file format:

    ground   chassis (size, drive layout) + drive (motors, wheels, driver)        -> a ground_wheeled vehicle
    air      chassis (a frame part)       + drive (motor+propeller part, ESC)     -> a multirotor
    water    chassis (a hull part)        + drive (a thruster part)               -> a boat (plug-in kind)

`power`, `controller`, `sensors`, `lamp`, `extras` and `calibration` mean the same on all of them.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..spec.params import Issue, Param, apply_schema
from ..spec.vehicle import NAME_RE
from ..vehicles.ground import DRIVES
from .library import ROOT, all_parts
from .schema import Part

PLATFORMS = ("ground", "air", "water")
TOP_KEYS = ("name", "platform", "description", "chassis", "drive", "power", "controller", "sensors", "lamp", "extras", "calibration")

CHASSIS = (
    Param("length", "float", 0.16, 0.005, 50.0, "m", "Envelope along the forward axis"),
    Param("width", "float", 0.12, 0.005, 50.0, "m", "Envelope across the vehicle"),
    Param("height", "float", 0.055, 0.005, 50.0, "m", "Envelope height"),
    Param("frame_mass_kg", "float", 0.1, 0.0, 5000.0, "kg", "The bare chassis plate / body, without any of the parts below"),
    Param("drive", "enum", "diff2wd", doc="Drive layout", choices=DRIVES),
    Param("caster_count", "int", 1, 0, 8, "", "Passive casters or skids carrying weight (2-wheel drive)"),
)
DRIVE = (
    Param("motor", "str", "", doc="motor_dc part"),
    Param("motor_count", "int", 2, 1, 12, "", "Even number: half on each side"),
    Param("wheel", "str", "", doc="wheel part"),
    Param("driver", "str", "", doc="driver_dc part"),
    Param("speed_limit_mps", "float", 0.0, 0.0, 40.0, "m/s", "Speed cap set in the firmware; 0 = none (the motors' own top speed)"),
)
AIR_CHASSIS = (
    Param("frame", "str", "", doc="frame part"),
    Param("extra_mass_kg", "float", 0.0, 0.0, 50.0, "kg", "Payload or anything fixed to the frame that is not listed elsewhere"),
)
AIR_DRIVE = (
    Param("propulsor", "str", "", doc="propulsor_air part (motor + propeller); one per rotor"),
    Param("esc", "str", "", doc="esc part; one per rotor"),
)
WATER_CHASSIS = (
    Param("hull", "str", "", doc="hull part"),
    Param("ballast_kg", "float", 0.0, 0.0, 1000.0, "kg", "Added weight (payload, ballast)"),
)
WATER_DRIVE = (
    Param("thruster", "str", "", doc="thruster_water part"),
    Param("count", "int", 1, 1, 4, "", "Thrusters (their thrust adds up)"),
)
POWER = (
    Param("battery", "str", "", doc="battery part (one cell or unit)"),
    Param("series", "int", 4, 1, 14, "", "Cells in series"),
    Param("parallel", "int", 1, 1, 20, "", "Strings in parallel"),
    Param("regulator", "str", "", doc="regulator part, or empty for none"),
)
MOUNT = (
    Param("forward", "float", 0.0, -5.0, 5.0, "m", "Forward of the envelope centre"),
    Param("height", "float", 0.075, 0.0, 20.0, "m", "Above the lowest point of the vehicle"),
    Param("tilt_deg", "float", 0.0, -90.0, 90.0, "deg", "Pitch; positive looks down, negative looks up"),
)
EXTRA = (
    Param("name", "str", "extra"),
    Param("mass_kg", "float", 0.0, 0.0, 100.0, "kg"),
    Param("current_a", "float", 0.0, 0.0, 20.0, "A"),
)
CALIBRATION_KEYS = {
    "speed_scale": Param("speed_scale", "float", 1.0, 0.05, 3.0, "", "Measured / predicted top speed"),
    "yaw_gain": Param("yaw_gain", "float", 1.0, 0.05, 1.5, "", "Measured actual / ideal turn rate"),
    "mass_kg": Param("mass_kg", "float", 1.0, 0.001, 5000.0, "kg", "Measured total mass"),
    "accel_scale": Param("accel_scale", "float", 1.0, 0.05, 5.0, "", "Measured / predicted acceleration"),
    "battery_derate": Param("battery_derate", "float", 0.8, 0.1, 1.0, "", "Usable fraction of the rated capacity"),
}
# per platform: the schema of the three platform-specific sections, and which fields name which kind of part
SECTIONS = {"ground": {"chassis": CHASSIS, "drive": DRIVE, "power": POWER},
            "air": {"chassis": AIR_CHASSIS, "drive": AIR_DRIVE, "power": POWER},
            "water": {"chassis": WATER_CHASSIS, "drive": WATER_DRIVE, "power": POWER}}
REFS = {"ground": (("drive.motor", "motor_dc"), ("drive.wheel", "wheel"), ("drive.driver", "driver_dc"), ("power.battery", "battery")),
        "air": (("chassis.frame", "frame"), ("drive.propulsor", "propulsor_air"), ("drive.esc", "esc"), ("power.battery", "battery")),
        "water": (("chassis.hull", "hull"), ("drive.thruster", "thruster_water"), ("power.battery", "battery"))}


@dataclass
class SensorUse:
    part: str
    name: str
    mount: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"part": self.part, "name": self.name, "mount": dict(self.mount)}


@dataclass
class Resolved:
    """A ground build with its part names replaced by the parts themselves."""
    motor: Part
    wheel: Part
    driver: Part
    battery: Part
    regulator: Part | None
    mcu: Part
    sensors: list[tuple[SensorUse, Part]]
    lamp: Part | None
    lamp_count: int


@dataclass
class ResolvedAir:
    frame: Part
    propulsor: Part
    esc: Part
    battery: Part
    regulator: Part | None
    mcu: Part
    sensors: list[tuple[SensorUse, Part]]
    lamp: Part | None
    lamp_count: int


@dataclass
class ResolvedWater:
    hull: Part
    thruster: Part
    battery: Part
    regulator: Part | None
    mcu: Part
    sensors: list[tuple[SensorUse, Part]]
    lamp: Part | None
    lamp_count: int


def _get(d: Mapping, path: str):
    for k in path.split("."):
        d = d[k]
    return d


@dataclass
class Build:
    name: str
    platform: str = "ground"
    description: str = ""
    chassis: dict = field(default_factory=dict)
    drive: dict = field(default_factory=dict)
    power: dict = field(default_factory=dict)
    controller: str = ""
    sensors: list[SensorUse] = field(default_factory=list)
    lamp: dict | None = None
    extras: list[dict] = field(default_factory=list)
    calibration: dict = field(default_factory=dict)

    # ---- construction ---------------------------------------------------------
    @classmethod
    def parse(cls, d: Mapping) -> tuple["Build | None", list[Issue]]:
        d = dict(d or {})
        issues = [Issue("error", k, "unknown top-level key") for k in d if k not in TOP_KEYS]
        name = str(d.get("name", "")).strip()
        if not NAME_RE.fullmatch(name):
            issues.append(Issue("error", "name", "needs a name made of letters, digits, _ . -"))
        platform = str(d.get("platform", "ground"))
        if platform not in PLATFORMS:
            issues.append(Issue("error", "platform", f"must be one of {list(PLATFORMS)}"))
            return None, issues
        schemas = SECTIONS[platform]
        chassis, i1 = apply_schema(schemas["chassis"], d.get("chassis"), "chassis")
        drive, i2 = apply_schema(schemas["drive"], d.get("drive"), "drive")
        power, i3 = apply_schema(schemas["power"], d.get("power"), "power")
        issues += i1 + i2 + i3
        parts = all_parts()
        sections = {"chassis": chassis, "drive": drive, "power": power}

        def check_ref(where: str, value: str, kind: str, required: bool = True) -> None:
            if not value:
                if required:
                    issues.append(Issue("error", where, f"choose a {kind} part"))
            elif value not in parts or parts[value].kind != kind:
                pool = sorted(n for n, p in parts.items() if p.kind == kind)
                issues.append(Issue("error", where, f"unknown {kind} part {value!r}; choose from {pool}"))

        for where, kind in REFS[platform]:
            check_ref(where, _get(sections, where), kind)
        check_ref("power.regulator", power["regulator"], "regulator", required=False)
        controller = str(d.get("controller") or "")
        check_ref("controller", controller, "mcu")
        if platform == "ground" and drive["motor_count"] % 2:
            issues.append(Issue("error", "drive.motor_count", "use an even number of motors: half drive each side"))

        sensors: list[SensorUse] = []
        raw_sensors = d.get("sensors") or []
        if not isinstance(raw_sensors, (list, tuple)):
            issues.append(Issue("error", "sensors", "must be a list"))
            raw_sensors = []
        seen = set()
        for i, s in enumerate(raw_sensors):
            where = f"sensors[{i}]"
            if not isinstance(s, Mapping):
                issues.append(Issue("error", where, "must be a mapping with 'part' and 'name'"))
                continue
            pname = str(s.get("part", ""))
            sname = str(s.get("name") or pname)
            if pname not in parts or not parts[pname].kind.startswith("sensor_"):
                pool = sorted(n for n, p in parts.items() if p.kind.startswith("sensor_"))
                issues.append(Issue("error", f"{where}.part", f"unknown sensor part {pname!r}; choose from {pool}"))
                continue
            if not NAME_RE.fullmatch(sname) or sname in seen:
                issues.append(Issue("error", f"{where}.name", "needs a unique name made of letters, digits, _ . -"))
                continue
            seen.add(sname)
            mount, im = apply_schema(MOUNT, s.get("mount"), f"{where}.mount")
            issues += im
            sensors.append(SensorUse(pname, sname, mount))

        lamp = d.get("lamp")
        if lamp:
            lp = str(lamp.get("part", "")) if isinstance(lamp, Mapping) else ""
            if lp not in parts or parts[lp].kind != "lamp":
                issues.append(Issue("error", "lamp.part", f"unknown lamp part {lp!r}; choose from {sorted(n for n, p in parts.items() if p.kind == 'lamp')}"))
                lamp = None
            else:
                try:
                    lamp = {"part": lp, "count": Param("count", "int", 1, 1, 20).coerce(lamp.get("count", 1))}
                except ValueError as e:
                    issues.append(Issue("error", "lamp.count", str(e)))
                    lamp = None
        else:
            lamp = None

        extras = []
        for i, e in enumerate(d.get("extras") or []):
            ex, ie = apply_schema(EXTRA, e if isinstance(e, Mapping) else {}, f"extras[{i}]")
            issues += ie
            extras.append(ex)

        cal, raw_cal = {}, dict(d.get("calibration") or {})
        for k, v in raw_cal.items():
            if k in CALIBRATION_KEYS:
                try:
                    cal[k] = CALIBRATION_KEYS[k].coerce(v)
                except ValueError as e:
                    issues.append(Issue("error", f"calibration.{k}", str(e)))
            elif k in ("measured", "date"):
                cal[k] = v
            else:
                issues.append(Issue("error", f"calibration.{k}", f"unknown key; known: {sorted([*CALIBRATION_KEYS, 'measured', 'date'])}"))

        if any(i.level == "error" for i in issues):
            return None, issues
        return cls(name, platform, str(d.get("description", "")), chassis, drive, power, controller, sensors, lamp, extras, cal), issues

    @classmethod
    def from_dict(cls, d: Mapping) -> "Build":
        b, issues = cls.parse(d)
        if b is None:
            raise BuildError(issues)
        return b

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "platform": self.platform, "description": self.description,
                               "chassis": dict(self.chassis), "drive": dict(self.drive), "power": dict(self.power),
                               "controller": self.controller, "sensors": [s.to_dict() for s in self.sensors]}
        if self.lamp:
            out["lamp"] = dict(self.lamp)
        out["extras"] = [dict(e) for e in self.extras]
        out["calibration"] = copy.deepcopy(self.calibration)
        return out

    def resolve(self):
        """The platform's Resolved record: every part name replaced by the part."""
        parts = all_parts()
        reg = parts[self.power["regulator"]] if self.power["regulator"] else None
        lamp = parts[self.lamp["part"]] if self.lamp else None
        common = (parts[self.power["battery"]], reg, parts[self.controller], [(s, parts[s.part]) for s in self.sensors], lamp, self.lamp["count"] if self.lamp else 0)
        if self.platform == "air":
            return ResolvedAir(parts[self.chassis["frame"]], parts[self.drive["propulsor"]], parts[self.drive["esc"]], *common)
        if self.platform == "water":
            return ResolvedWater(parts[self.chassis["hull"]], parts[self.drive["thruster"]], *common)
        return Resolved(parts[self.drive["motor"]], parts[self.drive["wheel"]], parts[self.drive["driver"]], *common)

    def dump(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(yaml.safe_dump(self.to_dict(), sort_keys=False), encoding="utf-8")


class BuildError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues if i.level == "error") or "invalid build")


# ---------------------------------------------------------------- saved builds
def builds_dir() -> Path:
    return Path(os.environ.get("SIMLAB_BUILDS", ROOT / "config" / "builds"))


def build_names() -> list[str]:
    d = builds_dir()
    return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []


def get_build(name: str) -> Build:
    path = builds_dir() / f"{name}.yaml"
    if not NAME_RE.fullmatch(name) or not path.is_file():
        raise KeyError(f"unknown build {name!r}; saved builds: {build_names()}")
    return Build.from_dict(yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {})


def save_build(build: Build) -> Path:
    path = builds_dir() / f"{build.name}.yaml"
    build.dump(path)
    return path
