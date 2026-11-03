"""Sensor specs. Only the parameters live here; the simulated behaviour is implemented by each backend.

Mount convention for every vehicle: x forward, y left, z up, origin at the centre of the envelope; `height` is
measured from the lowest point of the vehicle (wheel contact / landing-gear base) and `forward` from the centre.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .params import Param

CAMERA = (
    Param("forward", "float", 0.0, -5.0, 5.0, "m", "Lens offset forward of the envelope centre"),
    Param("height", "float", 0.10, 0.0, 20.0, "m", "Lens height above the lowest point of the vehicle"),
    Param("tilt_deg", "float", 8.0, -90.0, 90.0, "deg", "Pitch; positive looks down, negative looks up"),
    Param("fov_deg", "float", 62.0, 10.0, 180.0, "deg", "Horizontal field of view (OV2640 is ~62-66)"),
    Param("width_px", "int", 640, 32, 4096, "px"),
    Param("height_px", "int", 480, 32, 4096, "px"),
    Param("fps", "float", 12.0, 0.5, 240.0, "Hz", "Frame rate (real ESP32-CAM manages ~12 at SVGA)"),
    Param("noise", "float", 0.02, 0.0, 1.0, "", "Gaussian pixel noise, 0..1"),
    Param("motion_blur_ms", "float", 0.0, 0.0, 500.0, "ms", "Exposure time that smears fast motion"),
    Param("fisheye", "bool", False),
    Param("auto_exposure", "bool", True, doc="Emulate the sensor's AEC/AGC (the simulated camera has none)"),
    Param("mjcf_camera", "str", "", doc="Imported models only: use this camera defined in the model instead of the mount above"),
)

IMU = (
    Param("rate_hz", "float", 200.0, 1.0, 2000.0, "Hz"),
    Param("gyro_noise", "float", 0.003, 0.0, 1.0, "rad/s", "Gyro white noise (1 sigma)"),
    Param("accel_noise", "float", 0.05, 0.0, 10.0, "m/s^2", "Accelerometer white noise (1 sigma)"),
)

POSE = (
    Param("rate_hz", "float", 20.0, 0.5, 1000.0, "Hz"),
    Param("pos_noise", "float", 0.0, 0.0, 10.0, "m", "Position noise (1 sigma); 0 = ground truth"),
    Param("yaw_noise", "float", 0.0, 0.0, 3.2, "rad", "Heading noise (1 sigma); 0 = ground truth"),
    Param("delay_s", "float", 0.0, 0.0, 2.0, "s", "Measurement latency"),
)

RANGEFINDER = (
    Param("rays", "int", 3, 1, 128, "", "Number of rays across the field of view"),
    Param("fov_deg", "float", 60.0, 1.0, 360.0, "deg"),
    Param("max_range", "float", 4.0, 0.05, 200.0, "m"),
    Param("direction", "enum", "forward", choices=("forward", "down", "ring")),
    Param("noise", "float", 0.0, 0.0, 5.0, "m"),
    Param("rate_hz", "float", 20.0, 0.5, 500.0, "Hz"),
)

BATTERY = (
    Param("chemistry", "enum", "nimh", choices=("nimh", "lipo", "liion")),
    Param("cells", "int", 4, 1, 14),
    Param("capacity_mah", "float", 2000.0, 10.0, 100000.0, "mAh"),
)

SENSOR_SCHEMAS: dict[str, tuple[Param, ...]] = {
    "camera": CAMERA, "imu": IMU, "pose": POSE, "rangefinder": RANGEFINDER, "battery": BATTERY,
}


@dataclass
class SensorSpec:
    type: str
    name: str
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "name": self.name, **self.options}
