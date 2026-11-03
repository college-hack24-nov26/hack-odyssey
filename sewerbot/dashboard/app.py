"""Dashboard backend: Simulation tab + Hardware tab on one FastAPI app (`python -m sewerbot`)."""
from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..brain.detectors import make_detector
from ..brain.hardware import HardwareManager, NotArmed
from ..brain.session import Session
from ..brain.sim import MAPS_DIR, ROOT, SimManager, list_maps
from ..core.mapspec import load_map
from ..core.profiles import PRESETS
from ..hardware.serial_rover import RoverLinkError, list_ports
from simlab.dashboard import STATIC as LAB_STATIC, build_router, chat_router, goal_router, project_router, det_jobs as lab_det_jobs, jobs as lab_jobs, router as lab_router, runner as lab_runner

STATIC = Path(__file__).parent / "static"
MODELS = ROOT / "models"
FIRMWARE = ROOT / "firmware"

app = FastAPI(title="sewerbot")
# The Lab router must be included before the catch-all /api/{kind}/... routes below, or those would shadow it.
app.include_router(lab_router)
app.include_router(build_router)
app.include_router(goal_router)
app.include_router(chat_router)         # /chat and /api/chat/*: also before the catch-all routes below
app.include_router(project_router)      # /api/project/*: also before the catch-all routes below
app.mount("/lab-static", StaticFiles(directory=str(LAB_STATIC)), name="lab-static")


@app.middleware("http")
async def _no_cache(request, call_next):
    """Local dev tool: never serve a stale UI after the code changes."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


_detectors: dict[str, object] = {}
_det_lock = threading.Lock()


# --------------------------------------------------------------------------- detectors
def available_detectors() -> list[dict]:
    out = [{"id": "classical", "label": "Classical OpenCV (no training, low precision)"}]
    if MODELS.exists():
        for d in sorted(MODELS.iterdir()):
            if d.is_dir() and any(d.glob("checkpoint*.pth")):
                out.append({"id": f"rfdetr:{d.name}", "label": f"RF-DETR {d.name} (trained)"})
    return out


def get_detector(det_id: str):
    with _det_lock:
        if det_id not in _detectors:
            if det_id == "classical":
                _detectors[det_id] = make_detector("classical")
            elif det_id.startswith("rfdetr:"):
                name = det_id.split(":", 1)[1]
                size = "small" if "small" in name else "medium" if "medium" in name else "nano"
                _detectors[det_id] = make_detector("rfdetr", weights=str(MODELS / name), size=size)
            else:
                raise HTTPException(400, f"unknown detector {det_id}")
        return _detectors[det_id]


def default_detector_id() -> str:
    """Newest trained model (by checkpoint time), else the classical fallback."""
    best, best_t = "classical", 0.0
    for x in available_detectors():
        if x["id"] == "classical":
            continue
        d = MODELS / x["id"].split(":", 1)[1]
        t = max(p.stat().st_mtime for p in d.glob("checkpoint*.pth"))
        if t > best_t:
            best, best_t = x["id"], t
    return best


sim: SimManager | None = None
hw: HardwareManager | None = None
_mock: dict = {}


@app.on_event("startup")
def _startup():
    global sim, hw
    det = get_detector(default_detector_id())
    sim = SimManager(det)
    hw = HardwareManager(det)


@app.on_event("shutdown")
def _shutdown():
    lab_runner.stop()
    lab_jobs.stop_all()
    lab_det_jobs.stop_all()
    if sim:
        sim.stop()
    if hw:
        hw.disconnect()


def mgr(kind: str):
    if kind == "sim":
        return sim
    if kind == "hw":
        return hw
    raise HTTPException(404, "unknown tab")


# --------------------------------------------------------------------------- catalog
@app.get("/api/catalog")
def catalog():
    maps = {}
    for name in list_maps():
        m = load_map(MAPS_DIR / f"{name}.yaml")
        s = m.summary()
        s["fit"] = {r: {e.id: p.fits(e.section) for e in m.edges.values()} for r, p in PRESETS.items()}
        maps[name] = s
    return {"maps": maps, "rovers": {k: v.to_dict() for k, v in PRESETS.items()},
            "detectors": available_detectors(), "default_detector": default_detector_id()}


class DetectorReq(BaseModel):
    id: str


@app.post("/api/{kind}/detector")
def set_detector(kind: str, req: DetectorReq):
    m = mgr(kind)
    m.session.set_detector(get_detector(req.id))
    return {"ok": True, "detector": m.session.detector.name}


# --------------------------------------------------------------------------- simulation
class SimStart(BaseModel):
    map: str
    rover: str = "mid_4wd"
    overrides: dict = {}
    mission: dict = {"mode": "manual", "goal": None}
    detector: str | None = None
    ambient: float = 0.35
    mode: str = "realtime"
    defect_seed: int | None = None
    defect_count: int | None = None


@app.post("/api/sim/start")
def sim_start(req: SimStart):
    try:
        if req.detector:
            sim.session.set_detector(get_detector(req.detector))
        info = sim.launch(req.map, req.rover, req.overrides, req.mission, req.ambient, req.mode,
                          req.defect_seed, req.defect_count)
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **info}


class FitReq(BaseModel):
    map: str
    rover: str = "mid_4wd"
    overrides: dict = {}


@app.post("/api/sim/fitcheck")
def sim_fitcheck(req: FitReq):
    """Which pipes of the map can this (possibly customised) rover enter and turn in?"""
    from ..core.profiles import get_profile
    try:
        m = load_map(MAPS_DIR / f"{req.map}.yaml")
        p = get_profile(req.rover, **req.overrides)
    except (ValueError, KeyError, FileNotFoundError, TypeError) as e:
        raise HTTPException(400, str(e))
    return {"fit": {e.id: p.fits(e.section) for e in m.edges.values()}}


@app.post("/api/sim/stop")
def sim_stop():
    sim.stop()
    return {"ok": True}


class MissionReq(BaseModel):
    mode: str = "manual"
    goal: str | None = None


@app.post("/api/sim/mission")
def sim_mission(req: MissionReq):
    try:
        sim.set_mission(req.model_dump())
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class DriveReq(BaseModel):
    v: float = 0.0
    w: float = 0.0


@app.post("/api/sim/drive")
def sim_drive(req: DriveReq):
    sim.drive(req.v, req.w)
    return {"ok": True}


@app.post("/api/sim/report")
def sim_report():
    sim.finish()
    return {"report": sim.report_dir.name if sim.report_dir else None, "evaluation": sim.evaluation}


@app.get("/api/sim/truth")
def sim_truth():
    return {"defects": (sim.meta or {}).get("defects", []), "goals": (sim.meta or {}).get("goals", {})}


# --------------------------------------------------------------------------- hardware
class HwConnect(BaseModel):
    rover_host: str = ""
    cam_url: str = ""


@app.post("/api/hw/connect")
def hw_connect(req: HwConnect):
    try:
        hw.connect(req.rover_host, req.cam_url)
    except RoverLinkError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.get("/api/hw/ports")
def hw_ports():
    return {"ports": list_ports()}


class HwArm(BaseModel):
    checklist: dict = {}


@app.post("/api/hw/arm")
def hw_arm(req: HwArm):
    try:
        hw.arm(req.checklist)
    except NotArmed as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/hw/disarm")
def hw_disarm():
    hw.disarm()
    return {"ok": True}


class HwLevel(BaseModel):
    level: int


@app.post("/api/hw/speed-level")
def hw_speed_level(req: HwLevel):
    try:
        return {"level": hw.set_level(req.level)}
    except NotArmed as e:
        raise HTTPException(409, str(e))


@app.post("/api/hw/disconnect")
def hw_disconnect():
    hw.disconnect()
    return {"ok": True}


class HwSetup(BaseModel):
    profile: dict = {}
    pipe_diameter: float | None = None


@app.post("/api/hw/setup")
def hw_setup(req: HwSetup):
    try:
        return {"fit": hw.update_setup(req.profile, req.pipe_diameter)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/hw/drive")
def hw_drive(req: DriveReq):
    try:
        hw.drive(req.v, req.w)
    except NotArmed as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/hw/stop")
def hw_stop():
    hw.stop()
    return {"ok": True}


@app.post("/api/hw/reset-latch")
def hw_reset_latch():
    if hw.rover:
        hw.rover.reset_latch()
    return {"ok": True}


@app.post("/api/hw/capture")
def hw_capture():
    name = hw.capture("manual")
    if not name:
        raise HTTPException(409, "no frame yet")
    return {"saved": name}


class AutoCap(BaseModel):
    seconds: float = 0.0


@app.post("/api/hw/autocapture")
def hw_autocap(req: AutoCap):
    hw.auto_capture = max(0.0, req.seconds)
    return {"ok": True}


class Replay(BaseModel):
    path: str
    fps: float = 6.0
    step_m: float = 0.06


@app.post("/api/hw/replay")
def hw_replay(req: Replay):
    try:
        hw.replay(req.path, req.fps, req.step_m)
    except FileNotFoundError as e:
        raise HTTPException(404, f"not found: {e}")
    return {"ok": True}


@app.post("/api/hw/replay/stop")
def hw_replay_stop():
    hw._replay_stop.set()
    return {"ok": True}


class CalSpeed(BaseModel):
    v: float
    seconds: float
    measured_m: float


class CalYaw(BaseModel):
    w: float
    seconds: float
    measured_deg: float


@app.post("/api/hw/calibrate/speed")
def hw_cal_speed(req: CalSpeed):
    return hw.calibrate_speed(req.v, req.seconds, req.measured_m)


@app.post("/api/hw/calibrate/yaw")
def hw_cal_yaw(req: CalYaw):
    return hw.calibrate_yaw(req.w, req.seconds, req.measured_deg)


class CalRun(BaseModel):
    kind: str            # "speed" | "yaw"
    value: float
    seconds: float


@app.post("/api/hw/calibrate/run")
def hw_cal_run(req: CalRun):
    """Drive (or spin) for a fixed time so the user can measure the result."""
    if not hw.rover or not hw.rover.connected:
        raise HTTPException(409, "rover not connected")
    if hw._needs_arming() and not hw.armed:
        raise HTTPException(409, "not armed: complete the pre-drive checklist first")

    def go():
        t_end = time.time() + req.seconds
        while time.time() < t_end and (hw.armed or not hw._needs_arming()):
            hw.rover.drive(req.value, 0.0) if req.kind == "speed" else hw.rover.drive(0.0, req.value)
            time.sleep(0.1)
        hw.rover.stop()
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True}


class MockReq(BaseModel):
    frames_dir: str = "datasets/raw/straight_t__micro_2wd__s0"
    fps: float = 10.0
    serial: bool = False          # emulate the USB-serial rover (firmware/esp32_rover_serial) instead of the Wi-Fi one


@app.post("/api/hw/mock/start")
def hw_mock_start(req: MockReq):
    from ..hardware.mock import MockCam, MockRover
    if _mock:
        return {"ok": True, "already": True}
    try:
        if not req.serial:
            _mock["rover"] = MockRover()
            _mock["rover"].start()
        _mock["cam"] = MockCam(ROOT / req.frames_dir, 8081, req.fps).start()
    except Exception as e:
        _mock.clear()
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    host = "serial:mock" if req.serial else "127.0.0.1"
    hw.connect(host, "http://127.0.0.1:8081/stream")
    return {"ok": True, "rover_host": host, "cam_url": "http://127.0.0.1:8081/stream"}


@app.post("/api/hw/mock/stop")
def hw_mock_stop():
    hw.disconnect()
    for k in ("cam", "rover"):
        o = _mock.pop(k, None)
        if o:
            o.stop()
    return {"ok": True}


@app.post("/api/hw/firmware/compile")
def fw_compile(target: str):
    fqbn = {"esp32_rover": "esp32:esp32:esp32", "esp32_rover_serial": "esp32:esp32:esp32", "esp32cam_stream": "esp32:esp32:esp32cam",
            "arduino_sensor_hub": "arduino:avr:uno"}.get(target)
    if not fqbn:
        raise HTTPException(404, "unknown firmware target")
    try:
        r = subprocess.run(["arduino-cli", "compile", "--fqbn", fqbn, str(FIRMWARE / target)],
                           capture_output=True, text=True, timeout=600)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-3500:]}


# --------------------------------------------------------------------------- shared: state, video, snapshots
@app.get("/api/{kind}/state")
def state(kind: str):
    return mgr(kind).state()


@app.websocket("/ws/{kind}")
async def ws_state(ws: WebSocket, kind: str):
    await ws.accept()
    m = mgr(kind)
    try:
        while True:
            await ws.send_text(json.dumps(m.state(), default=str))
            await asyncio.sleep(0.15)
    except (WebSocketDisconnect, RuntimeError):
        pass


_PLACEHOLDER = None


def _placeholder() -> bytes:
    global _PLACEHOLDER
    if _PLACEHOLDER is None:
        import cv2
        import numpy as np
        im = np.full((360, 640, 3), 24, np.uint8)
        cv2.putText(im, "waiting for video...", (180, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (120, 130, 150), 2, cv2.LINE_AA)
        _PLACEHOLDER = cv2.imencode(".jpg", im)[1].tobytes()
    return _PLACEHOLDER


@app.get("/api/{kind}/video")
def video(kind: str):
    s: Session = mgr(kind).session

    async def gen():
        last = None
        while True:
            jpg = s.view_jpeg
            if jpg is None:
                jpg = _placeholder()
            if jpg is not last:
                last = jpg
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(0.04)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


@app.get("/api/{kind}/defect/{did}.jpg")
def defect_snapshot(kind: str, did: int):
    s = mgr(kind).session
    for d in (s.tracker.defects if s.tracker else []):
        if d.id == did and d.snapshot:
            return Response(d.snapshot, media_type="image/jpeg")
    raise HTTPException(404, "no snapshot")


@app.get("/api/reports")
def reports():
    base = ROOT / "runs" / "reports"
    items = sorted((p.name for p in base.glob("*") if p.is_dir()), reverse=True) if base.exists() else []
    return {"reports": items}


REPORTS = ROOT / "runs" / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(REPORTS), html=True), name="reports")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
