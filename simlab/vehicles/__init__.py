"""Built-in vehicle kinds; importing this package registers them."""
from . import custom, ground, multirotor  # noqa: F401
from .base import Fact, VehicleKind, box_inertia

__all__ = ["Fact", "VehicleKind", "box_inertia", "custom", "ground", "multirotor"]
