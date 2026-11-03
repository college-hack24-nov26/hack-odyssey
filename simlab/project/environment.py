"""The named test environment: what the machine is tested in, e.g. "tile floor, tape line, two bags, one box".

It is plain data so it can serve twice: as the simulated scene (a later milestone turns surface + objects into the scene) and as the physical
set-up instructions the user follows before a real test. Every run record stores the environment's name, so a result is never separated from where it
was measured.
"""
from __future__ import annotations

MAX_NAME = 60
MAX_TEXT = 600
MAX_OBJECTS = 30


class EnvError(ValueError):
    pass


def _s(v, limit: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:limit]


def normalise(raw) -> dict:
    """{name, surface, objects: [{name, count, note}], notes} from user input. Raises EnvError with a plain sentence when it cannot be used."""
    if not isinstance(raw, dict):
        raise EnvError("the environment must be an object with a name, a surface, objects and notes")
    name = _s(raw.get("name"), MAX_NAME)
    if not name:
        raise EnvError("give the environment a name (for example 'tile floor, tape line, two bags, one box')")
    objects = []
    items = raw.get("objects", [])
    if not isinstance(items, list):
        raise EnvError("objects must be a list")
    for o in items[:MAX_OBJECTS]:
        if isinstance(o, str):
            o = {"name": o}
        if not isinstance(o, dict) or not _s(o.get("name"), 60):
            raise EnvError("each object needs a name")
        try:
            count = int(o.get("count", 1))
        except (TypeError, ValueError):
            raise EnvError(f"the count of {_s(o.get('name'), 60)!r} must be a whole number")
        if not 1 <= count <= 99:
            raise EnvError(f"the count of {_s(o.get('name'), 60)!r} must be between 1 and 99")
        objects.append({"name": _s(o["name"], 60), "count": count, "note": _s(o.get("note"), 200)})
    if len(items) > MAX_OBJECTS:
        raise EnvError(f"at most {MAX_OBJECTS} objects")
    return {"name": name, "surface": _s(raw.get("surface"), 60), "objects": objects, "notes": _s(raw.get("notes"), MAX_TEXT)}


def setup_steps(env: dict) -> list[str]:
    """The physical set-up, in plain sentences, from the same data the simulated scene uses."""
    if not env:
        return []
    steps = []
    if env.get("surface"):
        steps.append(f"Use this surface: {env['surface']}.")
    for o in env.get("objects", []):
        steps.append(f"Place {o['count']} x {o['name']}" + (f" ({o['note']})" if o.get("note") else "") + ".")
    if env.get("notes"):
        steps.append(env["notes"])
    steps.append(f"Check the area matches \"{env['name']}\" before the test, and keep Stop within reach.")
    return steps
