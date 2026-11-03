"""Turn a Build into a VehicleSpec plus predicted performance, per platform."""
from __future__ import annotations

from ..build import Build
from .common import Derived, PFact


def build_to_vehicle(build: Build):
    """(VehicleSpec | None, Derived). The spec is None only if the derived numbers cannot form a valid vehicle."""
    if build.platform == "ground":
        from .ground import build_to_vehicle as derive
    elif build.platform == "air":
        from .air import build_to_vehicle as derive
    elif build.platform == "water":
        from .water import build_to_vehicle as derive
    else:
        raise ValueError(f"no derivation for platform {build.platform!r}")
    return derive(build)


__all__ = ["Derived", "PFact", "build_to_vehicle"]
