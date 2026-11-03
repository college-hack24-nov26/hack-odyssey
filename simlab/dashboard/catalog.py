"""What the Lab tab needs to know: kinds (with their parameter schemas), vehicles, tasks, validation of an edited spec."""
from __future__ import annotations

from ..catalog import broken_vehicles, get_vehicle, vehicle_names
from ..registry import BACKENDS, KINDS, PLUGIN_ERRORS, TASKS
from ..tasks.base import Task
from ..spec.params import Issue
from ..spec.sensors import SENSOR_SCHEMAS
from ..spec.task import TaskSpec
from ..spec.vehicle import VehicleSpec, body_schema
from ..tasks import get_task_spec, task_names


def describe(spec: VehicleSpec, issues: list[Issue] | None = None) -> dict:
    """Everything the UI shows about a (valid) spec: its normalised dict, command spaces, derived facts, issues."""
    return {"ok": not any(i.level == "error" for i in (issues or spec.validate())), "spec": spec.to_dict(),
            "spaces": {n: s.to_dict() for n, s in spec.spaces().items()},
            "derived": [f.to_dict() for f in spec.derived()],
            "issues": [i.to_dict() for i in (issues if issues is not None else spec.validate())]}


def validate_vehicle(d: dict) -> dict:
    """Live validation of whatever the editor currently holds; never raises."""
    spec, issues = VehicleSpec.parse(d)
    if spec is None:
        return {"ok": False, "spec": None, "spaces": {}, "derived": [], "issues": [i.to_dict() for i in issues]}
    return describe(spec, issues + spec.kind_plugin().validate(spec, None))


def kinds_info() -> dict:
    out = {}
    for name in KINDS.names():
        k = KINDS.get(name)
        out[name] = {"title": k.title, "description": k.description, "default_space": k.default_space,
                     "schema": [p.to_dict() for p in k.schema], "body_schema": [p.to_dict() for p in body_schema(k)]}
    return out


def build_catalog() -> dict:
    vehicles = {}
    for n in vehicle_names():
        spec = get_vehicle(n)
        vehicles[n] = {"kind": spec.kind, "description": spec.description, **describe(spec)}
    tasks = {}
    for n in task_names():
        ts: TaskSpec = get_task_spec(n)
        cls = TASKS.get(ts.task)
        tasks[n] = {"task": ts.task, "title": cls.title, "description": ts.description or cls.description,
                    "kinds": list(cls.kinds), "backends": list(cls.backends), "spec": ts.to_dict(), "baseline": cls.baseline is not Task.baseline}
    return {"kinds": kinds_info(), "vehicles": vehicles, "tasks": tasks, "backends": BACKENDS.names(),
            "sensors": {t: [p.to_dict() for p in schema] for t, schema in SENSOR_SCHEMAS.items()},
            "policies": ["baseline", "manual", "idle"], "problems": {**broken_vehicles(), **{f"plugin {k}": v for k, v in PLUGIN_ERRORS.items()}}}
