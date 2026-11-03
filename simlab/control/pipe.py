"""Classical controller for pipe_inspect: the sewer pipeline's own path follower, driven by the simulator's pose."""
from __future__ import annotations

import numpy as np


class PipeController:
    """Follow the route through the node centres (sewerbot.brain.navigation.PathFollower), pivoting for sharp turns."""

    def __init__(self, task):
        from sewerbot.core.profiles import profile_from_spec
        self.task = task
        self.profile = profile_from_spec(task.vehicle)
        self.follower = None
        self.status, self.note = "idle", ""             # the follower's own verdict: idle | running | arrived | blocked | done

    def reset(self):
        self.follower = None
        self.status, self.note = "idle", ""

    def __call__(self, obs, episode, space=None) -> np.ndarray:
        from sewerbot.brain.navigation import PathFollower
        if self.follower is None:
            self.follower = PathFollower(self.task.map, self.profile, list(self.task.route))
        st, d = obs.state, obs.sensors["range"]
        ranges = {"right": float(d[0]), "front": float(d[len(d) // 2]), "left": float(d[-1])}
        v, w, st_ = self.follower.step({"x": float(st.pos[0]), "y": float(st.pos[1]), "yaw": float(st.yaw)}, ranges)
        self.status, self.note = st_.state, st_.note
        return np.array([v, w])
