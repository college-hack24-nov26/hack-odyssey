"""The rover's pin map as data: `config/hardware.json` key "pins" (defaults below), turned into compiler flags by tools/flash.py.

The order of PIN_ORDER is the order of `pins=` in the firmware's hello line, so the app can compare the wiring the board was built with
against the wiring the config says.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "hardware.json"

DEFAULT_PINS = {"left_in1": 26, "left_in2": 27, "right_in1": 32, "right_in2": 33, "sda": 21, "scl": 22, "trig": 18, "echo": 35}
PIN_ORDER = ("left_in1", "left_in2", "right_in1", "right_in2", "sda", "scl", "trig", "echo")
FLAGS = {"left_in1": "AIN1_PIN", "left_in2": "AIN2_PIN", "right_in1": "BIN1_PIN", "right_in2": "BIN2_PIN",
         "sda": "SDA_PIN", "scl": "SCL_PIN", "trig": "TRIG_PIN", "echo": "ECHO_PIN"}
INPUT_ONLY = {34, 35, 36, 39}                         # ESP32 pins that can be read but never driven
FLASH_PINS = {6, 7, 8, 9, 10, 11}                     # wired to the module's flash chip
MOTOR_KEYS = ("left_in1", "left_in2", "right_in1", "right_in2")
OUTPUT_KEYS = MOTOR_KEYS + ("trig",)


def load_pins(path: Path | None = None) -> dict:
    p = dict(DEFAULT_PINS)
    try:
        d = json.loads((path or CONFIG).read_text())
        p.update({k: int(v) for k, v in (d.get("pins") or {}).items() if k in DEFAULT_PINS})
    except (OSError, ValueError, TypeError):
        pass
    return p


def check_pins(pins: dict) -> list[str]:
    """Plain-words problems with a pin map ([] = fine). Catches the mistakes made on this very rover: sharing a pin, or driving from an input-only pin."""
    out: list[str] = []
    seen: dict[int, str] = {}
    for k in PIN_ORDER:
        n = int(pins[k])
        if n in seen:
            out.append(f"GPIO{n} is used for both {seen[n]} and {k}: one pin cannot do two jobs (the motor would twitch with the sensor)")
        seen[n] = k
        if n in FLASH_PINS:
            out.append(f"GPIO{n} ({k}) is wired to the board's flash chip: pick another pin")
        if k in OUTPUT_KEYS and n in INPUT_ONLY:
            out.append(f"GPIO{n} ({k}) is input-only on the ESP32 and cannot drive a signal: use an output-capable pin such as GPIO18")
        if not 0 <= n <= 39:
            out.append(f"GPIO{n} ({k}) does not exist on the ESP32")
    return out


def pins_tuple(pins: dict) -> tuple[int, ...]:
    return tuple(int(pins[k]) for k in PIN_ORDER)


def pins_to_flags(pins: dict) -> list[str]:
    return [f"-D{FLAGS[k]}={int(pins[k])}" for k in PIN_ORDER]
