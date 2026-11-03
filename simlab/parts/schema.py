"""Part kinds: the parameter schema of every kind of component a build can use, and the `Part` record itself.

A part is plain data (one YAML file). Its `params` are checked against the schema of its kind, exactly as a vehicle's
parameters are checked against its vehicle kind, so the library, the Builder form and the validation all come from the
definitions below. Every part says where its numbers came from (`source`) and how far to trust them (`confidence`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..spec.params import Issue, Param, apply_schema
from ..spec.vehicle import NAME_RE

CONFIDENCE = ("datasheet", "typical", "assumed", "measured")
TOP_KEYS = ("name", "kind", "title", "source", "confidence", "mass_kg", "notes", "params")

# supply_tol: fraction around supply_v the part tolerates (a 5 V part with 0.1 accepts 4.5 .. 5.5 V)
_SUPPLY = (
    Param("supply_v", "float", 5.0, 0.8, 60.0, "V", "Nominal supply voltage"),
    Param("supply_tol", "float", 0.1, 0.0, 0.9, "", "Accepted deviation from the nominal supply, as a fraction"),
    Param("current_a", "float", 0.02, 0.0, 20.0, "A", "Typical supply current"),
)

MOTOR_DC = (
    Param("voltage_rated", "float", 6.0, 0.5, 60.0, "V", "Voltage the numbers below are given at"),
    Param("no_load_rpm", "float", 200.0, 1.0, 100000.0, "rpm", "Output shaft speed with no load at the rated voltage"),
    Param("no_load_current_a", "float", 0.15, 0.0, 50.0, "A", "Current with no load at the rated voltage"),
    Param("stall_torque_nm", "float", 0.08, 1e-4, 50.0, "N*m", "Output torque when stalled at the rated voltage (1 kg*cm = 0.098 N*m)"),
    Param("stall_current_a", "float", 1.5, 0.01, 200.0, "A", "Current when stalled at the rated voltage"),
)
WHEEL = (
    Param("radius_m", "float", 0.03, 0.003, 1.0, "m"),
    Param("width_m", "float", 0.02, 0.002, 0.5, "m"),
    Param("friction", "float", 0.8, 0.1, 2.0, "", "Grip on a typical floor (friction coefficient)"),
    Param("rolling_resistance", "float", 0.03, 0.005, 0.3, "", "Rolling-resistance coefficient (force / weight)"),
    Param("is_track", "bool", False, doc="A track unit (one per side) instead of a wheel"),
)
DRIVER_DC = (
    Param("channels", "int", 2, 1, 8, "", "Independent motor outputs (H-bridges)"),
    Param("voltage_min", "float", 2.7, 0.5, 60.0, "V", "Lowest motor supply the driver works at"),
    Param("voltage_max", "float", 10.8, 1.0, 100.0, "V", "Highest motor supply"),
    Param("continuous_a_per_channel", "float", 1.5, 0.01, 100.0, "A"),
    Param("peak_a_per_channel", "float", 2.0, 0.01, 200.0, "A", "Current at which the driver trips or fails"),
    Param("r_on_ohm", "float", 0.36, 0.0, 20.0, "ohm", "Total on-resistance of the bridge (MOSFET drivers)"),
    Param("drop_v", "float", 0.0, 0.0, 10.0, "V", "Fixed voltage lost in the bridge (bipolar drivers such as the L298N)"),
    Param("logic_high_min_v", "float", 2.0, 0.5, 5.5, "V", "Lowest control-input voltage read as HIGH"),
    Param("gpio_per_channel", "int", 2, 1, 4, "", "Controller pins needed per channel (IN1, IN2, PWM ...)"),
    Param("extra_gpio", "int", 0, 0, 6, "", "Further pins (sleep, fault, standby)"),
)
BATTERY = (
    Param("chemistry", "enum", "nimh", doc="Cell chemistry", choices=("nimh", "lipo", "liion", "lifepo4", "lead")),
    Param("cell_v_nominal", "float", 1.2, 0.8, 12.0, "V", "Per cell (or per unit)"),
    Param("cell_v_full", "float", 1.4, 0.8, 14.0, "V", "Per cell, freshly charged"),
    Param("cell_v_cutoff", "float", 1.0, 0.5, 12.0, "V", "Per cell, empty"),
    Param("capacity_mah", "float", 2000.0, 10.0, 500000.0, "mAh", "Per cell (or per unit)"),
    Param("r_internal_ohm", "float", 0.04, 0.0005, 5.0, "ohm", "DC internal resistance per cell"),
    Param("max_continuous_a", "float", 4.0, 0.05, 1000.0, "A", "Largest continuous discharge current per cell"),
)
REGULATOR = (
    Param("topology", "enum", "boost", choices=("boost", "buck", "ldo")),
    Param("v_out", "float", 5.0, 0.8, 60.0, "V"),
    Param("i_out_max_a", "float", 2.0, 0.01, 100.0, "A"),
    Param("efficiency", "float", 0.9, 0.3, 1.0, "", "Typical conversion efficiency"),
    Param("v_in_min", "float", 2.0, 0.5, 60.0, "V"),
    Param("v_in_max", "float", 24.0, 1.0, 100.0, "V"),
    Param("dropout_v", "float", 0.0, 0.0, 10.0, "V", "Input must exceed the output by this much (buck, linear)"),
    Param("quiescent_a", "float", 0.002, 0.0, 1.0, "A"),
)
MCU = (
    Param("vin_min", "float", 4.6, 0.8, 60.0, "V", "Lowest supply voltage on the board's power input"),
    Param("vin_max", "float", 12.0, 1.0, 100.0, "V"),
    Param("logic_v", "float", 3.3, 1.2, 5.5, "V", "Logic level of the pins"),
    Param("current_a", "float", 0.15, 0.0, 10.0, "A", "Typical supply current in use"),
    Param("gpio_free", "int", 20, 0, 200, "", "Pins left for a driver and sensors"),
    Param("interfaces", "strlist", [], doc="i2c, uart, spi, adc, pwm, wifi, camera"),
    Param("integrated", "strlist", [], doc="Parts built onto the board, e.g. camera_ov2640"),
)
SENSOR_CAMERA = (
    Param("width_px", "int", 640, 32, 4096, "px"),
    Param("height_px", "int", 480, 32, 4096, "px"),
    Param("fps_max", "float", 12.0, 0.5, 240.0, "Hz", "Frame rate it manages at this resolution"),
    Param("fov_deg", "float", 65.0, 10.0, 180.0, "deg", "Horizontal field of view"),
    Param("noise", "float", 0.02, 0.0, 1.0, "", "Pixel noise, 0..1"),
    *_SUPPLY,
    Param("interface", "enum", "wifi_stream", doc="wifi_stream: a self-contained board that streams over Wi-Fi (no controller pins); "
                                                  "integrated: the camera built onto the controller board; csi / usb: needs that interface",
          choices=("wifi_stream", "integrated", "csi", "usb")),
    Param("integrated_tag", "str", "", doc="For interface=integrated: the tag the controller must list under 'integrated'"),
)
SENSOR_RANGE = (
    Param("sensor_type", "enum", "ultrasonic", choices=("ultrasonic", "tof", "lidar", "ir_proximity")),
    Param("rays", "int", 1, 1, 128),
    Param("fov_deg", "float", 15.0, 1.0, 360.0, "deg"),
    Param("min_range_m", "float", 0.02, 0.0, 50.0, "m"),
    Param("max_range_m", "float", 4.0, 0.05, 200.0, "m"),
    Param("noise_m", "float", 0.003, 0.0, 5.0, "m"),
    Param("rate_hz", "float", 15.0, 0.5, 500.0, "Hz"),
    Param("logic_v", "float", 5.0, 1.2, 5.5, "V", "Level of its output pins (an echo pin at 5 V can damage a 3.3 V controller)"),
    *_SUPPLY,
    Param("interface", "enum", "gpio_trig_echo", choices=("gpio_trig_echo", "gpio_digital", "i2c", "uart", "analog")),
)
SENSOR_IMU = (
    Param("rate_hz", "float", 100.0, 1.0, 2000.0, "Hz"),
    Param("gyro_noise", "float", 0.00087, 0.0, 1.0, "rad/s", "Gyro noise, 1 sigma, at the stated rate"),
    Param("accel_noise", "float", 0.04, 0.0, 10.0, "m/s^2"),
    Param("logic_v", "float", 3.3, 1.2, 5.5, "V"),
    *_SUPPLY,
    Param("interface", "enum", "i2c", choices=("i2c", "spi")),
)
SENSOR_GPS = (
    Param("pos_noise_m", "float", 2.5, 0.0, 50.0, "m"),
    Param("rate_hz", "float", 1.0, 0.1, 50.0, "Hz"),
    Param("logic_v", "float", 3.3, 1.2, 5.5, "V"),
    *_SUPPLY,
    Param("interface", "enum", "uart", choices=("uart",)),
)
LAMP = (
    Param("light_intensity", "float", 0.5, 0.0, 50.0, "", "Headlamp strength on the simulator's scale (micro_2wd 0.5, mid_4wd 1.2)"),
    *_SUPPLY,
)

# ---- air and water (lower confidence than the ground parts: the physics behind them is rougher)
FRAME = (
    Param("layout", "enum", "quad_x", doc="Rotor arrangement", choices=("quad_x", "quad_plus", "hex", "octo")),
    Param("arm_length_m", "float", 0.11, 0.01, 3.0, "m", "Centre to rotor distance"),
    Param("length_m", "float", 0.25, 0.01, 5.0, "m", "Envelope along the forward axis"),
    Param("width_m", "float", 0.25, 0.01, 5.0, "m", "Envelope across the vehicle"),
    Param("height_m", "float", 0.06, 0.005, 2.0, "m", "Envelope height"),
    Param("max_prop_in", "float", 5.1, 0.5, 40.0, "in", "Largest propeller the frame takes"),
)
PROPULSOR_AIR = (
    Param("ref_voltage", "float", 15.6, 2.0, 60.0, "V", "Loaded battery voltage the test was done at"),
    Param("thrust_max_n_at_ref", "float", 12.3, 0.01, 1000.0, "N", "Static thrust of one motor + propeller at full throttle at that voltage"),
    Param("current_max_a_at_ref", "float", 31.0, 0.05, 500.0, "A", "Current at full throttle at that voltage"),
    Param("idle_current_a", "float", 1.3, 0.0, 50.0, "A", "Current at the lowest throttle that still spins the propeller"),
    Param("prop_diameter_in", "float", 5.0, 0.5, 40.0, "in"),
    Param("prop_pitch_in", "float", 4.0, 0.2, 30.0, "in"),
    Param("kv", "float", 2400.0, 50.0, 60000.0, "rpm/V"),
)
ESC = (
    Param("max_continuous_a", "float", 30.0, 1.0, 300.0, "A", "Per motor"),
    Param("v_max", "float", 17.0, 2.0, 60.0, "V", "Highest battery voltage"),
    Param("v_min", "float", 6.0, 1.0, 60.0, "V", "Lowest battery voltage"),
)
HULL = (
    Param("length_m", "float", 1.2, 0.1, 30.0, "m"),
    Param("width_m", "float", 0.5, 0.05, 10.0, "m"),
    Param("height_m", "float", 0.3, 0.05, 5.0, "m", "Hull depth: draft plus freeboard when it floats"),
)
THRUSTER_WATER = (
    Param("thrust_max_n", "float", 60.0, 0.5, 20000.0, "N", "Bollard (static) thrust at the rated voltage"),
    Param("rated_v", "float", 12.0, 2.0, 60.0, "V"),
    Param("current_a", "float", 20.0, 0.1, 500.0, "A", "Current at full thrust at the rated voltage"),
    Param("idle_current_a", "float", 0.2, 0.0, 20.0, "A"),
)


@dataclass(frozen=True)
class PartKind:
    name: str
    title: str
    params: tuple[Param, ...]
    doc: str = ""


PART_KINDS: dict[str, PartKind] = {k.name: k for k in (
    PartKind("motor_dc", "DC gear motor", MOTOR_DC, "Brushed DC motor with its gearbox: the datasheet numbers at one voltage."),
    PartKind("wheel", "Wheel or track", WHEEL),
    PartKind("driver_dc", "Motor driver", DRIVER_DC),
    PartKind("battery", "Battery cell / unit", BATTERY, "One cell (or one pack unit); the build chooses how many in series and parallel."),
    PartKind("regulator", "Voltage regulator", REGULATOR),
    PartKind("mcu", "Controller board", MCU),
    PartKind("sensor_camera", "Camera", SENSOR_CAMERA),
    PartKind("sensor_range", "Range sensor", SENSOR_RANGE),
    PartKind("sensor_imu", "IMU", SENSOR_IMU),
    PartKind("sensor_gps", "GPS", SENSOR_GPS),
    PartKind("lamp", "Lamp", LAMP),
    PartKind("frame", "Drone frame", FRAME, "Rotor layout, arm length and envelope. Its mass is the bare frame."),
    PartKind("propulsor_air", "Motor + propeller", PROPULSOR_AIR, "One brushless motor with its propeller, from a published thrust test."),
    PartKind("esc", "Speed controller (ESC)", ESC),
    PartKind("hull", "Boat hull", HULL, "A displacement hull, modelled as a box."),
    PartKind("thruster_water", "Boat thruster", THRUSTER_WATER, "Propeller drive with its motor; the rudder is part of the boat model."),
)}


def register_kind(kind: PartKind) -> None:
    """Add a part kind (the air and water parts register theirs here)."""
    PART_KINDS[kind.name] = kind


@dataclass(frozen=True)
class Part:
    name: str
    kind: str
    title: str = ""
    params: dict = field(default_factory=dict)
    mass_kg: float = 0.0
    source: str = ""
    confidence: str = "assumed"
    notes: str = ""
    origin: str = "library"                      # "library" (ships with the project) or "user" (config/parts)

    @classmethod
    def parse(cls, d: Mapping) -> tuple["Part | None", list[Issue]]:
        """Never raises: (part, issues); part is None when there are errors."""
        d = dict(d or {})
        issues = [Issue("error", k, "unknown top-level key") for k in d if k not in TOP_KEYS]
        name = str(d.get("name", "")).strip()
        if not NAME_RE.fullmatch(name):
            issues.append(Issue("error", "name", "needs a name made of letters, digits, _ . -"))
        kind = d.get("kind")
        if kind not in PART_KINDS:
            issues.append(Issue("error", "kind", f"unknown part kind {kind!r}; known: {sorted(PART_KINDS)}"))
            return None, issues
        params, i1 = apply_schema(PART_KINDS[kind].params, d.get("params"), "params")
        issues += i1
        conf = str(d.get("confidence", "assumed"))
        if conf not in CONFIDENCE:
            issues.append(Issue("error", "confidence", f"must be one of {list(CONFIDENCE)}"))
        try:
            mass = Param("mass_kg", "float", 0.0, 0.0, 1000.0).coerce(d.get("mass_kg", 0.0))
        except ValueError as e:
            issues.append(Issue("error", "mass_kg", str(e)))
            mass = 0.0
        if any(i.level == "error" for i in issues):
            return None, issues
        part = cls(name, kind, str(d.get("title", "")) or name, params, mass, str(d.get("source", "")), conf, str(d.get("notes", "")))
        issues += check_part(part)
        return (None if any(i.level == "error" for i in issues) else part), issues

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "title": self.title, "source": self.source, "confidence": self.confidence,
                "mass_kg": self.mass_kg, "notes": self.notes, "params": dict(self.params)}


def motor_efficiency_at_max_power(p: Mapping) -> float:
    """Efficiency of a motor with these numbers at its maximum-output-power point (linear torque-speed line)."""
    w_nl = p["no_load_rpm"] * 2 * math.pi / 60.0
    p_mech = p["stall_torque_nm"] * w_nl / 4.0
    p_elec = p["voltage_rated"] * (p["no_load_current_a"] + (p["stall_current_a"] - p["no_load_current_a"]) / 2.0)
    return p_mech / max(p_elec, 1e-9)


def check_part(part: Part) -> list[Issue]:
    """Sanity checks that catch a datasheet typo (a motor that would be more than 100 % efficient is not physical)."""
    p, out = part.params, []
    if part.kind == "motor_dc":
        if p["no_load_current_a"] >= p["stall_current_a"]:
            out.append(Issue("error", "params.stall_current_a", "stall current must exceed the no-load current"))
        else:
            eff = motor_efficiency_at_max_power(p)
            if eff > 0.9:
                out.append(Issue("error", "params.stall_torque_nm", f"these numbers imply {eff:.0%} efficiency at maximum power: check units "
                                                                    "(torque in N*m, 1 kg*cm = 0.098 N*m; speed in rpm)"))
            elif eff > 0.6:
                out.append(Issue("warn", "params.stall_torque_nm", f"{eff:.0%} efficiency at maximum power is high for a small brushed gearmotor"))
    elif part.kind == "battery":
        if not p["cell_v_cutoff"] < p["cell_v_nominal"] < p["cell_v_full"]:
            out.append(Issue("error", "params.cell_v_nominal", "needs cutoff < nominal < full voltage"))
    elif part.kind == "driver_dc":
        if p["voltage_min"] >= p["voltage_max"]:
            out.append(Issue("error", "params.voltage_min", "must be below voltage_max"))
        if p["continuous_a_per_channel"] > p["peak_a_per_channel"]:
            out.append(Issue("error", "params.continuous_a_per_channel", "continuous current cannot exceed the peak current"))
    elif part.kind == "regulator":
        if p["v_in_min"] >= p["v_in_max"]:
            out.append(Issue("error", "params.v_in_min", "must be below v_in_max"))
    elif part.kind == "mcu":
        if p["vin_min"] >= p["vin_max"]:
            out.append(Issue("error", "params.vin_min", "must be below vin_max"))
    elif part.kind == "esc":
        if p["v_min"] >= p["v_max"]:
            out.append(Issue("error", "params.v_min", "must be below v_max"))
    elif part.kind == "propulsor_air":
        if p["idle_current_a"] >= p["current_max_a_at_ref"]:
            out.append(Issue("error", "params.idle_current_a", "must be below the full-throttle current"))
    return out


def kinds_info() -> dict:
    return {k.name: {"title": k.title, "doc": k.doc, "params": [p.to_dict() for p in k.params]} for k in PART_KINDS.values()}
