"""What a backend can and cannot do, so a spec can be checked against it before a run is launched."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Caps:
    name: str
    kinds: tuple[str, ...] = ()               # vehicle kinds supported; empty = all
    max_ground_speed: float = math.inf        # m/s
    parallel: bool = False                    # several instances at once (vectorised training)
    realtime_only: bool = False
    ground_truth: tuple[str, ...] = ()        # e.g. "pose", "segmentation"


# Webots steps at 16 ms and takes commands once per camera frame: faster rovers tunnel through the pipe walls.
WEBOTS_CAPS = Caps("webots", kinds=("ground_wheeled",), max_ground_speed=1.5, realtime_only=False,
                   ground_truth=("pose", "segmentation"))
