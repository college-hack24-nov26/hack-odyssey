"""A vehicle kind turns a plain-data VehicleSpec into physics: its schema, command spaces, checks and derived facts.

Adding a new machine type means writing one VehicleKind subclass and registering it; the spec file format, the
Designer form, validation and the training environment are all driven by `schema` and `spaces()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..spec.caps import Caps
from ..spec.commands import CommandSpace
from ..spec.params import Issue, Param

if TYPE_CHECKING:  # pragma: no cover
    from ..spec.vehicle import VehicleSpec


@dataclass(frozen=True)
class Fact:
    """A computed property shown next to the form (thrust-to-weight, top speed, ...)."""
    key: str
    label: str
    value: float
    unit: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "value": self.value, "unit": self.unit}


def box_inertia(mass: float, length: float, width: float, height: float) -> tuple[float, float, float]:
    """Principal moments (Ixx, Iyy, Izz) of a uniform box, x forward."""
    return (mass / 12.0 * (width ** 2 + height ** 2),
            mass / 12.0 * (length ** 2 + height ** 2),
            mass / 12.0 * (length ** 2 + width ** 2))


class VehicleKind:
    name = ""
    title = ""
    description = ""
    schema: tuple[Param, ...] = ()
    body_defaults: dict = {}
    default_space = ""

    def spaces(self, spec: "VehicleSpec") -> dict[str, CommandSpace]:
        raise NotImplementedError

    def validate(self, spec: "VehicleSpec", caps: Caps | None = None) -> list[Issue]:
        """Cross-field and backend checks beyond what the schema's ranges can say."""
        if caps is not None and caps.kinds and self.name not in caps.kinds:
            return [Issue("error", "kind", f"backend {caps.name!r} cannot simulate {self.name!r} vehicles "
                                           f"(supports {list(caps.kinds)})")]
        return []

    def derived(self, spec: "VehicleSpec") -> list[Fact]:
        return []
