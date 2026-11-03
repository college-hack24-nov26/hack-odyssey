"""Finds parts: the library that ships with the project plus your own files in config/parts (a same-named user part wins).

Layout: <dir>/<kind>/<name>.yaml. `config/inventory.yaml` lists how many of each part you own.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .schema import PART_KINDS, Part

ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = Path(__file__).parent / "library"

_cache: dict = {"sig": None, "parts": {}, "broken": {}}


def user_dir() -> Path:
    return Path(os.environ.get("SIMLAB_PARTS", ROOT / "config" / "parts"))


def inventory_path() -> Path:
    return Path(os.environ.get("SIMLAB_INVENTORY", ROOT / "config" / "inventory.yaml"))


def _paths() -> list[tuple[Path, str]]:
    out = []
    for d, origin in ((LIB_DIR, "library"), (user_dir(), "user")):
        if d.is_dir():
            out += [(p, origin) for p in sorted(d.glob("*/*.yaml"))]
    return out


def _load() -> None:
    paths = _paths()
    sig = tuple((str(p), p.stat().st_mtime_ns) for p, _ in paths)
    if sig == _cache["sig"]:
        return
    parts, broken = {}, {}
    for p, origin in paths:
        try:
            part, issues = Part.parse(yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {})
        except (yaml.YAMLError, OSError) as e:
            broken[p.stem] = str(e).splitlines()[0]
            continue
        if part is None:
            broken[p.stem] = "; ".join(str(i) for i in issues if i.level == "error")
            continue
        part = Part(**{**part.__dict__, "origin": origin})
        parts[part.name] = part
    _cache.update(sig=sig, parts=parts, broken=broken)


def all_parts() -> dict[str, Part]:
    _load()
    return dict(_cache["parts"])


def broken_parts() -> dict[str, str]:
    """file stem -> why it could not be loaded."""
    _load()
    return dict(_cache["broken"])


def part_names(kind: str | None = None) -> list[str]:
    return sorted(n for n, p in all_parts().items() if kind is None or p.kind == kind)


def parts_by_kind() -> dict[str, list[Part]]:
    out: dict[str, list[Part]] = {k: [] for k in PART_KINDS}
    for p in all_parts().values():
        out.setdefault(p.kind, []).append(p)
    return {k: sorted(v, key=lambda p: p.name) for k, v in out.items()}


def get_part(name: str, kind: str | None = None) -> Part:
    parts = all_parts()
    part = parts.get(name)
    if part is None or (kind is not None and part.kind != kind):
        pool = sorted(n for n, p in parts.items() if kind is None or p.kind == kind)
        what = f"{kind} part" if kind else "part"
        raise KeyError(f"unknown {what} {name!r}; choose from {pool}")
    return part


def save_part(part: Part) -> Path:
    """Write a part to config/parts/<kind>/<name>.yaml (it must parse cleanly)."""
    checked, issues = Part.parse(part.to_dict())
    if checked is None:
        raise ValueError("; ".join(str(i) for i in issues if i.level == "error"))
    path = user_dir() / part.kind / f"{part.name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(part.to_dict(), sort_keys=False), encoding="utf-8")
    return path


def delete_user_part(name: str) -> bool:
    for p, origin in _paths():
        if origin == "user" and p.stem == name:
            p.unlink()
            return True
    return False


# ---------------------------------------------------------------- what you own
def load_inventory() -> dict[str, int]:
    path = inventory_path()
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, (int, float)) and v > 0}


def save_inventory(owned: dict[str, int]) -> Path:
    path = inventory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {str(k): int(v) for k, v in owned.items() if int(v) > 0}
    path.write_text(yaml.safe_dump(clean, sort_keys=True), encoding="utf-8")
    return path
