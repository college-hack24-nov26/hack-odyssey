"""LabRunner: runs one Sim on a background thread at a chosen speed and publishes what the dashboard shows.

The simulation, policy and camera rendering all live on this one thread (an OpenGL context must be used by the thread
that created it); the dashboard only reads the published state.
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..control.policy import Manual, make_policy
from ..envs.core import Sim
from ..spec.task import TaskSpec
from ..spec.vehicle import VehicleSpec

PAUSE_AFTER_EPISODE = 1.2     # s of wall time to look at the result before the next episode starts


def _f(x, nd=4):
    return round(float(x), nd)


class LabRunner:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._req = threading.Lock()
        self._reset_flag = False
        self._policy_req: str | None = None
        self._speed = 1.0
        self.manual = Manual()
        self._state: dict = {"running": False, "status": "idle"}
        self._scene: dict | None = None
        self._jpeg: bytes | None = None
        self._watchers = 0
        self._dets: list = []
        self._det_ms: float | None = None

    # ---------------------------------------------------------------- control (called from request threads)
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, vehicle: VehicleSpec, task: TaskSpec, *, policy: str = "baseline", backend: str = "mujoco",
              speed: float = 1.0, seed: int = 0, loop: bool = True, detector: str | None = None, hazards: dict | None = None) -> None:
        self.stop()
        self._stop.clear()
        self._speed, self._reset_flag, self._policy_req, self._jpeg, self._scene = speed, False, None, None, None
        self._dets, self._det_ms = [], None
        self._state = {"running": True, "status": "starting", "vehicle": vehicle.name, "task": task.name}
        cfg = dict(vehicle=vehicle, task=task, policy=policy, backend=backend, seed=seed, loop=loop, detector=detector, hazards=hazards or {})
        self._thread = threading.Thread(target=self._run, args=(cfg,), daemon=True, name="lab-runner")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8)
        self._thread = None
        self._state = {"running": False, "status": "idle"}

    def reset(self) -> None:
        self._reset_flag = True

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.0, float(speed))

    def set_policy(self, name: str) -> None:
        self._policy_req = name

    def command(self, cmd: dict) -> str:
        """A human command. Takes over from any other policy; returns the policy now in charge."""
        self.manual.set(cmd)
        if self._state.get("policy") != "manual":
            self._policy_req = "manual"
        return "manual"

    def state(self) -> dict:
        return self._state

    def scene(self) -> dict | None:
        return self._scene

    def video_watch(self, delta: int) -> None:
        self._watchers = max(0, self._watchers + delta)

    def jpeg(self) -> bytes | None:
        return self._jpeg

    # ---------------------------------------------------------------- simulation thread
    def _run(self, c: dict) -> None:
        sim = None
        try:
            sim = Sim(c["vehicle"], c["task"], c["backend"], seed=c["seed"], hazards=c.get("hazards") or None)
            self._scene = {**sim.backend.scene(), "vehicle": c["vehicle"].name}
            seed = c["seed"]
            sim.reset(seed=seed)
            policy_name = c["policy"]
            policy = self._build_policy(policy_name, sim)
            stats = {"episodes": 0, "successes": 0, "collisions": 0, "time_to_goal": []}
            cams = [s for s in sim.vehicle.sensors if s.type == "camera"]
            cam = cams[0] if cams else None
            det = None
            if c.get("detector"):
                from ..perception.rfdetr import load_detector
                det = load_detector(c["detector"], conf=0.35)
                self._state = {**self._state, "status": "starting", "detector": Path(c["detector"]).parent.name}
            cam_dt = 1.0 / cam.options["fps"] if cam else 1e9
            next_cam = pub_at = 0.0
            wall0, sim0, frames, fps_t0, fps = time.perf_counter(), sim.t, 0, time.perf_counter(), 0.0
            last_speed = self._speed
            result, pause_until, status, info = None, 0.0, "running", {}
            cmd = sim.space.zeros()
            while not self._stop.is_set():
                now = time.perf_counter()
                if self._policy_req:
                    policy_name, policy = self._policy_req, self._build_policy(self._policy_req, sim)
                    self._policy_req = None
                    policy.reset(sim)
                if self._reset_flag or (pause_until and now >= pause_until):
                    self._reset_flag, pause_until, result = False, 0.0, None
                    seed += 1
                    sim.reset(seed=seed)
                    policy.reset(sim)
                    wall0, sim0 = time.perf_counter(), sim.t
                    status = "running"
                if pause_until:
                    time.sleep(0.03)
                else:
                    cmd = policy.act(sim)
                    _, _, term, trunc, info = sim.step_physical(cmd)
                    frames += 1
                    if term or trunc:
                        stats["episodes"] += 1
                        stats["successes"] += int(info.get("success", False))
                        stats["collisions"] += int(info.get("collisions", 0))
                        if info.get("success"):
                            stats["time_to_goal"].append(sim.t)
                        result = {"success": bool(info.get("success")), "t": _f(sim.t, 2), "return": _f(sim.ret, 2),
                                  "collisions": int(info.get("collisions", 0)), "timeout": bool(trunc), "crash": info.get("crash")}
                        status = "episode done"
                        if c["loop"]:
                            pause_until = time.perf_counter() + PAUSE_AFTER_EPISODE
                        else:
                            self._publish(sim, info, cmd, policy_name, status, stats, fps, result, c)
                            self._stop.wait(0.1)
                            self._state = {**self._state, "status": "finished", "running": True}
                            break
                    speed = self._speed
                    if speed != last_speed:                         # a new speed starts a new pacing reference
                        last_speed, wall0, sim0 = speed, time.perf_counter(), sim.t
                    if speed > 0:                                   # pace to wall time; speed 0 = as fast as possible
                        due = wall0 + (sim.t - sim0) / speed
                        self._paced_wait(due)
                        if time.perf_counter() - due > 1.0:         # cannot keep up: do not try to catch up in a burst
                            wall0, sim0 = time.perf_counter(), sim.t
                if cam and self._watchers and sim.t >= next_cam:
                    next_cam = sim.t + cam_dt
                    bgr = np.ascontiguousarray(sim.backend.render(cam.name)[:, :, ::-1])
                    if det is not None:
                        t_det = time.perf_counter()
                        dets = det.detect(bgr)
                        self._det_ms = (time.perf_counter() - t_det) * 1000
                        self._dets = [{"cls": d.cls, "conf": round(d.conf, 2)} for d in dets]
                        for d in dets:
                            x, y, w, h = d.box
                            cv2.rectangle(bgr, (x, y), (x + w, y + h), (60, 255, 120), 2)
                            cv2.putText(bgr, f"{d.cls} {d.conf:.2f}", (x, max(14, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 255, 120), 2, cv2.LINE_AA)
                    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 82])
                    if ok:
                        self._jpeg = buf.tobytes()
                t = time.perf_counter()
                if t - fps_t0 >= 1.0:
                    fps, frames, fps_t0 = frames / (t - fps_t0), 0, t
                if t - pub_at >= 1 / 30:
                    pub_at = t
                    self._publish(sim, info, cmd, policy_name, status, stats, fps, result, c)
        except Exception as e:  # noqa: BLE001 - surface any failure in the UI instead of dying silently
            self._state = {"running": False, "status": "error", "error": f"{type(e).__name__}: {e}"}
        finally:
            if sim is not None:
                sim.close()

    def _paced_wait(self, due: float) -> None:
        """Sleep until `due` (perf_counter time), but wake early for a stop, reset or policy change."""
        while not self._stop.is_set():
            left = due - time.perf_counter()
            if left <= 0 or self._policy_req or self._reset_flag:
                return
            self._stop.wait(min(left, 0.05))

    def _build_policy(self, name: str, sim: Sim):
        if name == "manual":
            return self.manual
        return make_policy(name, sim)

    def _publish(self, sim: Sim, info: dict, cmd, policy_name: str, status: str, stats: dict, fps: float, result, c: dict):
        st = sim.obs.state
        ttg = stats["time_to_goal"]
        self._state = {
            "running": True, "status": status, "vehicle": c["vehicle"].name, "task": c["task"].name, "policy": policy_name,
            "scene_id": self._scene["id"], "t": _f(st.t, 2), "speed_factor": self._speed,
            "poses": np.round(sim.backend.poses(), 4).tolist(), "markers": sim.task.markers(sim.episode),
            "env": sim.backend.plant.env() if hasattr(getattr(sim.backend, "plant", None), "env") else {},
            "pos": [_f(x) for x in st.pos], "yaw_deg": _f(math.degrees(st.yaw), 1),
            "speed": _f(math.hypot(*st.vel_body[:2])), "omega": _f(st.omega_body[2]), "collided": bool(st.collided),
            "space": sim.space_name, "cmd": {k: _f(v) for k, v in sim.space.as_dict(cmd).items()},
            "info": {k: (_f(v) if isinstance(v, (float, np.floating)) else v) for k, v in info.items() if k != "terms"},
            "terms": {k: _f(v) for k, v in info.get("terms", {}).items()}, "return": _f(sim.ret, 2),
            "result": result, "sim_fps": _f(fps, 0), "camera": next((s.name for s in sim.vehicle.sensors if s.type == "camera"), None),
            "battery": next(({k: _f(v, 3) for k, v in val.items()} for n, val in sim.obs.sensors.items()
                             if isinstance(val, dict) and "soc" in val), None),
            "detector": Path(c["detector"]).parent.name if c.get("detector") else None, "detections": self._dets,
            "detector_ms": _f(self._det_ms, 1) if self._det_ms is not None else None,
            "stats": {"episodes": stats["episodes"], "successes": stats["successes"], "collisions": stats["collisions"],
                      "mean_time": _f(sum(ttg) / len(ttg), 1) if ttg else None},
        }
