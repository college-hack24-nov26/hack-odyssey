"""HTTP API of projects: list, create, get, update, the guarded steps (confirm a sensor, confirm the parts, mark a stage), run records and the
"using what I have" comparison. Include it in a FastAPI app *before* any catch-all `/api/{kind}/...` routes, or those would shadow it.

Errors are plain sentences: 404 no such project, 422 a change that was refused (nothing was saved), 409 a project file that is damaged.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from ..project import ProjectError, ProjectService
from ..project.store import ProjectStore

router = APIRouter()

_service: ProjectService | None = None
_lock = threading.Lock()


def service() -> ProjectService:
    """The one service of this server (created on first use, so importing the module costs nothing)."""
    global _service
    with _lock:
        if _service is None:
            _service = ProjectService()
        return _service


def set_service(s: ProjectService | None) -> None:
    """Tests install a service on a temp folder; None goes back to the default on next use."""
    global _service
    with _lock:
        _service = s


class CreateReq(BaseModel):
    name: str = ""
    text: str = ""
    moving: bool = True
    owned: dict[str, int] | None = None


class ConfirmReq(BaseModel):
    confirm: bool = False
    note: str = ""


class NoteReq(BaseModel):
    note: str = ""


class RunReq(BaseModel):
    kind: str
    summary: str = ""
    ok: bool | None = None
    link: str | None = None
    data: dict | None = None


def _call(fn, *args, **kw):
    """Run a service call and turn its errors into HTTP errors."""
    try:
        return fn(*args, **kw)
    except ProjectError as e:
        raise HTTPException(422, str(e))
    except KeyError:
        raise HTTPException(404, "no such project")
    except ValueError as e:                                   # a damaged project.json
        raise HTTPException(409, str(e))


@router.get("/api/project")
def list_projects():
    return {"projects": service().list()}


@router.post("/api/project")
def create_project(req: CreateReq):
    return _call(service().create, req.name, req.text, req.moving, req.owned)


@router.get("/api/project/{pid}")
def get_project(pid: str):
    return _call(service().view, pid)


@router.put("/api/project/{pid}")
def update_project(pid: str, fields: dict = Body(...)):
    return _call(service().update, pid, fields)


@router.delete("/api/project/{pid}")
def delete_project(pid: str):
    _call(service().delete, pid)
    return {"ok": True}


@router.get("/api/project/{pid}/compare")
def compare_equipment(pid: str):
    return _call(service().compare, pid)


@router.post("/api/project/{pid}/sensors/{name}/confirm")
def confirm_sensor(pid: str, name: str, req: ConfirmReq):
    return _call(service().confirm_sensor, pid, name, req.confirm, req.note)


@router.post("/api/project/{pid}/parts/confirm")
def confirm_parts(pid: str):
    return _call(service().confirm_parts, pid)


@router.post("/api/project/{pid}/stage/{stage}")
def mark_stage(pid: str, stage: str, req: NoteReq | None = None):
    return _call(service().mark_stage, pid, stage, req.note if req else "")


@router.post("/api/project/{pid}/runs")
def record_run(pid: str, req: RunReq):
    return _call(service().record_run, pid, req.kind, req.summary, req.ok, req.link, req.data)
