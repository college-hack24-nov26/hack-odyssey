"""Command spaces: the named, bounded vectors a policy sends to a vehicle (replaces the fixed (v, omega) pair).

Each vehicle kind exposes several spaces (ground: unicycle / wheels, multirotor: velocity / position / ...); the
kind owns the mixer and inner control loops that turn a command in a space into actuator effort.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class Channel:
    name: str
    lo: float
    hi: float
    unit: str = ""


@dataclass(frozen=True)
class CommandSpace:
    name: str
    channels: tuple[Channel, ...]
    doc: str = ""

    @property
    def dim(self) -> int:
        return len(self.channels)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.channels)

    @property
    def lo(self) -> np.ndarray:
        return np.array([c.lo for c in self.channels], float)

    @property
    def hi(self) -> np.ndarray:
        return np.array([c.hi for c in self.channels], float)

    def clip(self, v: Sequence[float]) -> np.ndarray:
        v = np.nan_to_num(np.asarray(v, float).reshape(-1), nan=0.0, posinf=1e300, neginf=-1e300)
        return np.clip(v, self.lo, self.hi)

    def zeros(self) -> np.ndarray:
        return self.clip(np.zeros(self.dim))

    def vector(self, cmd: Mapping[str, float] | Sequence[float]) -> np.ndarray:
        """A dict (missing channels = 0, unknown names are errors) or a sequence -> clipped vector."""
        if isinstance(cmd, Mapping):
            unknown = set(cmd) - set(self.names)
            if unknown:
                raise KeyError(f"space {self.name!r} has no channel(s) {sorted(unknown)}; has {list(self.names)}")
            v = np.zeros(self.dim)
            for i, c in enumerate(self.channels):
                if c.name in cmd:
                    v[i] = float(cmd[c.name])
            return self.clip(v)
        v = np.asarray(cmd, float).reshape(-1)
        if v.size != self.dim:
            raise ValueError(f"space {self.name!r} needs {self.dim} values {list(self.names)}, got {v.size}")
        return self.clip(v)

    def as_dict(self, v: Sequence[float]) -> dict[str, float]:
        return {c.name: float(x) for c, x in zip(self.channels, self.clip(v))}

    def normalise(self, v: Sequence[float]) -> np.ndarray:
        """Physical -> [-1, 1] per channel (what an RL policy sees and emits)."""
        lo, hi = self.lo, self.hi
        return 2.0 * (self.clip(v) - lo) / np.where(hi > lo, hi - lo, 1.0) - 1.0

    def denormalise(self, u: Sequence[float]) -> np.ndarray:
        lo, hi = self.lo, self.hi
        u = np.clip(np.nan_to_num(np.asarray(u, float).reshape(-1)), -1.0, 1.0)
        return lo + (u + 1.0) * 0.5 * (hi - lo)

    def to_dict(self) -> dict:
        return {"name": self.name, "doc": self.doc,
                "channels": [{"name": c.name, "lo": c.lo, "hi": c.hi, "unit": c.unit} for c in self.channels]}
