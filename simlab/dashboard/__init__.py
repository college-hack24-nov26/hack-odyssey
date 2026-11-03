"""Dashboard pieces for simlab: `router` (API), `runner` (the live simulation), STATIC (the Lab tab's front-end files)."""
from pathlib import Path

from .build_router import router as build_router
from .goal_router import router as goal_router
from .router import det_jobs, jobs, router, runner

STATIC = Path(__file__).parent / "static"

__all__ = ["STATIC", "build_router", "goal_router", "det_jobs", "jobs", "router", "runner"]
