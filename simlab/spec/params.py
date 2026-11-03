"""Typed, documented parameters: one schema drives validation, defaults, docs and the Designer form."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Issue:
    level: str   # "error" blocks use of a spec; "warn" is shown but allowed
    field: str
    msg: str

    def to_dict(self) -> dict:
        return {"level": self.level, "field": self.field, "msg": self.msg}

    def __str__(self) -> str:
        return f"{self.field}: {self.msg}"


def join(where: str, name: str) -> str:
    return f"{where}.{name}" if where else name


@dataclass(frozen=True)
class Param:
    name: str
    type: str                  # float | int | bool | str | strlist | enum
    default: Any
    min: float | None = None
    max: float | None = None
    unit: str = ""
    doc: str = ""
    choices: tuple = ()

    def coerce(self, value: Any) -> Any:
        t = self.type
        try:
            if t == "float":
                if isinstance(value, bool):
                    raise ValueError
                x = float(value)
                if not math.isfinite(x):
                    raise ValueError("not finite")
            elif t == "int":
                if isinstance(value, bool):
                    raise ValueError
                x = int(value)
                if isinstance(value, float) and value != x:
                    raise ValueError("not a whole number")
            elif t == "bool":
                if isinstance(value, bool):
                    return value
                s = str(value).strip().lower()
                if s in ("true", "1", "yes", "on"):
                    return True
                if s in ("false", "0", "no", "off"):
                    return False
                raise ValueError
            elif t == "str":
                return str(value)
            elif t == "strlist":
                items = [s.strip() for s in value.split(",")] if isinstance(value, str) else [str(s).strip() for s in (value or [])]
                return [s for s in items if s]
            elif t == "enum":
                x = str(value)
                if x not in self.choices:
                    raise ValueError(f"must be one of {list(self.choices)}")
                return x
            else:
                raise ValueError(f"unknown parameter type {t!r}")
        except (TypeError, ValueError) as e:
            detail = f" ({e})" if str(e) else ""
            raise ValueError(f"expected {t}, got {value!r}{detail}") from None
        u = f" {self.unit}" if self.unit else ""
        if self.min is not None and x < self.min:
            raise ValueError(f"{x:g}{u} is below the minimum {self.min:g}{u}")
        if self.max is not None and x > self.max:
            raise ValueError(f"{x:g}{u} is above the maximum {self.max:g}{u}")
        return x

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "default": self.default, "min": self.min, "max": self.max,
                "unit": self.unit, "doc": self.doc, "choices": list(self.choices)}


def apply_schema(schema: Sequence[Param], values: Mapping | None, where: str = "") -> tuple[dict, list[Issue]]:
    """Fill defaults, coerce types, enforce ranges; unknown keys are errors (typos must not pass silently)."""
    if values is not None and not isinstance(values, Mapping):
        defaults = {p.name: p.default for p in schema}
        return defaults, [Issue("error", where or "values", f"must be a mapping, got {type(values).__name__}")]
    values = dict(values or {})
    known = {p.name: p for p in schema}
    issues = [Issue("error", join(where, k), f"unknown parameter; known: {sorted(known)}")
              for k in values if k not in known]
    out = {}
    for p in schema:
        try:
            out[p.name] = p.coerce(values.get(p.name, p.default))
        except ValueError as e:
            issues.append(Issue("error", join(where, p.name), str(e)))
            out[p.name] = p.default
    return out, issues
