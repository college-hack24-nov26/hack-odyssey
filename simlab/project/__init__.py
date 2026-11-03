"""A project: the record of one job from requirements to deploy (blueprint, equipment and inventory, sensor ladder, named environment, runs, stages).

Stored under runs/projects/<id>/ (store.py); every change goes through ProjectService (service.py). Offline, no hardware, no simulator.
"""
from .service import LINKS, RUN_KINDS, ProjectError, ProjectService
from .stages import STAGES
from .store import ProjectStore

__all__ = ["LINKS", "ProjectError", "ProjectService", "ProjectStore", "RUN_KINDS", "STAGES"]
