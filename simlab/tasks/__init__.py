"""Built-in tasks (importing this package registers them) and the catalog of task presets."""
from pathlib import Path
import os

from ..spec.task import TaskSpec
from . import aerial, goto, pipe, rescue, sandbox, search, sewer  # noqa: F401
from .base import Episode, Item, MeshItem, Requirement, StepResult, Task, World

PRESET_DIR = Path(__file__).parent / "presets"
ROOT = Path(__file__).resolve().parents[2]


def _files() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in (PRESET_DIR, Path(os.environ.get("SIMLAB_TASKS", ROOT / "config" / "tasks"))):
        if d.is_dir():
            for p in sorted(d.glob("*.yaml")):
                found[p.stem] = p
    return found


def task_names() -> list[str]:
    return sorted(_files())


def get_task_spec(name: str) -> TaskSpec:
    files = _files()
    if name not in files:
        raise KeyError(f"unknown task preset {name!r}; choose from {sorted(files)}")
    return TaskSpec.load(files[name])


__all__ = ["Episode", "Item", "MeshItem", "Requirement", "StepResult", "Task", "World", "get_task_spec", "task_names"]
