"""USB-serial client for the ESP32 rover (firmware/esp32_rover_serial, or the older rover_wasd sketch). Same surface as Esp32Rover.

host -> rover : single ASCII keys  w forward · s back · a spin left · d spin right · x stop · e / q speed +25 / -25
                . heartbeat · ? status line · t self-test (blocks the rover for ~5 s)
rover -> host : text lines  'forward' 'backward' 'spin left' 'spin right' 'stop' 'speed N' 'link lost - stopped' 'obstacle - stopped'
                and, with the new firmware, 'tel up=12 speed=180 dir=F dist=23.5 ir=0'

Safety the firmware cannot give by itself: the first byte after the boot delay is a stop, a heartbeat follows every 0.2 s,
a drive command lapses after `hold` seconds, stop() is the first thing close() does, and a forward command is refused while the
distance sensor (or an IR sensor) reports an obstacle, or has gone silent after it had been seen.
"""
from __future__ import annotations

import re
import threading
import time

LEVEL_MIN, LEVEL_MAX, LEVEL_STEP, LEVEL_DEFAULT = 100, 255, 25, 180
HEARTBEAT_S = 0.2
TX_PERIOD = 0.05
SENSOR_STALE_S = 1.5
_TEL = re.compile(r"(\w+)=(-?[\w.]+)")
_MOTION = {"forward": "F", "backward": "B", "spin left": "L", "spin right": "R", "stop": "S"}
_SIGN = {"F": (1, 0), "B": (-1, 0), "L": (0, 1), "R": (0, -1), "S": (0, 0)}      # (linear, angular); positive angular = left


class RoverLinkError(RuntimeError):
    """The serial port could not be opened or the link broke. The message says what to do about it."""


def list_ports() -> list[dict]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [{"port": p.device, "description": p.description or ""} for p in sorted(lp.comports(), key=lambda p: p.device)]


def is_serial_target(host: str) -> bool:
    h = (host or "").strip().lower()
    return h.startswith("serial:") or h.startswith("com") and h[3:].isdigit() or h.startswith("/dev/tty")


def open_port(target: str, baud: int):
    """`serial:COM6`, `COM6`, `/dev/ttyUSB0`, or `serial:mock` (the built-in firmware emulator)."""
    name = target.strip()
    if name.lower().startswith("serial:"):
        name = name[7:]
    if name.lower() == "mock":
        from .mock import MockSerialPort
        return MockSerialPort()
    try:
        import serial
    except ImportError as e:
        raise RoverLinkError("pyserial is not installed: run  uv pip install pyserial") from e
    try:
        return serial.Serial(name, baud, timeout=0.05, write_timeout=0.5)
    except serial.SerialException as e:
        msg = str(e)
        if "PermissionError" in msg or "Access is denied" in msg or "busy" in msg.lower():
            raise RoverLinkError(f"{name} is busy: close the Arduino serial monitor and any other script that uses it (only one program can hold a COM port)") from e
        if "FileNotFoundError" in msg or "could not open port" in msg:
            raise RoverLinkError(f"{name} was not found: check the USB cable and the port number in Device Manager") from e
        raise RoverLinkError(f"{name}: {msg}") from e


class SerialRover:
    link = "serial"

    def __init__(self, target: str, baud: int = 115200, opener=open_port, boot_wait: float = 2.5, guard_cm: float = 15.0,
                 vmax_ref: float = 0.20, yaw_ref: float = 1.3, ref_level: int = LEVEL_DEFAULT, calibrated: bool = False):
        self.target, self.baud = target, baud
        self.guard_cm = float(guard_cm)
        self.ref_level, self.ref_speed, self.ref_yaw = int(ref_level), float(vmax_ref), float(yaw_ref)
        self.calibrated = bool(calibrated)
        self.level = LEVEL_DEFAULT
        self.telemetry: dict = {}
        self.tel_t = 0.0
        self.rtt_ms = 0.0
        self.error = ""
        self.blocked = ""
        self.sent = 0
        self.sensors = {"dist": 0.0, "ir": 0.0}                 # time each sensor was last heard from (0 = never)
        self.last_line = ""
        self._ser = opener(target, baud)
        self._open_t = time.time()
        self._boot_wait = boot_wait
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._v = self._w = 0.0
        self._until = 0.0
        self._sent_key: str | None = None
        self._dir = "S"
        self._last_hb = 0.0
        self._rx = threading.Thread(target=self._rx_loop, daemon=True, name="serial-rx")
        self._tx = threading.Thread(target=self._tx_loop, daemon=True, name="serial-tx")
        self._rx.start()
        self._tx.start()

    # ------------------------------------------------------------------ api (same as Esp32Rover)
    @property
    def ready(self) -> bool:
        return time.time() - self._open_t >= self._boot_wait

    @property
    def connected(self) -> bool:
        return self._ser is not None and not self.error and self.ready and not self._stop.is_set()

    def drive(self, v: float, w: float, hold: float = 0.4):
        """Keep asking for (v, w) for `hold` seconds, then fall back to stop. The firmware has no arcs: the dominant part wins."""
        with self._lock:
            self._v, self._w, self._until = float(v), float(w), time.time() + hold

    def stop(self):
        with self._lock:
            self._v = self._w = 0.0
            self._until = 0.0
        self._write(b"x")
        self._sent_key = "x"

    def reset_latch(self):
        """No battery latch in this firmware: nothing to reset."""

    def configure(self, **cfg):
        """vmax = measured speed (m/s) at the current speed level, yaw_gain scales the assumed spin rate."""
        if cfg.get("vmax"):
            self.ref_speed, self.ref_level, self.calibrated = float(cfg["vmax"]), self.level, True
        if cfg.get("yaw_gain"):
            self.ref_yaw = 1.5 * float(cfg["yaw_gain"])
        if cfg.get("guard_cm") is not None:
            self.guard_cm = float(cfg["guard_cm"])

    def set_level(self, level: int) -> int:
        """Move the rover's speed level toward `level` (steps of 25, 100..255). Returns the level it expects to reach."""
        target = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
        if target <= LEVEL_MIN or target >= LEVEL_MAX:                # the firmware's steps (180, 155, 130, 105, 100) end short: overshoot
            steps = -8 if target <= LEVEL_MIN else 8
            new = target
        else:
            steps = round((target - self.level) / LEVEL_STEP)
            new = max(LEVEL_MIN, min(LEVEL_MAX, self.level + steps * LEVEL_STEP))
        if steps and new != self.level:
            self._write((b"e" if steps > 0 else b"q") * abs(steps))
            self.level = new
        return self.level

    def close(self):
        self.stop()
        self._write(b"x")
        self._stop.set()
        time.sleep(0.05)
        try:
            self._ser.close()
        except Exception:
            pass

    def info(self) -> dict:
        now = time.time()
        age = lambda t: round(now - t, 2) if t else None            # noqa: E731
        return {"link": "serial", "port": self.target, "baud": self.baud, "ready": self.ready, "error": self.error,
                "level": self.level, "blocked": self.blocked, "calibrated": self.calibrated, "guard_cm": self.guard_cm,
                "ref_speed_mps": round(self.ref_speed, 3), "last_line": self.last_line,
                "dist_cm": self.telemetry.get("dist_cm"), "dist_age_s": age(self.sensors["dist"]),
                "ir": self.telemetry.get("ir"), "ir_age_s": age(self.sensors["ir"]),
                "firmware_telemetry": bool(self.tel_t), "rx_age_s": age(self.tel_t)}

    # ------------------------------------------------------------------ io
    def _write(self, data: bytes):
        if self.error or self._ser is None:
            return
        try:
            self._ser.write(data)
            self.sent += len(data)
        except Exception as e:
            self.error = f"serial write failed ({type(e).__name__}): the cable was unplugged or the port was taken; the rover stops by itself within 1 s"

    def _key_for(self, v: float, w: float) -> str:
        if abs(v) < 1e-3 and abs(w) < 1e-3:
            return "x"
        if abs(w) / 1.5 > abs(v) / max(self.ref_speed, 1e-3):
            return "a" if w > 0 else "d"
        return "w" if v > 0 else "s"

    def _guard(self, key: str) -> str:
        """Why a forward command must not go out right now ('' = fine)."""
        if key != "w":
            return ""
        now = time.time()
        d, ir = self.telemetry.get("dist_cm"), self.telemetry.get("ir")
        if self.sensors["dist"]:
            if now - self.sensors["dist"] > SENSOR_STALE_S:
                return "the distance sensor has gone silent"
            if d is None:
                return "the distance sensor gives no valid reading (check its wiring)"
            if d < self.guard_cm:
                return f"obstacle {d:.0f} cm ahead"
        if self.sensors["ir"]:
            if now - self.sensors["ir"] > SENSOR_STALE_S:
                return "the IR sensor has gone silent"
            if ir:
                return "the IR sensor sees an obstacle"
        return ""

    def _tx_loop(self):
        while not self._stop.is_set():
            self._stop.wait(TX_PERIOD)
            if not self.ready or self.error:
                continue
            now = time.time()
            if now - self._last_hb >= HEARTBEAT_S:
                self._last_hb = now
                self._write(b".")
            with self._lock:
                active = now < self._until
                v, w = (self._v, self._w) if active else (0.0, 0.0)
            key = self._key_for(v, w)
            why = self._guard(key)
            self.blocked = why
            if why:
                key = "x"
            if key != self._sent_key:
                self._sent_key = key
                self._write(key.encode())
                if key in "wsad":
                    self._dir = {"w": "F", "s": "B", "a": "L", "d": "R"}[key]
                elif key == "x":
                    self._dir = "S"
            self._update_estimate()

    def _update_estimate(self):
        lin, ang = _SIGN[self._dir]
        scale = self.level / max(self.ref_level, 1)
        self.telemetry["v"] = lin * self.ref_speed * scale
        self.telemetry["w"] = ang * self.ref_yaw * scale

    def _rx_loop(self):
        while not self._stop.is_set():
            try:
                raw = self._ser.readline()
            except Exception as e:
                if not self._stop.is_set():
                    self.error = f"serial read failed ({type(e).__name__}): the cable was unplugged or the port was taken"
                return
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if line:
                self._on_line(line)

    def _on_line(self, line: str):
        now = time.time()
        self.last_line = line
        if line.startswith("tel "):
            self.tel_t = now
            for k, v in _TEL.findall(line[4:]):
                try:
                    val: float | str = float(v)
                except ValueError:
                    val = v
                if k == "dist":
                    self.sensors["dist"] = now
                    self.telemetry["dist_cm"] = val if isinstance(val, float) and val >= 0 else None
                elif k == "ir":
                    if isinstance(val, float) and val >= 0:
                        self.sensors["ir"] = now
                        self.telemetry["ir"] = int(val)
                elif k == "speed" and isinstance(val, float):
                    self.level = int(val)
                    self.telemetry["level"] = self.level
                elif k == "dir":
                    self.telemetry["dir"] = val
                elif k == "up":
                    self.telemetry["up"] = val
            return
        m = re.match(r"speed (\d+)", line)
        if m:
            self.level = int(m.group(1))
            self.telemetry["level"] = self.level
        elif line in _MOTION:
            self.telemetry["dir"] = _MOTION[line]
        elif "link lost" in line or "obstacle" in line:
            self.telemetry["last_stop"] = line
            self._dir = "S"
            self._sent_key = "x"
