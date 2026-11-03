"""HTTP API of the Builder tab. Include it in a FastAPI app *before* any catch-all `/api/{kind}/...` routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..catalog import PRESET_DIR, EXTRA_DIRS, save_vehicle, user_dir as vehicles_dir
from ..parts import (Build, Part, build_names, build_to_vehicle, evaluate, get_build, load_inventory, parts_by_kind, save_build, save_inventory,
                     save_part)
from ..parts.build import PLATFORMS, builds_dir
from ..parts.library import broken_parts, delete_user_part
from ..parts.schema import kinds_info
from ..parts.templates import example_builds
from ..spec.vehicle import NAME_RE, SpecError
from ..vehicles.ground import DRIVES

router = APIRouter()


class BuildReq(BaseModel):
    build: dict
    task: str | None = None


class CalibrateReq(BaseModel):
    build: dict
    measurements: dict[str, float | None] = {}
    clear: bool = False


class PartReq(BaseModel):
    part: dict


class InventoryReq(BaseModel):
    owned: dict[str, int]


class NameReq(BaseModel):
    name: str


def _builtin_vehicle_names() -> set[str]:
    return {p.stem for d in (PRESET_DIR, *EXTRA_DIRS) if d.is_dir() for p in d.glob("*.yaml")}


KIND_OF = {"ground": "ground_wheeled", "air": "multirotor", "water": "boat"}


def _tasks_by_platform() -> dict[str, list[dict]]:
    """The task presets each platform's vehicle can do."""
    from ..registry import TASKS
    from ..tasks import get_task_spec, task_names
    out: dict[str, list[dict]] = {p: [] for p in KIND_OF}
    for n in task_names():
        ts = get_task_spec(n)
        cls = TASKS.get(ts.task)
        for platform, kind in KIND_OF.items():
            if not cls.kinds or kind in cls.kinds:
                out[platform].append({"name": n, "title": cls.title, "description": ts.description or cls.description, "backends": list(cls.backends)})
    return out


def _parse(d: dict) -> Build:
    build, issues = Build.parse(d)
    if build is None:
        raise HTTPException(422, "; ".join(str(i) for i in issues if i.level == "error"))
    return build


@router.get("/api/build/library")
def library():
    owned = load_inventory()
    parts = {k: [{**p.to_dict(), "origin": p.origin, "owned": owned.get(p.name, 0)} for p in v] for k, v in parts_by_kind().items()}
    return {"kinds": kinds_info(), "parts": parts, "owned": owned, "broken": broken_parts(), "drives": list(DRIVES), "platforms": list(PLATFORMS),
            "builds": build_names(), "examples": example_builds(), "tasks": _tasks_by_platform()}


@router.post("/api/build/part")
def add_part(req: PartReq):
    part, issues = Part.parse(req.part)
    if part is None:
        raise HTTPException(422, "; ".join(str(i) for i in issues if i.level == "error"))
    return {"path": str(save_part(part)), "issues": [i.to_dict() for i in issues]}


@router.post("/api/build/part/delete")
def remove_part(req: NameReq):
    if not NAME_RE.fullmatch(req.name) or not delete_user_part(req.name):
        raise HTTPException(404, f"no part of yours named {req.name!r} (the shipped library cannot be deleted)")
    return {"ok": True}


@router.post("/api/build/inventory")
def inventory(req: InventoryReq):
    save_inventory(req.owned)
    return {"owned": load_inventory()}


@router.post("/api/build/evaluate")
def evaluate_build(req: BuildReq):
    return evaluate(req.build, req.task)


@router.post("/api/build/calibrate")
def calibrate_build(req: CalibrateReq):
    """Replace the assumptions the build made with what you measured on the real machine (see simlab/parts/calibrate.py)."""
    from ..parts.calibrate import fit
    try:
        return fit(req.build, req.measurements, req.clear)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/api/build/preview")
def preview_build(req: BuildReq):
    build = _parse(req.build)
    spec, _ = build_to_vehicle(build)
    if spec is None:
        raise HTTPException(422, "the build does not produce a valid vehicle yet")
    try:
        from ..parts.preview import preview
        return preview(build, spec)
    except ImportError as e:
        raise HTTPException(503, f"3D preview needs MuJoCo: {e}")


@router.post("/api/build/save")
def save(req: BuildReq):
    build = _parse(req.build)
    if build.name in _builtin_vehicle_names() and build.name not in build_names():
        raise HTTPException(422, f"{build.name!r} is the name of a built-in vehicle: choose another name")
    result = evaluate(build, suggest=False)
    if result["vehicle"] is None or any(f["severity"] == "error" for f in result["findings"]):
        errors = [f["title"] for f in result["findings"] if f["severity"] == "error"]
        raise HTTPException(422, "fix these first: " + "; ".join(errors or ["the build does not make a valid vehicle"]))
    spec, _ = build_to_vehicle(build)
    try:
        vehicle_path = save_vehicle(spec)
    except SpecError as e:
        raise HTTPException(422, str(e))
    return {"build_path": str(save_build(build)), "vehicle_path": str(vehicle_path), "vehicle": result["vehicle"], "builds": build_names()}


@router.get("/api/build/list")
def list_builds():
    return {"builds": build_names()}


@router.get("/api/build/get")
def get(name: str):
    try:
        return get_build(name).to_dict()
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))


@router.post("/api/build/delete")
def delete(req: NameReq):
    if not NAME_RE.fullmatch(req.name) or req.name not in build_names():
        raise HTTPException(404, f"no saved build named {req.name!r}")
    (builds_dir() / f"{req.name}.yaml").unlink()
    vehicle = vehicles_dir() / f"{req.name}.yaml"
    if vehicle.is_file() and req.name not in _builtin_vehicle_names():
        vehicle.unlink()
    return {"builds": build_names()}
