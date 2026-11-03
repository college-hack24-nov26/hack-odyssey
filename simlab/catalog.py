"""Finds vehicle specs: built-in presets plus your own files in config/vehicles (a same-named user file wins)."""
from __future__ import annotations

import os
from pathlib import Path

from .registry import load_plugins
from .spec.vehicle import SpecError, VehicleSpec

ROOT = Path(__file__).resolve().parents[1]
PRESET_DIR = Path(__file__).parent / "vehicles" / "presets"


EXTRA_DIRS: list[Path] = []          # preset folders contributed by plug-ins (register_vehicle_dir)


def register_vehicle_dir(path: str | Path) -> None:
    """Let a plug-in ship vehicle presets alongside its code."""
    p = Path(path)
    if p not in EXTRA_DIRS:
        EXTRA_DIRS.append(p)


def user_dir() -> Path:
    return Path(os.environ.get("SIMLAB_VEHICLES", ROOT / "config" / "vehicles"))


def _files(plugins: bool = True) -> dict[str, Path]:
    """name -> file. `plugins=False` looks only at the built-in presets and your own files, importing nothing extra."""
    if plugins:
        load_plugins()
    found: dict[str, Path] = {}
    for d in (PRESET_DIR, *EXTRA_DIRS, user_dir()):
        if d.is_dir():
            for p in sorted(d.glob("*.yaml")):
                found[p.stem] = p
    return found


def vehicle_names(kind: str | None = None) -> list[str]:
    """Names of the vehicles that load; a file that is invalid (or needs a plug-in that is missing) is left out."""
    names = []
    for n in sorted(_files()):
        try:
            if get_vehicle(n).kind == kind or kind is None:           # loading it is the check: unusable files stay out of the list
                names.append(n)
        except (SpecError, KeyError, ValueError):
            continue
    return names


def broken_vehicles() -> dict[str, str]:
    """name -> why it could not be loaded, for the vehicle files that were skipped."""
    out = {}
    for n in sorted(_files()):
        try:
            get_vehicle(n)
        except (SpecError, KeyError, ValueError) as e:
            out[n] = str(e).strip("'\"")
    return out


def get_vehicle(name: str, **overrides) -> VehicleSpec:
    files = _files(plugins=False)
    if name not in files:
        files = _files()                                  # perhaps a plug-in's preset: load plug-ins and look again
    if name not in files:
        raise KeyError(f"unknown vehicle {name!r}; choose from {sorted(files)}")
    spec = VehicleSpec.load(files[name])
    return spec.override(**overrides) if overrides else spec


def save_vehicle(spec: VehicleSpec) -> Path:
    """Write a spec to config/vehicles/<name>.yaml (after checking it)."""
    spec.check()
    path = user_dir() / f"{spec.name}.yaml"
    spec.dump(path)
    return path
