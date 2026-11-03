"""Software stand-ins for the ESP32 rover (UDP) and the ESP32-CAM (HTTP MJPEG).

They let the Hardware tab be exercised end-to-end without the boards:
    python -m sewerbot.hardware.mock --frames datasets/raw/straight_t__micro_2wd__s0
The mock rover reproduces the firmware's failsafe (stop 300 ms after the last command) and low-battery latch.
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .rover import PORT


class MockRover(threading.Thread):
    def __init__(self, port: int = PORT, host: str = "127.0.0.1", vmax: float = 0.25, track: float = 0.10):
        super().__init__(daemon=True, name="mock-rover")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        self.sock.bind((host, port))
        self.sock.settimeout(0.05)
        self.vmax, self.track = vmax, track
        self.v = self.w = 0.0
        self.last_cmd = 0.0
        self.vbat = 5.35
        self.latched = False
        self.cfg = {"track": track, "vmax": vmax, "yaw_gain": 0.85, "speed_scale": 1.0}
        self.t0 = time.time()
        self.odo = 0.0
        self._stop_evt = threading.Event()
        self.addr = None

    def stop(self):
        self._stop_evt.set()

    def run(self):
        last_tel = 0.0
        last_t = time.time()
        while not self._stop_evt.is_set():
            try:
                data, self.addr = self.sock.recvfrom(1024)
                m = json.loads(data)
                if m.get("t") == "cmd":
                    self.v, self.w, self.last_cmd, self._ts = float(m["v"]), float(m["w"]), time.time(), m.get("ts")
                elif m.get("t") == "stop":
                    self.v = self.w = 0.0
                elif m.get("t") == "reset":
                    self.latched = False
                elif m.get("t") == "cfg":
                    self.cfg.update({k: v for k, v in m.items() if k != "t"})
            except (socket.timeout, ValueError, ConnectionResetError):
                pass
            now = time.time()
            dt, last_t = now - last_t, now
            if now - self.last_cmd > 0.3 or self.latched:        # failsafe, exactly like the firmware
                self.v = self.w = 0.0
            self.vbat -= 0.00002 * abs(self.v) * dt * 1000 + 0.00001 * dt
            if self.vbat < 4.0:
                self.latched = True
            self.odo += self.v * self.cfg["speed_scale"] * dt
            if now - last_tel > 0.1 and self.addr:
                last_tel = now
                tel = {"t": "tel", "vbat": round(self.vbat, 2), "fault": 0, "latched": int(self.latched), "rssi": -52,
                       "up": round(now - self.t0, 1), "v": round(self.v, 3), "w": round(self.w, 3), "cfg": self.cfg,
                       "ts": getattr(self, "_ts", None)}
                try:
                    self.sock.sendto(json.dumps(tel).encode(), self.addr)
                except OSError:
                    pass


class MockCam:
    """HTTP server with /stream (multipart MJPEG), /capture and /status, cycling through JPEG files."""

    def __init__(self, frames_dir: str | Path, port: int = 8081, fps: float = 10.0):
        self.files = sorted(Path(frames_dir).glob("*.jpg"))
        if not self.files:
            raise FileNotFoundError(f"no .jpg files in {frames_dir}")
        self.fps, self.idx = fps, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while True:
                            jpg = outer.files[outer.idx % len(outer.files)].read_bytes()
                            outer.idx += 1
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                            time.sleep(1.0 / outer.fps)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                elif self.path.startswith("/capture"):
                    jpg = outer.files[outer.idx % len(outer.files)].read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpg)))
                    self.end_headers()
                    self.wfile.write(jpg)
                else:
                    body = json.dumps({"mock": True, "framesize": 8, "frames": len(outer.files)}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="mock-cam")

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()


class MockSerialPort:
    """pyserial-like fake of firmware/esp32_rover_serial: same keys, same replies, the same 1 s watchdog and obstacle stop.
    Set `obstacle_cm` / `ir_hit` to move an imaginary wall; `odo_m` is the distance driven; `history` lists (time, state)."""
    WATCHDOG_S = 1.0
    STOP_CM = 15.0

    def __init__(self, ultrasonic: bool = True, ir: bool = True, mps_per_level: float = 0.2 / 180):
        import queue
        self.is_open = True
        self.ultrasonic, self.ir = ultrasonic, ir
        self.obstacle_cm, self.ir_hit = 400.0, False
        self.level, self.state, self.odo_m, self.mps_per_level = 180, "S", 0.0, mps_per_level
        self.history: list[tuple[float, str]] = []
        self._in, self._out = queue.Queue(), queue.Queue()
        self._t0 = time.time()
        self._last_byte = time.time()
        self._stop_evt = threading.Event()
        self._say("Rover ready. w/a/s/d to drive, space to stop, t for self-test.")
        threading.Thread(target=self._run, daemon=True, name="mock-serial-rover").start()

    # ---- the pyserial surface the driver uses
    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise OSError("port closed")
        for b in data:
            self._in.put(chr(b))
        return len(data)

    def readline(self) -> bytes:
        import queue
        if not self.is_open:
            raise OSError("port closed")
        try:
            return (self._out.get(timeout=0.05) + chr(10)).encode()
        except queue.Empty:
            return b""

    def close(self):
        self.is_open = False
        self._stop_evt.set()

    # ---- the firmware
    def _say(self, line: str):
        self._out.put(line)

    def _go(self, state: str, name: str):
        self.state = state
        self.history.append((time.time() - self._t0, state))
        self._say(name)

    def _key(self, c: str):
        c = c.lower()
        if c == "w":
            if (self.ultrasonic and not 0 <= self.obstacle_cm >= self.STOP_CM) or (self.ir and self.ir_hit):
                self._go("S", "obstacle - refused")           # no valid reading (< 0) counts as blocked, like the firmware
            else:
                self._go("F", "forward")
        elif c == "s":
            self._go("B", "backward")
        elif c == "a":
            self._go("L", "spin left")
        elif c == "d":
            self._go("R", "spin right")
        elif c in (" ", "x"):
            self._go("S", "stop")
        elif c == "e":
            self.level = min(255, self.level + 25)
            self._say(f"speed {self.level}")
        elif c == "q":
            self.level = max(100, self.level - 25)
            self._say(f"speed {self.level}")
        elif c == "?":
            self._tel()

    def _tel(self):
        parts = [f"up={time.time() - self._t0:.1f}", f"speed={self.level}", f"dir={self.state}"]
        if self.ultrasonic:
            parts.append(f"dist={self.obstacle_cm:.1f}")
        if self.ir:
            parts.append(f"ir={int(self.ir_hit)}")
        self._say("tel " + " ".join(parts))

    def _run(self):
        import queue
        last_tel = last = time.time()
        while not self._stop_evt.is_set():
            try:
                while True:
                    self._key(self._in.get_nowait())
                    self._last_byte = time.time()
            except queue.Empty:
                pass
            now = time.time()
            dt, last = now - last, now
            if self.state != "S" and now - self._last_byte > self.WATCHDOG_S:
                self._go("S", "link lost - stopped")
            if self.state == "F" and ((self.ultrasonic and not 0 <= self.obstacle_cm >= self.STOP_CM) or (self.ir and self.ir_hit)):
                self._go("S", "obstacle - stopped")
            if self.state in "FB":
                self.odo_m += (1 if self.state == "F" else -1) * self.level * self.mps_per_level * dt
            if now - last_tel > 0.5:
                last_tel = now
                self._tel()
            time.sleep(0.01)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--cam-port", type=int, default=8081)
    ap.add_argument("--fps", type=float, default=10.0)
    a = ap.parse_args()
    MockRover().start()
    MockCam(a.frames, a.cam_port, a.fps).start()
    print(f"mock rover on udp 127.0.0.1:{PORT}, mock camera on http://127.0.0.1:{a.cam_port}/stream (Ctrl+C to stop)")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
