"""Baseline for the search task: patrol a lawnmower pattern, divert to any target in view, resume the patrol afterwards."""
from __future__ import annotations

import math

import numpy as np

from .aerial import WaypointController
from .ground import GotoController


class SearchController:
    def __init__(self, task):
        self.task = task
        self.nav = WaypointController(task) if task.air else GotoController(task)
        self.patrol: list[tuple[float, float]] = []
        self.i = 0

    def reset(self):
        self.patrol, self.i = [], 0
        self.nav.reset()

    def _plan(self):
        """Parallel passes spaced a little under the detection range, so every part of the arena gets seen."""
        t = self.task
        w, d = t.wp["width"], t.wp["depth"]
        m = 1.2 + t.radius
        gap = max(1.0, 1.5 * t.p["find_range"] * math.sin(t.fov / 2))
        ys = np.arange(-d / 2 + m, d / 2 - m + 1e-6, gap)
        pts = []
        for k, y in enumerate(ys):
            xs = (-w / 2 + m, w / 2 - m)
            pts += [(xs[k % 2], y), (xs[(k + 1) % 2], y)]
        self.patrol = pts or [(0.0, 0.0)]
        self.i = 0

    def __call__(self, obs, episode, space=None) -> np.ndarray:
        st, info = obs.state, episode.info
        if not self.patrol:
            self._plan()
            self.i = min(range(len(self.patrol)), key=lambda k: math.hypot(self.patrol[k][0] - st.pos[0], self.patrol[k][1] - st.pos[1]))
        seen = self.task.perceive(st, episode)
        if seen:                                                    # approach what the camera sees until it counts as found
            bearing, rng, idx = seen
            tx, ty = info["targets"][idx]
            goal = (tx, ty)
            stop = 0.55 * self.task.p["find_range"]
            if rng < stop:
                goal = (st.pos[0], st.pos[1])
        else:
            gx, gy = self.patrol[self.i]
            if math.hypot(gx - st.pos[0], gy - st.pos[1]) < 0.8 + self.task.radius:
                self.i = (self.i + 1) % len(self.patrol)
                gx, gy = self.patrol[self.i]
            goal = (gx, gy)
        z = self.task.p["altitude"]
        info["goal"] = goal
        info["wps"] = [np.array([goal[0], goal[1], z])]
        info["idx"] = 0
        cmd = self.nav(obs, episode, space)
        if self.task.air and space != "position":                   # a flying camera looks forward: turn toward where it is going
            c, s = math.cos(st.yaw), math.sin(st.yaw)
            dx, dy = goal[0] - st.pos[0], goal[1] - st.pos[1]
            bearing = math.atan2(-s * dx + c * dy, c * dx + s * dy)
            cmd = np.array(cmd, float)
            if math.hypot(dx, dy) > 0.5:
                cmd[3] = max(-self.task.wmax * 0.5, min(self.task.wmax * 0.5, 1.5 * bearing))
        return cmd
