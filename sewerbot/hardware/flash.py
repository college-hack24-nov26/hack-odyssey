"""Compile and flash the rover firmware with arduino-cli, safely.

* The sketch is copied to a temporary folder first: the Wi-Fi name/password (config/wifi.json, git-ignored) is written there as
  wifi_secrets.h and never lands in the repository or in any log.
* The pin map comes from config/hardware.json ("pins") as -D flags, so a different wiring is a config change, not a code edit; a map with a
  shared pin or a driven input-only pin is refused before anything is compiled.
* Uploading needs the exact confirm word and a port that exists right now. The older known-good firmware (esp32_rover_serial) is the
  recovery build: `sketch="recovery"`.
* If arduino-cli is missing or the upload fails, manual_steps() gives the Arduino IDE steps in plain words.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .pins import check_pins, load_pins, pins_to_flags
from .serial_rover import list_ports

ROOT = Path(__file__).resolve().parents[2]
SKETCHES = {"v2": ROOT / "firmware" / "esp32_rover_v2", "recovery": ROOT / "firmware" / "esp32_rover_serial"}
WIFI_CONFIG = ROOT / "config" / "wifi.json"
FQBN = "esp32:esp32:esp32"
CONFIRM_WORD = "FLASH"


class FlashError(RuntimeError):
    """The message says what to do about it."""


def find_cli() -> str | None:
    p = shutil.which("arduino-cli")
    if p:
        return p
    for guess in (r"C:\Program Files\Arduino CLI\arduino-cli.exe", "/usr/local/bin/arduino-cli"):
        if Path(guess).exists():
            return guess
    return None


def load_wifi(path: Path | None = None) -> dict | None:
    try:
        d = json.loads((path or WIFI_CONFIG).read_text())
        return d if d.get("ssid") else None
    except (OSError, ValueError):
        return None


def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def build_flags(sketch: str, pins: dict, wifi: bool, port_tcp: int = 8023) -> list[str]:
    if sketch == "recovery":
        return []                                           # the old sketch has fixed pins and no options
    flags = pins_to_flags(pins)
    flags.append(f"-DENABLE_WIFI={1 if wifi else 0}")
    flags.append(f"-DWIFI_TCP_PORT={port_tcp}")
    return flags


def prepare(sketch: str, wifi: dict | None, dest: Path) -> Path:
    """Copy the sketch folder to dest/<name> (arduino-cli wants the folder and the .ino to share a name) and write the secrets header."""
    src = SKETCHES[sketch]
    if not (src / f"{src.name}.ino").exists():
        raise FlashError(f"the firmware folder {src} is missing")
    out = dest / src.name
    shutil.copytree(src, out)
    if wifi and sketch == "v2":
        ssid, pw = str(wifi["ssid"]).replace('"', '\\"'), str(wifi.get("password", "")).replace('"', '\\"')
        (out / "wifi_secrets.h").write_text(f'#define WIFI_SSID "{ssid}"\n#define WIFI_PASS "{pw}"\n', encoding="utf-8")
    return out


def _run(cmd: list[str], secrets: list[str], timeout: float) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, "timed out"
    except OSError as e:
        return 1, str(e)
    return p.returncode, _scrub((p.stdout or "") + (p.stderr or ""), secrets)


def compile_sketch(sketch: str = "v2", pins: dict | None = None, use_wifi: bool = False, fqbn: str = FQBN, build_dir: Path | None = None,
                   timeout: float = 600.0) -> dict:
    """Compile only. {'ok', 'output', 'build_dir', 'flags'}; never touches a board."""
    cli = find_cli()
    if not cli:
        raise FlashError("arduino-cli was not found: " + manual_steps(sketch))
    pins = pins or load_pins()
    problems = check_pins(pins)
    if problems and sketch != "recovery":
        raise FlashError("the pin map is not usable: " + "; ".join(problems))
    wifi = load_wifi() if use_wifi else None
    if use_wifi and wifi is None:
        raise FlashError("Wi-Fi was requested but config/wifi.json (ssid, password) is missing")
    secrets = [str(wifi.get("password", "")), str(wifi.get("ssid", ""))] if wifi else []
    flags = build_flags(sketch, pins, bool(wifi))
    with tempfile.TemporaryDirectory(prefix="rover_fw_") as tmp:
        sk = prepare(sketch, wifi, Path(tmp))
        bdir = build_dir or Path(tmp) / "build"
        cmd = [cli, "compile", "--fqbn", fqbn, "--build-path", str(bdir)]
        if flags:
            cmd += ["--build-property", "compiler.cpp.extra_flags=" + " ".join(flags)]
        code, out = _run(cmd + [str(sk)], secrets, timeout)
        result = {"ok": code == 0, "output": out[-4000:], "flags": [f for f in flags if "WIFI_" not in f], "wifi": bool(wifi)}
        if build_dir is not None:
            result["build_dir"] = str(bdir)
    return result


def flash(port: str, confirm: str, sketch: str = "v2", pins: dict | None = None, use_wifi: bool = False, fqbn: str = FQBN,
          timeout: float = 900.0) -> dict:
    """Compile and upload. Refuses without the confirm word, for a port that is not there, or a pin map with a conflict."""
    if confirm != CONFIRM_WORD:
        raise FlashError(f"not confirmed: pass the word {CONFIRM_WORD} only after the person said yes to flashing")
    if port not in {p["port"] for p in list_ports()}:
        raise FlashError(f"{port} is not connected right now (check the USB cable and Device Manager)")
    cli = find_cli()
    if not cli:
        raise FlashError("arduino-cli was not found: " + manual_steps(sketch))
    pins = pins or load_pins()
    problems = check_pins(pins)
    if problems and sketch != "recovery":
        raise FlashError("the pin map is not usable: " + "; ".join(problems))
    wifi = load_wifi() if use_wifi else None
    if use_wifi and wifi is None:
        raise FlashError("Wi-Fi was requested but config/wifi.json (ssid, password) is missing")
    secrets = [str(wifi.get("password", "")), str(wifi.get("ssid", ""))] if wifi else []
    flags = build_flags(sketch, pins, bool(wifi))
    with tempfile.TemporaryDirectory(prefix="rover_fw_") as tmp:
        sk = prepare(sketch, wifi, Path(tmp))
        bdir = Path(tmp) / "build"
        cmd = [cli, "compile", "--fqbn", fqbn, "--build-path", str(bdir)]
        if flags:
            cmd += ["--build-property", "compiler.cpp.extra_flags=" + " ".join(flags)]
        code, out = _run(cmd + [str(sk)], secrets, timeout)
        if code != 0:
            raise FlashError("the firmware did not compile:\n" + out[-1500:] + "\n" + manual_steps(sketch))
        code, out = _run([cli, "upload", "-p", port, "--fqbn", fqbn, "--input-dir", str(bdir), str(sk)], secrets, timeout)
        if code != 0:
            hint = ("hold the BOOT button on the ESP32 while the upload starts, release when it says Writing; " if "Failed to connect" in out else "") + \
                   "close the serial monitor and anything else using the port"
            raise FlashError(f"the upload failed ({hint}):\n{out[-1200:]}\n{manual_steps(sketch)}")
    return {"ok": True, "port": port, "sketch": sketch, "flags": [f for f in flags if "WIFI_" not in f], "wifi": bool(wifi)}


def manual_steps(sketch: str = "v2") -> str:
    folder = SKETCHES.get(sketch, SKETCHES["v2"])
    return (f"Manual flashing: 1) open {folder / (folder.name + '.ino')} in the Arduino IDE; 2) Tools > Board > esp32 > ESP32 Dev Module "
            "(core 3.x); 3) select the rover's COM port; 4) close the serial monitor; 5) press Upload (if it sits on 'Connecting...', hold the "
            "BOOT button until it starts writing). The rover must be on a block with the wheels off the ground while it reboots.")
