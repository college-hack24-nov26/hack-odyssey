"""Hardware manager: real ESP32 rover + ESP32-CAM (or their emulators) behind the same Session as the simulator."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..core.geometry import Section
from ..core.profiles import CameraSpec, RoverProfile
from ..hardware.esp32cam import MJPEGClient
from ..hardware.rover import Esp32Rover
from ..hardware.serial_rover import RoverLinkError, SerialRover, is_serial_target
from .session import Session, annotate, jpeg
from .tracker import CameraModel, PipeFrame

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "hardware.json"
REAL_DIR = ROOT / "datasets" / "real"

# Every box must be ticked before the drive controls of a USB-serial rover unlock (checked here, not only in the browser).
PRE_DRIVE = (("wheels_off", "The wheels are off the ground (the rover sits on a block), or the area around it is clear and I am next to it"),
             ("battery_ok", "The battery pack reads at least 4.4 V on a multimeter (4 fresh AA NiMH)"),
             ("stop_works", "I pressed Stop once and the wheels stopped"),
             ("area_clear", "Nothing and nobody is in the rover's way, and I can reach the power switch"))


class NotArmed(RuntimeError):
    pass

DEFAULT_PROFILE = {
    "name": "esp32_rover", "drive": "diff2wd", "length": 0.16, "width": 0.12, "height": 0.06, "wheel_radius": 0.033,
    "mass": 0.6, "max_speed": 0.20, "max_accel": 0.5, "max_yaw_rate": 1.5, "yaw_gain": 0.85,
    "camera": {"height": 0.08, "tilt_deg": -10.0, "fov_deg": 62.0, "width_px": 640, "height_px": 480, "fps": 12.0,
               "noise": 0.0, "motion_blur_ms": 0.0, "fisheye": False, "auto_exposure": False},
}


class HardwareManager:
    def __init__(self, detector):
        self.session = Session("hw", detector)
        self.profile = RoverProfile.from_dict(DEFAULT_PROFILE)
        self.pipe_d = 0.9
        self.speed_scale = 1.0
        self.rover: Esp32Rover | SerialRover | None = None
        self.armed = False
        self.serial_cal = {"calibrated": False, "level": 180}
        self.cam: MJPEGClient | None = None
        self.rover_host = ""
        self.cam_url = ""
        self.s = 0.0                      # dead-reckoned distance along the (virtual, straight) pipe
        self._t_last = None
        self.replay_state = "idle"
        self._replay_stop = threading.Event()
        self.auto_capture = 0.0
        self._next_auto = 0.0
        self._last_bgr: np.ndarray | None = None
        self._last_dets: list = []
        self.load()
        self._rebuild()

    # ------------------------------------------------------------------ config
    def load(self):
        if CONFIG.exists():
            try:
                d = json.loads(CONFIG.read_text())
                self.profile = RoverProfile.from_dict(d.get("profile", DEFAULT_PROFILE))
                self.pipe_d = float(d.get("pipe_diameter", self.pipe_d))
                self.rover_host, self.cam_url = d.get("rover_host", ""), d.get("cam_url", "")
                self.speed_scale = float(d.get("speed_scale", 1.0))
                self.serial_cal = {**self.serial_cal, **d.get("serial_cal", {})}
            except Exception:
                pass

    def save(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({"profile": self.profile.to_dict(), "pipe_diameter": self.pipe_d,
                                      "rover_host": self.rover_host, "cam_url": self.cam_url,
                                      "speed_scale": self.speed_scale, "serial_cal": self.serial_cal}, indent=1))

    def _rebuild(self):
        self.session.configure(CameraModel.from_profile(self.profile))
        sec = Section.make("circular", self.pipe_d)
        self._pipe = PipeFrame("pipe", np.array([0.0, 0.0]), np.array([1.0, 0.0]), sec, -1e3, 1e6)
        self.session.pipe_for_pose = lambda pose: self._pipe

    def update_setup(self, profile: dict | None = None, pipe_diameter: float | None = None):
        if profile:
            d = self.profile.to_dict()
            cam = {**d["camera"], **profile.pop("camera", {})}
            d.update(profile)
            d["camera"] = cam
            self.profile = RoverProfile.from_dict(d)
        if pipe_diameter:
            self.pipe_d = float(pipe_diameter)
        self._rebuild()
        self.save()
        fit = self.profile.fits(Section.make("circular", self.pipe_d))
        return fit

    # ------------------------------------------------------------------ connection
    def connect(self, rover_host: str | None, cam_url: str | None):
        self.disconnect()
        self.rover_host, self.cam_url = (rover_host or "").strip(), (cam_url or "").strip()
        self.session.reset()
        self.s, self._t_last = 0.0, None
        if self.rover_host:
            if is_serial_target(self.rover_host):
                p = self.profile
                self.rover = SerialRover(self.rover_host, vmax_ref=p.max_speed, yaw_ref=1.5 * p.yaw_gain,
                                         ref_level=int(self.serial_cal["level"]), calibrated=bool(self.serial_cal["calibrated"]))
            else:
                host, _, port = self.rover_host.partition(":")
                self.rover = Esp32Rover(host, int(port) if port else 4210)
            self.session.note(f"rover link -> {self.rover_host}")
        if self.cam_url:
            self.cam = MJPEGClient(self.cam_url, self._on_frame)
            self.cam.start()
            self.session.note(f"camera stream -> {self.cam_url}")
        self.save()

    def disconnect(self):
        self.armed = False
        self._replay_stop.set()
        if self.cam:
            self.cam.stop()
            self.cam = None
        if self.rover:
            self.rover.close()
            self.rover = None

    # ------------------------------------------------------------------ frames
    def _on_frame(self, bgr: np.ndarray, t: float):
        dt = 0.0 if self._t_last is None else min(t - self._t_last, 0.5)
        self._t_last = t
        v = (self.rover.telemetry.get("v", 0.0) if self.rover and self.rover.connected else 0.0)
        self.s += v * self.speed_scale * dt
        self._last_bgr = bgr
        tel = dict(self.rover.telemetry) if self.rover else {}
        self.session.push(bgr, t, {"x": self.s, "y": 0.0, "yaw": 0.0}, None, None, tel)
        if self.auto_capture and t >= self._next_auto:
            self._next_auto = t + self.auto_capture
            self.capture("auto")

    # ------------------------------------------------------------------ safety gate (USB-serial rover)
    def arm(self, checklist: dict) -> None:
        if not (self.rover and getattr(self.rover, "link", "") == "serial"):
            raise NotArmed("connect the USB-serial rover first")
        missing = [text for key, text in PRE_DRIVE if not checklist.get(key)]
        if missing:
            raise NotArmed("tick every item first: " + "; ".join(missing))
        if not self.rover.connected:
            raise NotArmed(self.rover.error or "the rover is still booting: wait a few seconds")
        self.armed = True

    def disarm(self) -> None:
        self.armed = False
        self.stop()

    def _needs_arming(self) -> bool:
        return bool(self.rover and getattr(self.rover, "link", "") == "serial")

    def drive(self, v: float, w: float):
        if self.rover and self._needs_arming():
            if self.rover.error:
                self.armed = False
            if not self.armed:
                raise NotArmed("not armed: complete the pre-drive checklist first")
        if self.rover:
            self.rover.drive(v, w)

    def set_level(self, level: int) -> int:
        if not (self.rover and hasattr(self.rover, "set_level")):
            raise NotArmed("this rover has no speed levels")
        return self.rover.set_level(level)

    def stop(self):
        if self.rover:
            self.rover.stop()

    # ------------------------------------------------------------------ data capture (for fine-tuning on real frames)
    def capture(self, label: str = "manual") -> str | None:
        bgr = self._last_bgr
        if bgr is None:
            return None
        REAL_DIR.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        cv2.imwrite(str(REAL_DIR / f"{name}.jpg"), bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        (REAL_DIR / f"{name}.json").write_text(json.dumps({
            "label": label, "s": round(self.s, 3), "w": bgr.shape[1], "h": bgr.shape[0],
            "prelabels": [d.to_dict() for d in self.session.last_dets],
            "note": "prelabels come from the current detector - review before training"}))
        self.session.note(f"captured {name}.jpg")
        return name

    # ------------------------------------------------------------------ offline replay
    def replay(self, path: str, fps: float = 6.0, step_m: float = 0.06):
        self._replay_stop.set()
        time.sleep(0.05)
        self._replay_stop = threading.Event()
        stop = self._replay_stop
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        self.session.reset()
        self.s, self._t_last = 0.0, None

        def frames():
            if p.is_dir():
                for f in sorted(list(p.glob("*.jpg")) + list(p.glob("*.png"))):
                    im = cv2.imread(str(f))
                    if im is not None:
                        yield im
            else:
                cap = cv2.VideoCapture(str(p))
                src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                skip = max(1, int(round(src_fps / fps)))
                i = 0
                while True:
                    ok, im = cap.read()
                    if not ok:
                        break
                    if i % skip == 0:
                        yield im
                    i += 1
                cap.release()

        def run():
            self.replay_state = "running"
            n = 0
            for im in frames():
                if stop.is_set():
                    break
                self.s += step_m
                n += 1
                self._last_bgr = im
                self.session.push(im, time.time(), {"x": self.s, "y": 0.0, "yaw": 0.0})
                time.sleep(1.0 / fps)
            self.replay_state = f"finished ({n} frames)" if not stop.is_set() else "stopped"

        threading.Thread(target=run, daemon=True, name="hw-replay").start()

    # ------------------------------------------------------------------ calibration
    def calibrate_speed(self, v_cmd: float, seconds: float, measured_m: float) -> dict:
        """vmax_new = vmax_old * d / (v*T)."""
        if self.rover and getattr(self.rover, "link", "") == "serial":
            old, v = self.profile.max_speed, round(measured_m / max(seconds, 1e-6), 4)
            self.profile = self.profile.override(max_speed=min(v, 1.5))
            self.rover.configure(vmax=v)
            self.serial_cal = {"calibrated": True, "level": self.rover.level}
            self.save()
            return {"vmax_old": old, "vmax_new": v, "level": self.rover.level}
        cur = self.rover.telemetry.get("cfg", {}) if self.rover else {}
        vmax_old = float(cur.get("vmax", self.profile.max_speed))
        vmax_new = round(vmax_old * measured_m / max(v_cmd * seconds, 1e-6), 4)
        self.profile = self.profile.override(max_speed=min(vmax_new, 1.5))
        if self.rover:
            self.rover.configure(vmax=vmax_new)
        self.save()
        return {"vmax_old": vmax_old, "vmax_new": vmax_new}

    def calibrate_yaw(self, w_cmd: float, seconds: float, measured_deg: float) -> dict:
        """yaw_gain_new = yaw_gain_old * (actual rate / commanded rate)."""
        actual = np.radians(measured_deg) / max(seconds, 1e-6)
        if self.rover and getattr(self.rover, "link", "") == "serial":
            g_new = round(float(np.clip(actual / 1.5, 0.1, 1.5)), 3)
            old = self.profile.yaw_gain
            self.profile = self.profile.override(yaw_gain=g_new)
            self.rover.configure(yaw_gain=g_new)
            self.save()
            return {"yaw_gain_old": old, "yaw_gain_new": g_new}
        g_new = round(float(np.clip(self.profile.yaw_gain * actual / max(abs(w_cmd), 1e-6), 0.1, 1.5)), 3)
        self.profile = self.profile.override(yaw_gain=g_new)
        if self.rover:
            self.rover.configure(yaw_gain=g_new)
        self.save()
        return {"yaw_gain_old": self.profile.yaw_gain, "yaw_gain_new": g_new}

    # ------------------------------------------------------------------ dashboard
    def state(self) -> dict:
        s = self.session.state()
        r, c = self.rover, self.cam
        s.update({
            "rover_connected": bool(r and r.connected), "rover_host": self.rover_host,
            "rover_rtt_ms": round(r.rtt_ms, 0) if r else None,
            "rover_link": getattr(r, "link", "udp") if r else None, "rover_info": r.info() if r and hasattr(r, "info") else None,
            "armed": bool(self.armed and r and not getattr(r, "error", "")), "pre_drive": [{"id": k, "text": t} for k, t in PRE_DRIVE],
            "cam_connected": bool(c and c.connected), "cam_url": self.cam_url, "cam_fps": round(c.fps, 1) if c else 0,
            "cam_resolution": list(c.resolution) if c else None, "cam_error": c.last_error if c else "",
            "profile": self.profile.to_dict(), "pipe_diameter": self.pipe_d, "s": round(self.s, 2),
            "replay": self.replay_state, "auto_capture": self.auto_capture,
            "captured": len(list(REAL_DIR.glob("*.jpg"))) if REAL_DIR.exists() else 0,
            "fit": self.profile.fits(Section.make("circular", self.pipe_d)),
        })
        return s
