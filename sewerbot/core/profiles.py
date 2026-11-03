"""Rover profiles: one description shared by the Webots PROTO and the real-hardware tab."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict, replace

from simlab.catalog import get_vehicle
from simlab.spec import WEBOTS_CAPS, VehicleSpec
from simlab.vehicles.ground import DRIVES as DRIVE_TYPES, unicycle_to_sides as _mix_sides, wheel_xs

from .geometry import Section, fit_check

MAX_SPEED_LIMIT = WEBOTS_CAPS.max_ground_speed  # m/s: what the Webots world can simulate without tunnelling


@dataclass
class CameraSpec:
    height: float = 0.10          # lens height above the floor (m)
    tilt_deg: float = 8.0         # downward pitch
    fov_deg: float = 62.0         # horizontal field of view (OV2640 is ~62-66 deg)
    width_px: int = 640
    height_px: int = 480
    fps: float = 12.0             # real ESP32-CAM manages ~12 fps at SVGA
    noise: float = 0.02           # gaussian noise 0..1 (Webots Camera.noise)
    motion_blur_ms: float = 0.0   # >0 enables Webots motion blur (slow, but shows speed limits)
    fisheye: bool = False
    auto_exposure: bool = True    # real ESP32-CAMs run AEC/AGC; the simulated camera does not, so we emulate it


@dataclass
class RoverProfile:
    name: str = "mid_4wd"
    drive: str = "skid4wd"
    length: float = 0.35
    width: float = 0.26
    height: float = 0.12
    wheel_radius: float = 0.05
    mass: float = 3.5
    max_speed: float = 0.5        # m/s
    max_accel: float = 0.6        # m/s^2
    max_yaw_rate: float = 1.5     # rad/s (actual body rotation rate)
    yaw_gain: float = 1.0         # measured actual/ideal yaw rate of an in-place turn (skid-steer slips: < 1)
    light_intensity: float = 1.2  # headlamp; Webots uses inverse-square falloff, so keep this modest
    camera: CameraSpec = field(default_factory=CameraSpec)

    # ---- validation -----------------------------------------------------------
    def validate(self) -> "RoverProfile":
        if self.drive not in DRIVE_TYPES:
            raise ValueError(f"drive must be one of {DRIVE_TYPES}")
        for k in ("length", "width", "height", "wheel_radius", "mass", "max_speed"):
            if getattr(self, k) <= 0:
                raise ValueError(f"{k} must be > 0")
        if self.max_speed > MAX_SPEED_LIMIT:
            raise ValueError(f"max_speed {self.max_speed} exceeds limit {MAX_SPEED_LIMIT} m/s")
        if self.wheel_radius * 2 > self.height + 0.02:
            raise ValueError("wheels taller than the body: reduce wheel_radius or raise height")
        return self

    # ---- helpers --------------------------------------------------------------
    @property
    def track_width(self) -> float:
        """Distance between left and right wheel/track centres."""
        return self.width - self.wheel_width

    @property
    def wheel_width(self) -> float:
        return min(0.06, max(0.015, 0.5 * self.wheel_radius))

    @property
    def top_height(self) -> float:
        return max(self.height + 0.4 * self.wheel_radius, self.camera.height + 0.02)

    def fits(self, section: Section) -> dict:
        return fit_check(self.width, self.length, self.top_height, section)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "RoverProfile":
        d = dict(d)
        cam = CameraSpec(**d.pop("camera", {}))
        return RoverProfile(camera=cam, **d).validate()

    def override(self, **kw) -> "RoverProfile":
        cam_kw = {k[4:]: v for k, v in kw.items() if k.startswith("cam_")}
        other = {k: v for k, v in kw.items() if not k.startswith("cam_")}
        cam = replace(self.camera, **cam_kw) if cam_kw else self.camera
        return replace(self, camera=cam, **other).validate()


_CAMERA_FIELDS = tuple(CameraSpec.__dataclass_fields__)


def profile_from_spec(spec: VehicleSpec) -> RoverProfile:
    """The sewer/Webots view of a ground VehicleSpec (simlab/vehicles/presets/*.yaml is the source of truth)."""
    if spec.kind != "ground_wheeled":
        raise ValueError(f"{spec.name} is a {spec.kind}; the sewer rover pipeline only drives ground_wheeled vehicles")
    cam = spec.sensor("camera")
    camera = CameraSpec(**{k: v for k, v in (cam.options if cam else {}).items() if k in _CAMERA_FIELDS})
    p, b = spec.params, spec.body
    return RoverProfile(name=spec.name, drive=p["drive"], length=b["length"], width=b["width"], height=b["height"],
                        wheel_radius=p["wheel_radius"], mass=b["mass"], max_speed=p["max_speed"],
                        max_accel=p["max_accel"], max_yaw_rate=p["max_yaw_rate"], yaw_gain=p["yaw_gain"],
                        light_intensity=p["light_intensity"], camera=camera).validate()


def spec_from_profile(p: RoverProfile) -> VehicleSpec:
    return VehicleSpec.from_dict({
        "name": p.name, "kind": "ground_wheeled",
        "body": {"mass": p.mass, "length": p.length, "width": p.width, "height": p.height},
        "params": {"drive": p.drive, "wheel_radius": p.wheel_radius, "max_speed": p.max_speed,
                   "max_accel": p.max_accel, "max_yaw_rate": p.max_yaw_rate, "yaw_gain": p.yaw_gain,
                   "light_intensity": p.light_intensity},
        "sensors": [{"type": "camera", "name": "front", **asdict(p.camera)}],
    })


PRESETS: dict[str, RoverProfile] = {
    name: profile_from_spec(get_vehicle(name)) for name in ("micro_2wd", "mid_4wd", "tracked_crawler", "large_6wd")
}


def get_profile(name: str, **overrides) -> RoverProfile:
    if name not in PRESETS:
        raise KeyError(f"unknown rover preset {name!r}; choose from {sorted(PRESETS)}")
    p = PRESETS[name]
    return p.override(**overrides) if overrides else p


def wheel_layout(p: RoverProfile) -> list[tuple[str, float, float]]:
    """(name, x, side) for every driven wheel. side +1 = left, -1 = right (robot frame x fwd, y left)."""
    xs = wheel_xs(p.drive, p.length)
    out = []
    for i, x in enumerate(xs):
        out.append((f"wheel_l{i}", x, +1))
        out.append((f"wheel_r{i}", x, -1))
    return out


def unicycle_to_sides(p: RoverProfile, v: float, w: float) -> tuple[float, float]:
    """Convert (v, omega) to (left, right) ground speeds (m/s), clamped to the profile limits."""
    return _mix_sides(v, w, max_speed=p.max_speed, max_yaw_rate=p.max_yaw_rate, track=p.track_width,
                      yaw_gain=p.yaw_gain)
