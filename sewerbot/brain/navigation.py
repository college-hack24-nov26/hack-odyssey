"""Mission planning and a pose-feedback path follower (used by the simulator; the same (v, w) output drives hardware)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.mapspec import SewerMap
from ..core.profiles import RoverProfile


def fits_edge(profile: RoverProfile):
    return lambda e: profile.fits(e.section)["fits"]


def plan_route(m: SewerMap, profile: RoverProfile, src: str, dst: str) -> list[str]:
    return m.route(src, dst, fits_edge(profile))


def plan_inspect_all(m: SewerMap, profile: RoverProfile, src: str) -> tuple[list[str], list[str], list[str]]:
    """Visit every reachable goal (nearest first) and come back to the start. Returns (route, visited, skipped)."""
    ok = fits_edge(profile)
    remaining = {g.id: g.node for g in m.goals.values()}
    route, cur, visited, skipped = [src], src, [], []
    for gid, node in list(remaining.items()):
        try:
            m.route(src, node, ok)
        except ValueError:
            skipped.append(gid)
            remaining.pop(gid)
    def dist(a: str, b: str) -> float:
        p = m.route(a, b, ok)
        return sum(m.edge_between(p[i], p[i + 1]).length for i in range(len(p) - 1))
    while remaining:
        gid = min(remaining, key=lambda g: dist(cur, remaining[g]))
        leg = m.route(cur, remaining[gid], ok)
        route += leg[1:]
        cur = remaining.pop(gid)
        visited.append(gid)
    if cur != src:
        route += m.route(cur, src, ok)[1:]
    return route, visited, skipped


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class NavStatus:
    state: str = "idle"          # idle | running | arrived | blocked | done
    leg: int = 0
    n_legs: int = 0
    target_node: str | None = None
    cross_track: float = 0.0
    note: str = ""


class PathFollower:
    """Follow the polyline through node centres with heading feedback; pivot in place for sharp turns."""

    def __init__(self, m: SewerMap, profile: RoverProfile, route: list[str], cruise: float | None = None,
                 lookahead: float = 0.45, stop_range: float = 0.22):
        self.m, self.p = m, profile
        self.route = route
        self.pts = np.array([m.nodes[n].pos for n in route], float)
        self.cruise = min(cruise if cruise else profile.max_speed * 0.7, profile.max_speed)
        self.lookahead = max(lookahead, profile.length * 0.9)
        self.stop_range = stop_range
        self.leg = 0
        self.status = NavStatus("running" if len(route) > 1 else "arrived", 0, max(0, len(route) - 1),
                                route[1] if len(route) > 1 else None)
        self.pivoting = False

    def step(self, pose: dict, ranges: dict | None = None) -> tuple[float, float, NavStatus]:
        st = self.status
        if st.state in ("arrived", "done"):
            return 0.0, 0.0, st
        x, y, yaw = pose["x"], pose["y"], pose["yaw"]
        p = np.array([x, y])
        # advance legs when the next node centre is reached
        while self.leg < len(self.pts) - 1:
            node = self.m.nodes[self.route[self.leg + 1]]
            reach = max(0.3, 0.5 * self.m.chambers[node.id].apothem) if self.leg + 1 < len(self.pts) - 1 else 0.28
            if np.hypot(*(p - self.pts[self.leg + 1])) < reach:
                self.leg += 1
            else:
                break
        if self.leg >= len(self.pts) - 1:
            st.state, st.note = "arrived", f"reached {self.route[-1]}"
            return 0.0, 0.0, st
        a, b = self.pts[self.leg], self.pts[self.leg + 1]
        seg = b - a
        L = float(np.hypot(*seg))
        u = seg / L
        along = float((p - a) @ u)
        cross = float((p - a) @ np.array([-u[1], u[0]]))
        # lookahead target: ahead on this leg (or the next node if within reach)
        tgt = a + u * min(L, max(along, 0.0) + self.lookahead)
        desired = math.atan2(tgt[1] - y, tgt[0] - x)
        err = wrap(desired - yaw)
        st.leg, st.n_legs, st.target_node, st.cross_track, st.state = self.leg + 1, len(self.pts) - 1, self.route[self.leg + 1], cross, "running"
        if ranges and 0.0 < ranges.get("front", 9.0) < self.stop_range:
            st.state, st.note = "blocked", f"obstacle at {ranges['front']:.2f} m"
            return 0.0, 0.0, st
        if abs(err) > math.radians(55) or (self.pivoting and abs(err) > math.radians(12)):
            self.pivoting = True
            w = float(np.clip(1.8 * err, -self.p.max_yaw_rate, self.p.max_yaw_rate))
            w = math.copysign(max(abs(w), 0.25 * self.p.max_yaw_rate), err)
            return 0.0, w, st
        self.pivoting = False
        w = float(np.clip(2.2 * err, -self.p.max_yaw_rate, self.p.max_yaw_rate))
        v = self.cruise * max(0.25, math.cos(err) ** 2)
        remaining = L - along
        if self.leg == len(self.pts) - 2:               # slow into the final node
            v = min(v, max(0.08, 0.8 * remaining))
        return v, w, st
