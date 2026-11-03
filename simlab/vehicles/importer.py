"""Read any MuJoCo (MJCF) or URDF robot file and describe what simlab needs to drive it: actuators, base body, size."""
from __future__ import annotations

import functools
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CONVERTED = ROOT / "config" / "models"


def resolve(path: str | Path) -> Path:
    """Absolute path of a model file: as given, else relative to the project folder."""
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p.resolve()
    return (ROOT / p).resolve()


def to_mjcf(path: str | Path) -> Path:
    """The MJCF file for a model: itself, or for a URDF the MuJoCo-converted copy written to config/models/."""
    import mujoco
    p = resolve(path)
    if p.suffix.lower() != ".urdf":
        return p
    CONVERTED.mkdir(parents=True, exist_ok=True)
    out = CONVERTED / f"{p.stem}.xml"
    if not out.exists() or out.stat().st_mtime < p.stat().st_mtime:
        mujoco.mj_saveLastXML(str(out), mujoco.MjModel.from_xml_path(str(p)))
    return out


@functools.lru_cache(maxsize=32)
def _inspect(path: str, mtime: float, base_body: str) -> dict:
    import mujoco
    m = mujoco.MjModel.from_xml_path(path)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    names = [m.body(i).name for i in range(m.nbody)]
    if base_body:
        if base_body not in names:
            raise KeyError(f"no body named {base_body!r}; the model has {names[1:] or 'none'}")
        base = m.body(base_body).id
    elif m.nbody > 1:
        base = 1
    else:
        raise KeyError("the model has no bodies besides the world")
    in_tree = [b for b in range(m.nbody) if _descends(m, b, base)]
    free = any(int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE) and int(m.jnt_bodyid[j]) == base for j in range(m.njnt))
    acts = []
    for a in range(m.nu):
        lim = bool(m.actuator_ctrllimited[a])
        lo, hi = (float(m.actuator_ctrlrange[a][0]), float(m.actuator_ctrlrange[a][1])) if lim else (-1.0, 1.0)
        acts.append({"name": m.actuator(a).name or f"act{a}", "lo": lo, "hi": hi, "limited": lim})
    lows, highs = [], []
    for g in range(m.ngeom):
        if int(m.geom_bodyid[g]) in in_tree and int(m.geom_type[g]) != int(mujoco.mjtGeom.mjGEOM_PLANE):
            r = float(m.geom_rbound[g])
            lows.append(d.geom_xpos[g] - r)
            highs.append(d.geom_xpos[g] + r)
    lo, hi = (np.min(lows, 0), np.max(highs, 0)) if lows else (np.zeros(3), np.zeros(3))
    ext = np.maximum(hi - lo, 0.01)
    base_z = float(d.xpos[base][2])
    return {"path": path, "base_body": names[base], "bodies": names[1:], "actuators": acts, "free_base": free,
            "cameras": [m.camera(i).name for i in range(m.ncam) if m.camera(i).name],
            "mass": float(m.body_subtreemass[base]), "extent": [float(ext[0]), float(ext[1]), float(ext[2])],
            "origin_height": max(base_z - float(lo[2]), 0.0), "has_own_plane": any(int(t) == int(mujoco.mjtGeom.mjGEOM_PLANE) for t in m.geom_type),
            "dofs": int(m.nv), "qpos0_z": float(m.qpos0[2]) if free else base_z}


def _descends(m, body: int, ancestor: int) -> bool:
    while body > 0:
        if body == ancestor:
            return True
        body = int(m.body_parentid[body])
    return False


def inspect_model(path: str | Path, base_body: str = "") -> dict:
    """Facts about a model file (raises FileNotFoundError / ValueError / KeyError with a message fit for the UI)."""
    p = to_mjcf(path)
    if not p.is_file():
        raise FileNotFoundError(f"model file not found: {p}")
    try:
        return _inspect(str(p), p.stat().st_mtime, base_body)
    except (KeyError, FileNotFoundError):
        raise
    except Exception as e:  # noqa: BLE001 - MuJoCo raises ValueError with the compiler's message
        raise ValueError(f"MuJoCo could not load {p.name}: {e}") from None


def draft_spec(path: str | Path, name: str | None = None) -> dict:
    """A ready-to-edit vehicle spec (as a dict) for a model file: every actuator exposed, size and mass read from the file."""
    info = inspect_model(path)
    ex = info["extent"]
    cams = [{"type": "camera", "name": "camera", "mjcf_camera": info["cameras"][0], "width_px": 320, "height_px": 240, "fps": 20.0}] if info["cameras"] else []
    return {"name": name or Path(path).stem.replace(" ", "_"), "kind": "custom", "description": f"Imported from {Path(path).name}",
            "body": {"mass": round(max(info["mass"], 0.001), 3), "length": round(ex[0], 3), "width": round(ex[1], 3), "height": round(ex[2], 3)},
            "params": {"model": str(path), "base_body": info["base_body"], "actuators": [a["name"] for a in info["actuators"]]},
            "sensors": cams, "default_space": "raw"}
