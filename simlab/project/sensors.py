"""The sensor-status ladder: unknown -> fitted -> verified.

"fitted" is what the user says (it is wired in). "verified" means sane readings were seen AND the user confirmed it, so it is reachable only through
`confirm()`, which needs an explicit confirm=True; nothing else (an update, a blueprint change, a run record) can raise a sensor to it. Until a
sensor is verified the app never uses it for a safety decision. Lowering a sensor (wiring changed, it was removed) is always allowed.
"""
from __future__ import annotations

import time

SENSORS = ("camera", "mpu6050", "hcsr04")
LEVELS = ("unknown", "fitted", "verified")

# a Builder part -> the ladder's sensor name (kind sensor_camera is any camera)
PART_TO_SENSOR = {"mpu6050": "mpu6050", "hc_sr04": "hcsr04"}
KIND_TO_SENSOR = {"sensor_camera": "camera"}


class SensorError(ValueError):
    pass


def fresh() -> dict:
    return {s: {"level": "unknown", "note": "", "at": 0.0} for s in SENSORS}


def normalise(raw) -> dict:
    """A stored sensors block made safe: every sensor present, unknown levels become 'unknown'."""
    out = fresh()
    if isinstance(raw, dict):
        for s in SENSORS:
            v = raw.get(s)
            if isinstance(v, dict) and v.get("level") in LEVELS:
                out[s] = {"level": v["level"], "note": str(v.get("note", ""))[:200], "at": float(v.get("at", 0.0) or 0.0)}
    return out


def _check(name: str) -> None:
    if name not in SENSORS:
        raise SensorError(f"unknown sensor {name!r}; known: {', '.join(SENSORS)}")


def set_level(sensors: dict, name: str, level: str, note: str = "", now: float | None = None) -> None:
    """Move a sensor to 'unknown' or 'fitted'. 'verified' is refused here: only confirm() gets there."""
    _check(name)
    if level == "verified":
        raise SensorError(f"{name} can only be marked verified by an explicit confirm (readings seen and you said it works)")
    if level not in LEVELS:
        raise SensorError(f"level must be one of {', '.join(LEVELS)}")
    sensors[name] = {"level": level, "note": str(note)[:200], "at": time.time() if now is None else now}


def confirm(sensors: dict, name: str, confirm: bool = False, note: str = "", now: float | None = None) -> None:
    """The one way to 'verified': needs confirm=True and a sensor already marked fitted."""
    _check(name)
    if confirm is not True:
        raise SensorError("confirm must be true: say you saw sane readings from this sensor")
    if sensors[name]["level"] == "unknown":
        raise SensorError(f"mark {name} as fitted first (it has to be wired in before it can be verified)")
    sensors[name] = {"level": "verified", "note": str(note)[:200], "at": time.time() if now is None else now}


def usable_for_safety(sensors: dict, name: str) -> bool:
    return sensors.get(name, {}).get("level") == "verified"


def expected(build: dict | None) -> list[str]:
    """The ladder sensors a blueprint carries (so the page can say 'your blueprint has an HC-SR04, it is still unknown')."""
    out: list[str] = []
    if not isinstance(build, dict):
        return out
    from ..parts.library import all_parts
    parts = all_parts()
    for s in build.get("sensors", []) or []:
        name = s.get("part") if isinstance(s, dict) else None
        sensor = PART_TO_SENSOR.get(name) or (KIND_TO_SENSOR.get(parts[name].kind) if name in parts else None)
        if sensor and sensor not in out:
            out.append(sensor)
    return out
