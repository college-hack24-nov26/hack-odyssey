"""A Session turns a stream of camera frames (simulated or real) into detections, tracked defects and annotated video.

Detection runs on a worker thread that always takes the *newest* frame, so a slow model never delays control.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import cv2
import numpy as np

from .detectors.base import Detection
from .tracker import CameraModel, DefectTracker, PipeFrame

COLORS = {"crack": (60, 60, 255), "leak": (255, 150, 30)}      # BGR
GT_COLOR = (80, 220, 80)


def jpeg(bgr: np.ndarray, q: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes() if ok else b""


def annotate(bgr: np.ndarray, dets: list[Detection], gt: list[dict] | None = None, hud: str = "") -> np.ndarray:
    im = bgr.copy()
    if gt:
        for g in gt:
            if g.get("cls") in COLORS:
                x, y, w, h = g["box"]
                cv2.rectangle(im, (x, y), (x + w, y + h), GT_COLOR, 1)
    for d in dets:
        x, y, w, h = d.box
        col = COLORS.get(d.cls, (200, 200, 200))
        cv2.rectangle(im, (x, y), (x + w, y + h), col, 2)
        label = f"{d.cls}{'/' + d.subtype if d.subtype else ''} {d.conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(im, (x, max(0, y - th - 6)), (x + tw + 6, y), col, -1)
        cv2.putText(im, label, (x + 3, max(th, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    if hud:
        cv2.putText(im, hud, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(im, hud, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return im


class Session:
    def __init__(self, kind: str, detector, show_gt: bool = False):
        self.kind = kind
        self.detector = detector
        self.show_gt = show_gt
        self.cam: CameraModel | None = None
        self.tracker: DefectTracker | None = None
        self.pipe_for_pose = None          # callable(pose_dict) -> PipeFrame | None
        self.log: deque = deque(maxlen=200)
        self.reset()
        self._cv = threading.Condition()
        self._pending = None
        self._stop = False
        self._thr = threading.Thread(target=self._worker, daemon=True, name=f"{kind}-detect")
        self._thr.start()

    # ------------------------------------------------------------------ lifecycle
    def reset(self):
        self.frames_in = 0
        self.frames_done = 0
        self.t_start = time.time()
        self.raw_jpeg: bytes | None = None
        self.view_jpeg: bytes | None = None
        self.last_dets: list[Detection] = []
        self.last_gt: list[dict] = []
        self.pose: dict | None = None
        self.ranges: dict = {}
        self.telemetry: dict = {}
        self.det_ms = 0.0
        self.fps_in = 0.0
        self._t_prev = None
        if self.tracker:
            self.tracker.reset()

    def configure(self, cam: CameraModel):
        self.cam = cam
        self.tracker = DefectTracker(cam)

    def set_detector(self, detector):
        self.detector = detector

    def close(self):
        self._stop = True
        with self._cv:
            self._cv.notify_all()

    def note(self, msg: str):
        self.log.append({"t": round(time.time() - self.t_start, 1), "msg": msg})

    # ------------------------------------------------------------------ input
    def push(self, bgr: np.ndarray, t: float, pose: dict | None = None, ranges: dict | None = None,
             gt: list[dict] | None = None, telemetry: dict | None = None):
        now = time.time()
        if self._t_prev:
            inst = 1.0 / max(now - self._t_prev, 1e-3)
            self.fps_in = inst if self.fps_in == 0 else 0.9 * self.fps_in + 0.1 * inst
        self._t_prev = now
        self.frames_in += 1
        self.pose = pose or self.pose
        self.ranges = ranges if ranges is not None else self.ranges
        if telemetry:
            self.telemetry = telemetry
        self.raw_jpeg = None
        with self._cv:
            self._pending = (bgr, t, pose, gt)
            self._cv.notify()

    # ------------------------------------------------------------------ worker
    def _worker(self):
        while not self._stop:
            with self._cv:
                while self._pending is None and not self._stop:
                    self._cv.wait(0.5)
                item, self._pending = self._pending, None
            if item is None:
                continue
            bgr, t, pose, gt = item
            try:
                t0 = time.perf_counter()
                dets = self.detector.detect(bgr) if self.detector else []
                self.det_ms = 0.8 * self.det_ms + 0.2 * (time.perf_counter() - t0) * 1000 if self.det_ms else (time.perf_counter() - t0) * 1000
                if self.tracker and pose is not None and self.pipe_for_pose:
                    pipe = self.pipe_for_pose(pose)
                    snap = lambda d: jpeg(annotate(bgr, [d]), 85)       # noqa: E731  (best-view snapshot of this detection)
                    self.tracker.update(t, pipe, (pose["x"], pose["y"], pose["yaw"]), dets, snap)
                self.last_dets, self.last_gt = dets, (gt or [])
                n_conf = len(self.tracker.summary()) if self.tracker else 0
                hud = f"{self.detector.name if self.detector else '-'}  {self.det_ms:.0f} ms  in {self.fps_in:.0f} fps  defects {n_conf}"
                self.view_jpeg = jpeg(annotate(bgr, dets, gt if self.show_gt else None, hud))
                self.frames_done += 1
            except Exception as e:                         # never let one bad frame kill the pipeline
                self.note(f"detector error: {type(e).__name__}: {e}")
                self.view_jpeg = jpeg(bgr)

    # ------------------------------------------------------------------ output
    def state(self) -> dict:
        return {
            "kind": self.kind,
            "frames_in": self.frames_in, "frames_done": self.frames_done,
            "fps_in": round(self.fps_in, 1), "det_ms": round(self.det_ms, 1),
            "detector": self.detector.name if self.detector else None,
            "pose": self.pose, "ranges": self.ranges, "telemetry": self.telemetry,
            "detections": [d.to_dict() for d in self.last_dets],
            "defects": self.tracker.summary() if self.tracker else [],
            "log": list(self.log)[-12:],
        }
