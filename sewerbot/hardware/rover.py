"""UDP client for the ESP32 rover firmware (firmware/esp32_rover). One JSON datagram per message.

host -> rover : {"t":"cmd","v":m/s,"w":rad/s}   sent every 50 ms; the rover stops 300 ms after the last one
                {"t":"stop"} | {"t":"reset"} (clear the low-battery latch) | {"t":"cfg", "track":m, "vmax":m/s, ...}
rover -> host : {"t":"tel","vbat":V,"fault":0|1,"latched":0|1,"rssi":dBm,"up":s,"v":..,"w":..,"cfg":{...}}
"""
from __future__ import annotations

import json
import socket
import threading
import time

PORT = 4210
SEND_PERIOD = 0.05


class Esp32Rover:
    TS_WRAP = 1000.0          # the ESP32 parses numbers as float32: keep timestamps small (resolution ~0.1 ms)

    def _now(self) -> float:
        return (time.time() - self._t0) % self.TS_WRAP

    def __init__(self, host: str, port: int = PORT):
        self._t0 = time.time()
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SIO_UDP_CONNRESET"):      # Windows: ICMP 'port unreachable' must not break recvfrom()
            self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        self.sock.settimeout(0.2)
        self._v = self._w = 0.0
        self._until = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.telemetry: dict = {}
        self.tel_t = 0.0
        self.rtt_ms = 0.0
        self.sent = 0
        self._tx = threading.Thread(target=self._tx_loop, daemon=True, name="rover-tx")
        self._rx = threading.Thread(target=self._rx_loop, daemon=True, name="rover-rx")
        self._tx.start()
        self._rx.start()

    # ------------------------------------------------------------------ api
    @property
    def connected(self) -> bool:
        return time.time() - self.tel_t < 1.5

    def drive(self, v: float, w: float, hold: float = 0.4):
        """Keep sending (v, w) for `hold` seconds, then fall back to stop."""
        with self._lock:
            self._v, self._w, self._until = float(v), float(w), time.time() + hold

    def stop(self):
        with self._lock:
            self._v = self._w = 0.0
            self._until = 0.0
        self._send({"t": "stop"})

    def reset_latch(self):
        self._send({"t": "reset"})

    def configure(self, **cfg):
        self._send({"t": "cfg", **cfg})

    def close(self):
        self.stop()
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------ io
    def _send(self, msg: dict):
        try:
            self.sock.sendto(json.dumps(msg, separators=(",", ":")).encode(), self.addr)
            self.sent += 1
        except OSError:
            pass

    def _tx_loop(self):
        while not self._stop.is_set():
            with self._lock:
                active = time.time() < self._until
                v, w = (self._v, self._w) if active else (0.0, 0.0)
            self._send({"t": "cmd", "v": round(v, 3), "w": round(w, 3), "ts": round(self._now(), 3)})
            self._stop.wait(SEND_PERIOD)

    def _rx_loop(self):
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(2048)
            except (socket.timeout, ConnectionResetError):
                continue            # peer unreachable right now: keep listening, the link may come back
            except OSError:
                return
            try:
                m = json.loads(data)
            except ValueError:
                continue
            if m.get("t") == "tel":
                self.telemetry, self.tel_t = m, time.time()
                if m.get("ts"):       # round-trip: our wrapped clock now minus the echoed send time
                    self.rtt_ms = ((self._now() - float(m["ts"])) % self.TS_WRAP) * 1000
                    if self.rtt_ms > 5000:
                        self.rtt_ms = 0.0
