"""Pieces shared by every platform's derivation: facts, the battery pack, and the electronics power budget."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...spec.params import Issue
from ..schema import Part

G = 9.81


@dataclass
class PFact:
    """One predicted number. `lo`/`hi` give the range over the battery state (spent .. fresh) when that matters."""
    key: str
    label: str
    value: float
    unit: str = ""
    lo: float | None = None
    hi: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "value": self.value, "unit": self.unit, "lo": self.lo, "hi": self.hi, "note": self.note}


@dataclass
class Pack:
    """A battery of `series` x `parallel` units of one battery part."""
    v_nom: float
    v_full: float
    v_cut: float
    r_pack: float          # ohm
    q_mah: float
    max_a: float
    mass_kg: float
    chemistry: str
    series: int
    parallel: int


def pack_from(part: Part, series: int, parallel: int) -> Pack:
    p = part.params
    return Pack(series * p["cell_v_nominal"], series * p["cell_v_full"], series * p["cell_v_cutoff"],
                series * p["r_internal_ohm"] / parallel, parallel * p["capacity_mah"], parallel * p["max_continuous_a"],
                series * parallel * part.mass_kg, p["chemistry"], series, parallel)


@dataclass
class Rail:
    """The voltage the electronics (controller, sensors, lamp) see, over the battery's charge range."""
    v_min: float
    v_max: float
    v_nom: float
    via: str               # "pack" or the regulator's name


def rail_of(pack: Pack, regulator: Part | None) -> Rail:
    if regulator is None:
        return Rail(pack.v_cut, pack.v_full, pack.v_nom, "pack")
    r = regulator.params
    out, top = r["v_out"], r["topology"]
    if top == "boost":                                       # holds the set output while the input is above its minimum, and passes the input
        low = out if pack.v_cut >= r["v_in_min"] else 0.0     # through when it is above the set output (a fresh pack can exceed it)
        return Rail(low, max(out, pack.v_full), max(out, pack.v_nom), regulator.name)
    low = min(out, pack.v_cut - r["dropout_v"])              # buck / linear: follows the input down once it gets within the dropout
    return Rail(low, out, out, regulator.name)


def battery_current(i_rail: float, rail_v: float, v_batt: float, regulator: Part | None) -> float:
    """Current the battery supplies for electronics drawing `i_rail` at `rail_v`."""
    if regulator is None:
        return i_rail
    r = regulator.params
    if r["topology"] == "ldo":
        return i_rail + r["quiescent_a"]
    return rail_v * i_rail / (r["efficiency"] * max(v_batt, 1e-6)) + r["quiescent_a"]


@dataclass
class Derived:
    platform: str
    body: dict
    params: dict
    sensors: list[dict]
    description: str
    facts: list[PFact] = field(default_factory=list)
    budget: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    numbers: dict = field(default_factory=dict)       # raw internals the rules read
    issues: list[Issue] = field(default_factory=list)

    def fact(self, key: str) -> PFact | None:
        return next((f for f in self.facts if f.key == key), None)
