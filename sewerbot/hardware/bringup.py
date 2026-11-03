"""First contact with a real rover: a short, safe list of checks that says in plain words what is connected and what is not.

Reads only, unless `drive` is on, and then only after you type that the wheels are off the ground. Every check ends as pass / warn / fail / skip
with a sentence a person can act on, and the whole run (checks + everything the firmware said) is written to runs/rover/<time>/.

Checks: link, pin plan (config), firmware (version, why the board last reset: brownout!), MPU6050 (found? on which pins), gyro (bias, noise, gravity),
a by-hand 90 degree turn (gyro sign and scale), HC-SR04, power (multimeter values you type), motors (guided, wheels off the ground), camera.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .pins import check_pins, load_pins, pins_tuple
from .serial_rover import list_ports

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs" / "rover"
I2C_CANDIDATES = ((21, 22), (16, 17), (4, 5), (13, 14), (23, 5))
GOOD_PORT_WORDS = ("cp210", "ch340", "ch910", "usb-serial", "usb serial", "ftdi", "silicon labs")
BAD_PORT_WORDS = ("bluetooth",)


@dataclass
class Check:
    id: str
    title: str
    status: str                 # pass | warn | fail | skip
    detail: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status, "detail": self.detail, "data": self.data}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    folder: str = ""

    def add(self, c: Check) -> Check:
        self.checks.append(c)
        return c

    def get(self, cid: str) -> Check | None:
        return next((c for c in self.checks if c.id == cid), None)

    @property
    def ok_to_test(self) -> bool:
        """True when nothing that makes a motion test unsafe or meaningless has failed."""
        return not any(c.status == "fail" for c in self.checks)

    def summary(self) -> str:
        n = {s: sum(1 for c in self.checks if c.status == s) for s in ("pass", "warn", "fail", "skip")}
        return f"{n['pass']} passed, {n['warn']} to look at, {n['fail']} failed, {n['skip']} skipped"

    def to_dict(self) -> dict:
        return {"started": self.started, "summary": self.summary(), "ok_to_test": self.ok_to_test, "checks": [c.to_dict() for c in self.checks]}


def find_port(ports: list[dict] | None = None) -> tuple[str | None, str]:
    """(port, why). Prefers a USB-serial bridge; never a Bluetooth virtual port. None when nothing looks like an ESP32 board."""
    ports = list_ports() if ports is None else ports
    usable = [p for p in ports if not any(w in p["description"].lower() for w in BAD_PORT_WORDS)]
    good = [p for p in usable if any(w in p["description"].lower() for w in GOOD_PORT_WORDS)]
    if len(good) == 1:
        return good[0]["port"], f"{good[0]['port']} ({good[0]['description']})"
    if len(good) > 1:
        return None, "more than one USB-serial port: " + ", ".join(p["port"] for p in good) + " - pass --port to choose"
    if len(usable) == 1:
        return usable[0]["port"], f"{usable[0]['port']} ({usable[0]['description'] or 'unknown device'})"
    skipped = len(ports) - len(usable)
    return None, "no USB-serial port found" + (f" ({skipped} Bluetooth port(s) ignored)" if skipped else "") + ": plug the rover in with a data cable and check Device Manager"


def _num(d: dict | None, k: str, default: float = float("nan")) -> float:
    try:
        return float((d or {}).get(k, default))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ the checks
def check_link(rover, rep: Report, wait: float = 6.0):
    end = time.time() + wait
    while time.time() < end and not (rover.connected or rover.error):
        time.sleep(0.1)
    if rover.error:
        rep.add(Check("link", "USB link", "fail", rover.error))
    elif not rover.connected:
        rep.add(Check("link", "USB link", "fail", "the port opened but the board never became ready: press the board's reset button and try again"))
    else:
        rep.add(Check("link", "USB link", "pass", f"connected on {rover.target}"))


def check_pin_plan(rep: Report, pins: dict | None = None):
    pins = pins or load_pins()
    problems = check_pins(pins)
    if problems:
        rep.add(Check("pin_plan", "Pin plan", "fail", "; ".join(problems), {"pins": pins}))
    else:
        rep.add(Check("pin_plan", "Pin plan", "pass", "no pin is shared and every output uses an output-capable pin", {"pins": pins}))


def check_firmware(rover, rep: Report, pins: dict | None = None):
    h = rover.ask("HELLO", "hello", timeout=2.5)
    if h is None:
        rep.add(Check("firmware", "Firmware", "warn", "the rover answers keys but not the v2 commands: it runs the older firmware (esp32_rover_serial). "
                      "Driving works; the gyro, timed moves and settings need the v2 firmware: flash it with tools/flash.py"))
        return
    reset, brown = str(h.get("reset", "?")), int(_num(h, "brownouts", 0) or 0)
    detail = f"firmware {h.get('fw', '?')}, last reset: {reset}"
    status = "pass"
    if reset == "brownout" or brown > 0:
        status = "warn"
        detail += (f"; the board has reset {brown} time(s) because the supply dipped (brownout). Check the pack voltage with a multimeter, "
                   "the buck setting, thick short power wires, and keep the camera off the same rail while testing")
    if reset in ("panic", "wdt"):
        status = "warn"
        detail += "; the firmware crashed last time: send me runs/rover/<this run>/log.txt"
    want = pins_tuple(pins or load_pins())
    got = tuple(int(x) for x in str(h.get("pins", "")).split(",") if x.strip().lstrip("-").isdigit())
    if got and got != want:
        status = "warn" if status == "pass" else status
        detail += f"; the board was built for pins {list(got)} but the config says {list(want)}: reflash with the right map"
    rep.add(Check("firmware", "Firmware", status, detail, {"hello": h}))


def check_mpu(rover, rep: Report):
    h = rover.hello
    if h and _num(h, "mpu") == 1:
        rep.add(Check("mpu", "MPU6050", "pass", f"found at 0x68 (chip id {h.get('mpu_id', '?')}) on the configured pins"))
        return
    found = []
    for sda, scl in I2C_CANDIDATES:
        r = rover.ask(f"I2CSCAN {sda} {scl}", "i2cscan", timeout=2.0)
        if r and str(r.get("found", "-")) not in ("-", ""):
            found.append((sda, scl, str(r["found"])))
    if found:
        sda, scl, addr = found[0]
        rep.add(Check("mpu", "MPU6050", "warn", f"the board is configured for other pins but a device ({addr}) answers on SDA GPIO{sda} / SCL GPIO{scl}: "
                      "put those pins in the config and reflash (or move the wires)", {"found": found}))
    else:
        rep.add(Check("mpu", "MPU6050", "fail", "no MPU6050 answers on SDA/SCL GPIO21/22, 16/17, 4/5. Check VCC (3V3), GND, SDA, SCL, AD0 to GND. "
                      "If SDA/SCL are on GPIO32/33, those are the RIGHT MOTOR pins: move the wires to GPIO21/22"))


def check_gyro(rover, rep: Report):
    if not rep.get("mpu") or rep.get("mpu").status != "pass":
        rep.add(Check("gyro", "Gyro", "skip", "needs the MPU6050 first"))
        return
    z = rover.ask("CALGYRO", "gyro zero", timeout=4.0)
    g = rover.ask("GYRO", "gyro", timeout=2.0) or {}
    if z is None:
        rep.add(Check("gyro", "Gyro", "warn", "the zeroing did not answer", {"gyro": g}))
        return
    noise, bias = _num(g, "noise"), _num(g, "bias")
    ax, ay, az = _num(g, "ax"), _num(g, "ay"), _num(g, "az")
    status, notes = "pass", [f"bias {bias:.2f} deg/s, noise {noise:.2f} deg/s"]
    if _num(z, "ok", 0) != 1:
        status = "warn"
        notes.append("it could not zero itself: keep the rover completely still and run the check again")
    if noise > 1.0:
        status = "warn"
        notes.append("noisy: check the module is fixed firmly, away from the motors")
    flat = abs(az) > 0.8 and abs(ax) < 0.35 and abs(ay) < 0.35
    if not flat:
        status = "warn"
        notes.append(f"gravity reads x={ax:.2f} y={ay:.2f} z={az:.2f} g: the module is not flat with Z pointing up/down (heading would be wrong): mount it flat")
    elif az < 0:
        notes.append("the module is upside down (Z points down): that is fine, the turn check will set the sign")
    rep.add(Check("gyro", "Gyro", status, "; ".join(notes), {"gyro": g}))


def check_turn(rover, rep: Report, ask: Callable[[str], str] | None, apply: bool = True):
    if ask is None or not rep.get("gyro") or rep.get("gyro").status == "skip":
        rep.add(Check("turn", "Gyro direction", "skip", "needs the gyro and you at the rover"))
        return
    ask("Put the rover flat and still on the table, then press Enter.")
    before = (rover.ask("GYRO", "gyro", 2.0) or {}).get("yaw")
    ask("Now turn it by hand about 90 degrees to the LEFT (anticlockwise seen from above), hold it still, then press Enter.")
    after = (rover.ask("GYRO", "gyro", 2.0) or {}).get("yaw")
    if before is None or after is None:
        rep.add(Check("turn", "Gyro direction", "warn", "the gyro did not answer: repeat the check"))
        return
    d = float(after) - float(before)
    if abs(d) < 25:
        rep.add(Check("turn", "Gyro direction", "warn", f"it only saw {d:.0f} deg: turn the rover further, or the gyro is not reading", {"delta": d}))
        return
    status, notes, data = "pass", [f"measured {d:+.0f} deg for a hand turn of about +90"], {"delta": d}
    if d < 0:
        notes.append("the sign is reversed: setting gyrosign=-1 (RAM only until you confirm saving it)")
        if apply:
            rover.ask("CFG gyrosign=-1", "cfg", 2.0)
        data["gyrosign"] = -1
        status = "warn"
    if not 60 <= abs(d) <= 120:
        status = "warn"
        notes.append("outside 60-120 deg: a hand turn is rough, repeat it, or the gyro scale is off")
    rep.add(Check("turn", "Gyro direction", status, "; ".join(notes), data))


def check_ultrasonic(rover, rep: Report, listen_s: float = 1.6):
    time.sleep(listen_s)
    d = rover.telemetry.get("dist_cm")
    h = rover.hello or {}
    if d is not None and 2 <= d <= 400:
        rep.add(Check("ultrasonic", "HC-SR04", "pass", f"reads {d:.0f} cm. Wave a hand in front of it to see the number change; it is not used to stop the rover until you confirm it",
                      {"dist_cm": d}))
    elif _num(h, "us", 0) == 1:
        rep.add(Check("ultrasonic", "HC-SR04", "warn", "it answered once but the reading is not sensible now: check the ECHO divider and 5 V supply"))
    else:
        rep.add(Check("ultrasonic", "HC-SR04", "skip", "no echo seen (not fitted yet, or not working). TRIG must be on an output pin (GPIO18), ECHO on GPIO35 through the 10k/20k divider, VCC 5 V"))


def check_power(rep: Report, ask: Callable[[str], str] | None):
    if ask is None:
        rep.add(Check("power", "Power", "skip", "needs your multimeter readings"))
        return
    try:
        pack = float(ask("Multimeter: voltage of the 2S pack right now (volts, e.g. 7.9), or empty to skip:").strip().replace(",", ".") or "nan")
        rail = float(ask("Multimeter: voltage at the buck output / ESP32 VIN (volts, e.g. 5.2), or empty to skip:").strip().replace(",", ".") or "nan")
    except ValueError:
        rep.add(Check("power", "Power", "skip", "no numbers given"))
        return
    notes, status = [], "pass"
    if pack == pack:
        if pack < 6.8:
            status, notes = "fail", [f"pack {pack:.1f} V is too low: charge it before driving"]
        elif pack < 7.2:
            status, notes = "warn", [f"pack {pack:.1f} V is nearly empty for a 5.x V buck: expect resets, charge it soon"]
        else:
            notes.append(f"pack {pack:.1f} V is fine")
    if rail == rail:
        if not 5.0 <= rail <= 5.8:
            status = "fail" if status != "fail" else status
            notes.append(f"buck output {rail:.2f} V is outside 5.0-5.8 V: set it with the screw before connecting the boards")
        else:
            notes.append(f"buck output {rail:.2f} V is fine (also re-measure while the motors run, with the pack near 7 V: it must stay above 5.0 V)")
    rep.add(Check("power", "Power", status, "; ".join(notes) or "no numbers given", {"pack_v": pack, "rail_v": rail}))


def check_motors(rover, rep: Report, ask: Callable[[str], str] | None, drive: bool):
    if not drive or ask is None:
        rep.add(Check("motors", "Motors", "skip", "not run (needs --drive and your confirmation that the wheels are off the ground)"))
        return
    ok = ask("Type YES if the rover is on a block with BOTH wheels off the ground and your hand is near the power switch:").strip().lower()
    if ok != "yes":
        rep.add(Check("motors", "Motors", "skip", "you did not confirm the wheels are off the ground"))
        return
    problems, data = [], {}
    for name, (l, r) in (("left forward", (150, 0)), ("right forward", (0, 150)), ("left backward", (-150, 0)), ("right backward", (0, -150))):
        rover.drive_duty(l, r, 700)                 # the firmware ends it by itself after 700 ms
        time.sleep(0.9)
        ans = ask(f"Did the {name.split()[0].upper()} wheel turn {name.split()[1].upper()}? (y = yes, n = it turned the other way, h = hum or no movement)").strip().lower()
        data[name] = ans
        if ans.startswith("n"):
            problems.append(f"{name}: turned the wrong way (swap that motor's two wires, or set inv{name[0].upper()}=-1 after you confirm)")
        elif ans.startswith("h"):
            problems.append(f"{name}: hums or does not move (check the wiring, the motor, and the supply; try a higher minimum duty)")
    rover.stop()
    rep.add(Check("motors", "Motors", "fail" if any("hums" in p for p in problems) else ("warn" if problems else "pass"),
                  "; ".join(problems) or "both wheels turn the right way in both directions", data))


def check_camera(rep: Report, camera_url: str | None, probe: Callable[[str], dict] | None = None):
    if not camera_url:
        rep.add(Check("camera", "Camera", "skip", "no camera address given"))
        return
    r = (probe or _probe_camera)(camera_url)
    if not r.get("ok"):
        rep.add(Check("camera", "Camera", "fail", r.get("error") or "the camera stream could not be read: check the address, the Wi-Fi and the camera's power", r))
        return
    notes, status = [f"{r['fps']:.1f} fps, {r['w']}x{r['h']}"], "pass"
    if r["fps"] < 5:
        status = "warn"
        notes.append("slow: the rover needs about 8-10 fps; check the Wi-Fi signal and the camera's supply")
    if not 30 <= r["brightness"] <= 220:
        status = "warn"
        notes.append(f"brightness {r['brightness']:.0f}/255 is {'too dark' if r['brightness'] < 30 else 'too bright'}: change the light or the exposure")
    if r["sharpness"] < 15:
        status = "warn"
        notes.append("the picture looks blurry or flat: check the focus ring and the lens cover")
    rep.add(Check("camera", "Camera", status, "; ".join(notes), r))


def _probe_camera(url: str, seconds: float = 4.0) -> dict:
    """Grab frames from an MJPEG stream for a few seconds: fps, size, brightness, sharpness (variance of the Laplacian)."""
    import threading

    import cv2
    out: dict = {"ok": False}

    def run():
        cap = cv2.VideoCapture(url)
        if not cap.isOpened():
            out["error"] = "the camera stream did not open: check the address, the Wi-Fi and the camera's power"
            return
        n, t0, br, sh, w, h = 0, time.time(), [], [], 0, 0
        while time.time() - t0 < seconds:
            ok, im = cap.read()
            if not ok:
                break
            n += 1
            h, w = im.shape[:2]
            g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
            br.append(float(g.mean()))
            sh.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
        cap.release()
        if n >= 2:
            out.update(ok=True, fps=n / max(time.time() - t0, 1e-3), w=w, h=h, brightness=sum(br) / len(br), sharpness=sum(sh) / len(sh), frames=n)
        else:
            out["error"] = "the stream opened but no frames arrived"

    th = threading.Thread(target=run, daemon=True, name="camera-probe")
    th.start()
    th.join(seconds + 4.0)
    if th.is_alive():
        out.setdefault("error", "the camera did not answer in time")
    return out


# ------------------------------------------------------------------ the run
def run_bringup(rover, ask: Callable[[str], str] | None = None, camera_url: str | None = None, drive: bool = False,
                pins: dict | None = None, camera_probe: Callable[[str], dict] | None = None, out_dir: Path | None = None,
                listen_s: float = 1.6) -> Report:
    """All checks in a safe order. `ask(prompt) -> text` is how a person answers (None = unattended: the guided checks are skipped)."""
    rep = Report()
    check_pin_plan(rep, pins)
    check_link(rover, rep)
    if rep.get("link").status == "pass":
        check_firmware(rover, rep, pins)
        if "hello" in rep.get("firmware").data:
            check_mpu(rover, rep)
            check_gyro(rover, rep)
            check_turn(rover, rep, ask)
        else:
            for cid, title in (("mpu", "MPU6050"), ("gyro", "Gyro"), ("turn", "Gyro direction")):
                rep.add(Check(cid, title, "skip", "needs the v2 firmware"))
        check_ultrasonic(rover, rep, listen_s)
        check_power(rep, ask)
        check_motors(rover, rep, ask, drive)
    check_camera(rep, camera_url, camera_probe)
    rep.folder = save_report(rep, rover, out_dir)
    return rep


def save_report(rep: Report, rover, out_dir: Path | None = None) -> str:
    folder = (out_dir or RUNS) / time.strftime("%Y%m%d_%H%M%S", time.localtime(rep.started))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps(rep.to_dict(), indent=1, default=str), encoding="utf-8")
    lines = [f"{time.strftime('%H:%M:%S', time.localtime(t))}  {ln}" for t, ln in list(getattr(rover, "lines", []))]
    text = "\n".join(f"[{c.status.upper():4}] {c.title}: {c.detail}" for c in rep.checks) + "\n\n--- everything the firmware said ---\n" + "\n".join(lines) + "\n"
    (folder / "log.txt").write_text(text, encoding="utf-8")
    return str(folder)
