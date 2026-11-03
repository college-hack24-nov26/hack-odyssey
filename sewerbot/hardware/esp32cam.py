"""MJPEG client for the ESP32-CAM (`http://<ip>:81/stream`). Reconnects automatically; keeps only the newest frame."""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np
import requests

SOI, EOI = b"\xff\xd8", b"\xff\xd9"


class MJPEGClient(threading.Thread):
    def __init__(self, url: str, on_frame: Callable[[np.ndarray, float], None], name: str = "esp32cam"):
        super().__init__(daemon=True, name=name)
        self.url, self.on_frame = url, on_frame
        self._stop_evt = threading.Event()
        self.connected = False
        self.fps = 0.0
        self.last_error = ""
        self.frames = 0
        self.last_frame_t = 0.0
        self.resolution = (0, 0)

    def stop(self):
        self._stop_evt.set()

    def run(self):
        backoff = 0.5
        while not self._stop_evt.is_set():
            try:
                with requests.get(self.url, stream=True, timeout=(4, 6)) as r:
                    r.raise_for_status()
                    self.connected, self.last_error, backoff = True, "", 0.5
                    buf = bytearray()
                    t_prev = None
                    for chunk in r.iter_content(chunk_size=4096):
                        if self._stop_evt.is_set():
                            return
                        buf += chunk
                        while True:
                            a = buf.find(SOI)
                            if a < 0:
                                buf.clear()
                                break
                            b = buf.find(EOI, a + 2)
                            if b < 0:
                                if a > 0:
                                    del buf[:a]
                                break
                            jpg = bytes(buf[a:b + 2])
                            del buf[:b + 2]
                            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                            if img is None:
                                continue
                            now = time.time()
                            if t_prev:
                                inst = 1.0 / max(now - t_prev, 1e-3)
                                self.fps = inst if self.fps == 0 else 0.85 * self.fps + 0.15 * inst
                            t_prev = now
                            self.frames += 1
                            self.last_frame_t = now
                            self.resolution = (img.shape[1], img.shape[0])
                            self.on_frame(img, now)
            except Exception as e:
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"[:160]
            self.fps = 0.0
            if self._stop_evt.wait(backoff):
                return
            backoff = min(backoff * 1.7, 5.0)
        self.connected = False
