"""Goal -> machine: describe the job in words, get a checked machine (see docs/GOAL.md)."""
from .catalogue import Goal, all_goals, get_goal
from .design import propose
from .match import match

__all__ = ["Goal", "all_goals", "get_goal", "propose", "match"]
