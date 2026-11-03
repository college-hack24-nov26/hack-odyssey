"""Custom machines: any MuJoCo (MJCF) or URDF model, driven through its own actuators."""
from __future__ import annotations

from ..registry import KINDS
from ..spec.caps import Caps
from ..spec.commands import Channel, CommandSpace
from ..spec.params import Issue, Param
from .base import Fact, VehicleKind
from .importer import inspect_model


def model_info(spec) -> dict | None:
    """inspect_model for a spec, or None when the file cannot be read (validate() explains why)."""
    try:
        return inspect_model(spec.params["model"], spec.params["base_body"])
    except (FileNotFoundError, ValueError, KeyError):
        return None


def chosen_actuators(spec, info: dict) -> list[dict]:
    want = spec.params["actuators"]
    return [a for a in info["actuators"] if a["name"] in want] if want else list(info["actuators"])


class CustomModel(VehicleKind):
    name = "custom"
    title = "Custom model (MJCF / URDF)"
    description = "Any MuJoCo or URDF robot: its actuators become the command channels (raw space)."
    schema = (
        Param("model", "str", "", doc="Path to an MJCF (.xml) or URDF (.urdf) file, absolute or relative to the project folder"),
        Param("base_body", "str", "", doc="The robot's root body; empty = the first body in the file"),
        Param("actuators", "strlist", [], doc="Comma-separated actuator names to expose; empty = all"),
    )
    body_defaults = {"mass": 1.0, "length": 0.5, "width": 0.5, "height": 0.3}
    default_space = "raw"

    def spaces(self, spec):
        info = model_info(spec)
        chans = tuple(Channel(a["name"], a["lo"], a["hi"]) for a in chosen_actuators(spec, info)) if info else ()
        return {"raw": CommandSpace("raw", chans or (Channel("none", 0.0, 0.0),), "One channel per actuator, in the model's own units.")}

    def validate(self, spec, caps: Caps | None = None):
        issues = super().validate(spec, caps)
        p = spec.params
        if not p["model"]:
            return issues + [Issue("error", "params.model", "choose a model file")]
        try:
            info = inspect_model(p["model"], p["base_body"])
        except (FileNotFoundError, ValueError) as e:
            return issues + [Issue("error", "params.model", str(e))]
        except KeyError as e:
            return issues + [Issue("error", "params.base_body", str(e).strip("'\""))]
        known = {a["name"] for a in info["actuators"]}
        for name in p["actuators"]:
            if name not in known:
                issues.append(Issue("error", "params.actuators", f"no actuator named {name!r}; the model has {sorted(known) or 'none'}"))
        if not info["actuators"]:
            issues.append(Issue("warn", "params.model", "the model has no actuators: it can only be placed and watched"))
        if not info["free_base"]:
            issues.append(Issue("warn", "params.base_body", "the base has no free joint, so it cannot be moved to a start pose"))
        if info["has_own_plane"]:
            issues.append(Issue("warn", "params.model", "the model has its own ground plane; it will sit beside the arena's floor"))
        return issues

    def derived(self, spec):
        info = model_info(spec)
        if not info:
            return []
        return [Fact("mass", "Model mass", info["mass"], "kg"), Fact("actuators", "Actuators", len(chosen_actuators(spec, info))),
                Fact("dofs", "Degrees of freedom", info["dofs"]), Fact("height", "Height", info["extent"][2], "m")]


KINDS.add(CustomModel())
