"""HTTP/WebSocket API of the Lab tab. Include it in a FastAPI app *before* any catch-all `/api/{kind}/...` routes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..catalog import get_vehicle, save_vehicle
from ..registry import TASKS
from ..perception.train import RUNS as DET_RUNS
from ..runtime.runner import LabRunner
from ..train.jobs import JobManager
from ..spec.task import TaskSpec
from ..spec.vehicle import SpecError, VehicleSpec
from ..tasks import get_task_spec
from ..vehicles.importer import draft_spec
from .catalog import build_catalog, validate_vehicle

router = APIRouter()
runner = LabRunner()
jobs = JobManager()
det_jobs = JobManager(DET_RUNS, module="simlab.perception", args=("pipeline",), model_file="model/detector.json", tag="det")


class DetectReq(BaseModel):
    vehicle: dict | str
    task: dict | str
    images: int = 600
    epochs: int = 20
    size: str = "nano"
    seed: int = 0


class TrainReq(BaseModel):
    vehicle: dict | str
    task: dict | str
    algo: str = "ppo"
    total_steps: int = 200_000
    n_envs: int = 8
    seed: int = 0
    curriculum_fraction: float = 0.0
    eval_every: int = 20_000
    eval_episodes: int = 20


class RunReq(BaseModel):
    id: str


class VehicleReq(BaseModel):
    vehicle: dict


class StartReq(BaseModel):
    vehicle: dict | str
    task: dict | str
    policy: str = "baseline"
    backend: str = "mujoco"
    speed: float = 1.0
    seed: int = 0
    loop: bool = True
    detector: str | None = None       # a model directory made by the perception pipeline: its boxes are drawn on the camera view
    hazards: dict[str, float] = {}    # fixed conditions for the run (wave_hs, wind_ms, current_ms ...); clamped to what the plants accept


class CommandReq(BaseModel):
    u: dict[str, float]


def _vehicle(v: dict | str) -> VehicleSpec:
    try:
        return get_vehicle(v) if isinstance(v, str) else VehicleSpec.from_dict(v)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except SpecError as e:
        raise HTTPException(422, f"invalid vehicle: {e}")


def _task(t: dict | str) -> TaskSpec:
    try:
        return get_task_spec(t) if isinstance(t, str) else TaskSpec.from_dict(t)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except SpecError as e:
        raise HTTPException(422, f"invalid task: {e}")


@router.get("/api/lab/catalog")
def catalog():
    return {**build_catalog(), "models": jobs.models(), "detectors": det_jobs.models()}


@router.post("/api/lab/validate")
def validate(req: VehicleReq):
    return validate_vehicle(req.vehicle)


class ImportReq(BaseModel):
    path: str
    name: str | None = None


@router.post("/api/lab/import")
def import_model(req: ImportReq):
    """A draft custom-vehicle spec (with its checks) for an MJCF or URDF file on this machine."""
    if Path(req.path).suffix.lower() not in (".xml", ".mjcf", ".urdf"):
        raise HTTPException(422, "expected a MuJoCo (.xml / .mjcf) or URDF (.urdf) file")
    try:
        return validate_vehicle(draft_spec(req.path, req.name))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e).strip("'\""))


@router.post("/api/lab/save")
def save(req: VehicleReq):
    spec = _vehicle(req.vehicle)
    try:
        return {"path": str(save_vehicle(spec))}
    except SpecError as e:
        raise HTTPException(422, str(e))


LIVE_HAZARDS = ("wave_hs", "wind_ms", "current_ms", "grip_mu", "drag_ms2", "cmd_delay_s", "cmd_dropout")


def _clean_hazards(h: dict) -> dict:
    """Only the knobs a person may set from the Lab, each clamped to its physical range; zero (no hazard) is dropped."""
    from ..llm.menu import RANGES
    out = {}
    for k, v in (h or {}).items():
        if k in LIVE_HAZARDS and isinstance(v, (int, float)):
            lo, hi = (0.0, 20.0) if k == "drag_ms2" else RANGES[k]          # drag here is absolute m/s^2 (the Lab scales a share by the vehicle's acceleration)
            x = max(lo, min(hi, float(v)))
            if k == "grip_mu" and x >= 0.8:
                continue
            if k != "grip_mu" and x <= 0.0:
                continue
            out[k] = x
    return out


@router.post("/api/lab/start")
def start(req: StartReq):
    vehicle, task = _vehicle(req.vehicle), _task(req.task)
    try:
        vehicle.check()
        cls = TASKS.get(task.task)
        if req.backend not in cls.backends:
            raise HTTPException(422, f"task {task.name!r} runs on {list(cls.backends)}, not on {req.backend!r}")
        cls(task, vehicle)                                   # a vehicle/task mismatch is the user's to fix: say so now
        runner.start(vehicle, task, policy=req.policy, backend=req.backend, speed=req.speed, seed=req.seed, loop=req.loop,
                     detector=req.detector, hazards=_clean_hazards(req.hazards))
    except (KeyError, SpecError) as e:
        raise HTTPException(422, str(e).strip("'\""))
    return {"ok": True}


@router.post("/api/lab/stop")
def stop():
    runner.stop()
    return {"ok": True}


@router.post("/api/lab/reset")
def reset():
    runner.reset()
    return {"ok": True}


class PolicyReq(BaseModel):
    policy: str


class SpeedReq(BaseModel):
    speed: float


@router.post("/api/lab/policy")
def policy(req: PolicyReq):
    runner.set_policy(req.policy)
    return {"ok": True}


@router.post("/api/lab/speed")
def speed(req: SpeedReq):
    runner.set_speed(req.speed)
    return {"ok": True}


@router.post("/api/lab/command")
def command(req: CommandReq):
    return {"policy": runner.command(req.u)}


@router.get("/api/train/runs")
def train_runs():
    return {"runs": jobs.list()}


@router.get("/api/train/run/{run_id}")
def train_run(run_id: str):
    try:
        return jobs.details(run_id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))


@router.post("/api/train/start")
def train_start(req: TrainReq):
    vehicle, task = _vehicle(req.vehicle), _task(req.task)
    if req.algo not in ("ppo", "sac"):
        raise HTTPException(422, "algo must be ppo or sac")
    if not 1 <= req.n_envs <= 32 or req.total_steps < 1000:
        raise HTTPException(422, "n_envs must be 1-32 and total_steps at least 1000")
    try:
        cls = TASKS.get(task.task)
        if "mujoco" not in cls.backends:
            raise HTTPException(422, f"task {task.name!r} needs {list(cls.backends)}; training runs many MuJoCo simulators in parallel")
        cls(task, vehicle)                                   # surfaces a vehicle/task mismatch now, not inside the subprocess
    except (KeyError, SpecError) as e:
        raise HTTPException(422, str(e).strip("'\""))
    cfg = {"vehicle": vehicle.to_dict(), "task": task.to_dict(), "algo": req.algo, "total_steps": req.total_steps,
           "n_envs": req.n_envs, "seed": req.seed, "curriculum_fraction": req.curriculum_fraction,
           "eval_every": req.eval_every, "eval_episodes": req.eval_episodes}
    return {"id": jobs.start(cfg)}


@router.post("/api/train/stop")
def train_stop(req: RunReq):
    try:
        jobs.stop(req.id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    return {"ok": True}


@router.post("/api/train/delete")
def train_delete(req: RunReq):
    try:
        jobs.delete(req.id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.get("/api/perception/runs")
def det_runs():
    return {"runs": det_jobs.list()}


@router.get("/api/perception/run/{run_id}")
def det_run(run_id: str):
    try:
        return det_jobs.details(run_id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))


@router.get("/api/perception/file/{run_id}/{name}")
def det_file(run_id: str, name: str):
    try:
        return FileResponse(det_jobs.file(run_id, name), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))


@router.post("/api/perception/start")
def det_start(req: DetectReq):
    vehicle, task = _vehicle(req.vehicle), _task(req.task)
    if not task.labels:
        raise HTTPException(422, f"task {task.name!r} lists no labels: there is nothing to detect")
    if vehicle.sensor("camera") is None:
        raise HTTPException(422, f"{vehicle.name} has no camera")
    if req.size not in ("nano", "small", "medium") or not 50 <= req.images <= 20000 or not 1 <= req.epochs <= 200:
        raise HTTPException(422, "size must be nano/small/medium, images 50-20000, epochs 1-200")
    try:
        TASKS.get(task.task)(task, vehicle)
    except (KeyError, SpecError) as e:
        raise HTTPException(422, str(e).strip("'\""))
    return {"id": det_jobs.start({"vehicle": vehicle.to_dict(), "task": task.to_dict(), "images": req.images, "epochs": req.epochs,
                                  "size": req.size, "seed": req.seed})}


@router.post("/api/perception/stop")
def det_stop(req: RunReq):
    try:
        det_jobs.stop(req.id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    return {"ok": True}


@router.post("/api/perception/delete")
def det_delete(req: RunReq):
    try:
        det_jobs.delete(req.id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.get("/api/lab/state")
def state():
    return runner.state()


@router.get("/api/lab/scene")
def scene():
    s = runner.scene()
    if s is None:
        raise HTTPException(404, "no scene: start a run first")
    return s


@router.websocket("/ws/lab")
async def ws_lab(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_text(json.dumps(runner.state(), default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
            await asyncio.sleep(0.05)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.get("/api/lab/video")
async def video():
    async def gen():
        runner.video_watch(+1)
        try:
            last = None
            while True:
                jpg = runner.jpeg()
                if jpg is not None and jpg is not last:
                    last = jpg
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode()
                           + b"\r\n\r\n" + jpg + b"\r\n")
                await asyncio.sleep(0.03)
        finally:
            runner.video_watch(-1)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame", headers={"Cache-Control": "no-store"})
