"""Sewer network description (YAML) -> validated geometry, routing and coordinate transforms."""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .defects import CLASSES, SUBTYPES, DefectSpec
from .geometry import Chamber, Section, layout_chamber

MIN_PIPE_CLEAR = 1.0         # metres of straight pipe between two chambers
MIN_BEARING_GAP = math.radians(25)


@dataclass
class Node:
    id: str
    pos: tuple[float, float]
    kind: str = "junction"


@dataclass
class Edge:
    id: str
    a: str
    b: str
    section: Section
    material: str = "concrete"
    # filled in by SewerMap
    length: float = 0.0           # centre-to-centre
    s_min: float = 0.0            # first pipe metre (chamber face on node a)
    s_max: float = 0.0            # last pipe metre (chamber face on node b)
    direction: tuple[float, float] = (1.0, 0.0)


@dataclass
class Goal:
    id: str
    node: str
    color: str = "red"


@dataclass
class SewerMap:
    name: str
    nodes: dict[str, Node]
    edges: dict[str, Edge]
    goals: dict[str, Goal]
    defects: list[DefectSpec]
    start_node: str
    chambers: dict[str, Chamber] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    # ---- graph ------------------------------------------------------------
    def incident(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges.values() if node_id in (e.a, e.b)]

    def neighbors(self, node_id: str):
        for e in self.incident(node_id):
            yield (e.b if e.a == node_id else e.a), e

    def route(self, src: str, dst: str, edge_ok=None) -> list[str]:
        """Shortest node path (Dijkstra on centre-to-centre length). `edge_ok(edge)->bool` can forbid pipes
        (e.g. ones the rover does not fit). Raises ValueError if unreachable."""
        if src not in self.nodes or dst not in self.nodes:
            raise KeyError("unknown node")
        dist = {src: 0.0}
        prev: dict[str, str] = {}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                break
            if d > dist.get(u, math.inf):
                continue
            for v, e in self.neighbors(u):
                if edge_ok is not None and not edge_ok(e):
                    continue
                nd = d + e.length
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
        if dst not in dist:
            raise ValueError(f"no route from {src} to {dst}")
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        return path[::-1]

    def edge_between(self, a: str, b: str) -> Edge:
        for e in self.edges.values():
            if {e.a, e.b} == {a, b}:
                return e
        raise KeyError(f"no pipe between {a} and {b}")

    def waypoints(self, route: list[str]) -> list[tuple[float, float]]:
        return [self.nodes[n].pos for n in route]

    def reachable_goals(self) -> dict[str, bool]:
        out = {}
        for g in self.goals.values():
            try:
                self.route(self.start_node, g.node)
                out[g.id] = True
            except ValueError:
                out[g.id] = False
        return out

    # ---- coordinates --------------------------------------------------------
    def edge_frame(self, edge_id: str):
        e = self.edges[edge_id]
        ax, ay = self.nodes[e.a].pos
        dx, dy = e.direction
        return np.array([ax, ay]), np.array([dx, dy]), np.array([dy, -dx])  # origin, forward, right

    def world_point(self, edge_id: str, s: float, r: float, z_axis: float) -> tuple[float, float, float]:
        e = self.edges[edge_id]
        o, f, rt = self.edge_frame(edge_id)
        p = o + f * s + rt * r
        return float(p[0]), float(p[1]), float(e.section.fo + z_axis)

    def edge_bounds(self, edge_id: str) -> tuple[float, float]:
        e = self.edges[edge_id]
        return e.s_min, e.s_max

    def locate(self, x: float, y: float) -> dict:
        """Which pipe/chamber is a floor-plane point in? Returns {'node':id}|{'edge':id,'s':..,'lateral':..}."""
        for nid, n in self.nodes.items():
            ch = self.chambers[nid]
            if math.hypot(x - n.pos[0], y - n.pos[1]) <= ch.apothem * 1.05:
                return {"node": nid}
        best = None
        for eid, e in self.edges.items():
            o, f, rt = self.edge_frame(eid)
            d = np.array([x, y]) - o
            s, lat = float(d @ f), float(d @ rt)
            if e.s_min - 0.05 <= s <= e.s_max + 0.05 and abs(lat) <= e.section.width / 2 + 0.1:
                if best is None or abs(lat) < abs(best["lateral"]):
                    best = {"edge": eid, "s": s, "lateral": lat}
        return best or {}

    def bounding_box(self, pad: float = 2.0) -> tuple[float, float, float, float]:
        xs = [n.pos[0] for n in self.nodes.values()]
        ys = [n.pos[1] for n in self.nodes.values()]
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad

    def summary(self) -> dict:
        return {
            "name": self.name,
            "nodes": {k: {"pos": list(v.pos), "kind": v.kind, "apothem": round(self.chambers[k].apothem, 3)}
                      for k, v in self.nodes.items()},
            "edges": {k: {"a": e.a, "b": e.b, "shape": e.section.shape, "width": e.section.width,
                          "height": e.section.height, "length": round(e.length, 3),
                          "s_min": round(e.s_min, 3), "s_max": round(e.s_max, 3),
                          "floor_width": round(e.section.floor_width, 3),
                          "clear_height": round(e.section.clear_height, 3)}
                      for k, e in self.edges.items()},
            "goals": {k: {"node": g.node, "color": g.color} for k, g in self.goals.items()},
            "defects": [{"id": d.id, "edge": d.edge, "s": d.s, "angle": d.angle, "type": d.type,
                         "subtype": d.subtype, "size": list(d.size), "severity": d.severity}
                        for d in self.defects],
            "start": self.start_node,
        }


GOAL_COLORS = {"red": (0.9, 0.1, 0.1), "green": (0.1, 0.8, 0.2), "blue": (0.15, 0.3, 0.95),
               "yellow": (0.95, 0.85, 0.1), "magenta": (0.9, 0.2, 0.85), "cyan": (0.1, 0.85, 0.9),
               "orange": (0.95, 0.5, 0.05), "white": (0.95, 0.95, 0.95)}


def _section_from(d: dict) -> Section:
    shape = d.get("shape", "circular")
    if shape == "circular":
        dia = float(d.get("diameter", d.get("width", 0.9)))
        return Section.make("circular", dia)
    w = float(d["width"])
    h = float(d.get("height", w))
    return Section.make(shape, w, h)


def _random_defects(m: SewerMap, count: int, seed: int) -> list[DefectSpec]:
    rng = random.Random(seed)
    out: list[DefectSpec] = []
    edges = list(m.edges.values())
    weights = [max(0.1, e.s_max - e.s_min) for e in edges]
    for i in range(count):
        e = rng.choices(edges, weights)[0]
        typ = "crack" if rng.random() < 0.55 else "leak"
        sub = rng.choice(SUBTYPES[typ])
        span = e.s_max - e.s_min
        s = e.s_min + 0.8 + rng.random() * max(0.1, span - 1.6)
        # walls and crown are likelier than the floor
        angle = rng.choice([rng.uniform(-30, 20), rng.uniform(150, 210), rng.uniform(40, 140),
                            rng.uniform(40, 140), rng.uniform(235, 305)])
        w = rng.uniform(0.25, 0.7) if typ == "crack" else rng.uniform(0.3, 0.8)
        h = rng.uniform(0.25, 0.7)
        out.append(DefectSpec(f"R{i + 1:02d}", e.id, round(s, 2), round(angle % 360, 1), typ, sub,
                              (round(w, 2), round(h, 2)), seed=rng.randrange(10**6),
                              severity=round(rng.uniform(0.25, 1.0), 2)).validate())
    return out


def build_map(spec: dict) -> SewerMap:
    name = spec.get("name", "unnamed")
    nodes: dict[str, Node] = {}
    for nid, nd in (spec.get("nodes") or {}).items():
        pos = nd["pos"] if isinstance(nd, dict) else nd
        nodes[str(nid)] = Node(str(nid), (float(pos[0]), float(pos[1])),
                               nd.get("kind", "junction") if isinstance(nd, dict) else "junction")
    if len(nodes) < 2:
        raise ValueError("a map needs at least two nodes")

    edges: dict[str, Edge] = {}
    for i, ed in enumerate(spec.get("edges") or []):
        eid = str(ed.get("id", f"e{i + 1}"))
        if eid in edges:
            raise ValueError(f"duplicate edge id {eid}")
        a, b = str(ed["from"]), str(ed["to"])
        for n in (a, b):
            if n not in nodes:
                raise ValueError(f"edge {eid}: unknown node {n}")
        if a == b:
            raise ValueError(f"edge {eid}: from == to")
        edges[eid] = Edge(eid, a, b, _section_from(ed), ed.get("material", "concrete"))

    # directions / lengths
    for e in edges.values():
        ax, ay = nodes[e.a].pos
        bx, by = nodes[e.b].pos
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-6:
            raise ValueError(f"edge {e.id}: nodes coincide")
        e.length, e.direction = L, ((bx - ax) / L, (by - ay) / L)

    # chambers
    chambers: dict[str, Chamber] = {}
    for nid, n in nodes.items():
        atts, tops = [], []
        for e in edges.values():
            if nid not in (e.a, e.b):
                continue
            other = nodes[e.b if e.a == nid else e.a].pos
            bearing = math.atan2(other[1] - n.pos[1], other[0] - n.pos[0])
            atts.append((e.id, bearing, e.section.width / 2))
            tops.append(e.section.top)
        if not atts:
            raise ValueError(f"node {nid} has no pipes attached")
        bs = sorted(a[1] % (2 * math.pi) for a in atts)
        for i in range(len(bs)):
            gap = (bs[(i + 1) % len(bs)] - bs[i]) % (2 * math.pi)
            if len(bs) > 1 and gap < MIN_BEARING_GAP:
                raise ValueError(f"node {nid}: two pipes leave within {math.degrees(gap):.0f} deg of each other "
                                 f"(minimum {math.degrees(MIN_BEARING_GAP):.0f})")
        chambers[nid] = layout_chamber(atts, section_tops=tops)

    for e in edges.values():
        e.s_min = chambers[e.a].apothem
        e.s_max = e.length - chambers[e.b].apothem
        if e.s_max - e.s_min < MIN_PIPE_CLEAR:
            raise ValueError(f"edge {e.id}: only {e.s_max - e.s_min:.2f} m of pipe between chambers "
                             f"(minimum {MIN_PIPE_CLEAR} m) - move the nodes apart")

    goals = {}
    for g in spec.get("goals") or []:
        gid = str(g["id"])
        if str(g["node"]) not in nodes:
            raise ValueError(f"goal {gid}: unknown node {g['node']}")
        if g.get("color", "red") not in GOAL_COLORS:
            raise ValueError(f"goal {gid}: colour must be one of {sorted(GOAL_COLORS)}")
        goals[gid] = Goal(gid, str(g["node"]), g.get("color", "red"))

    start = str((spec.get("start") or {}).get("node", next(iter(nodes))))
    if start not in nodes:
        raise ValueError(f"start node {start} unknown")

    m = SewerMap(name, nodes, edges, goals, [], start, chambers, meta=dict(spec.get("meta") or {}))

    defects: list[DefectSpec] = []
    for i, d in enumerate(spec.get("defects") or []):
        eid = str(d["edge"])
        if eid not in edges:
            raise ValueError(f"defect {i}: unknown edge {eid}")
        size = d.get("size", [0.3, 0.3])
        ds = DefectSpec(str(d.get("id", f"D{i + 1:02d}")), eid, float(d["s"]), float(d.get("angle", 90)),
                        d.get("type", "crack"), d.get("subtype", "longitudinal"),
                        (float(size[0]), float(size[1])), int(d.get("seed", i)),
                        float(d.get("severity", 0.6))).validate()
        lo, hi = m.edge_bounds(eid)
        if not (lo + ds.size[1] / 2 <= ds.s <= hi - ds.size[1] / 2):
            raise ValueError(f"defect {ds.id}: s={ds.s} outside pipe {eid} ({lo:.2f}..{hi:.2f} m)")
        defects.append(ds)
    rnd = spec.get("random_defects")
    if rnd:
        defects += _random_defects(m, int(rnd.get("count", 10)), int(rnd.get("seed", 0)))
    ids = [d.id for d in defects]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate defect ids")
    m.defects = defects

    bad = [g for g, ok in m.reachable_goals().items() if not ok]
    if bad:
        raise ValueError(f"goals not reachable from start {start}: {bad}")
    return m


def load_map(path: str | Path, defect_seed: int | None = None, defect_count: int | None = None) -> SewerMap:
    """Load a YAML map. `defect_seed` / `defect_count` re-roll the random defects (used for dataset variety)."""
    with open(path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    if defect_seed is not None or defect_count is not None:
        rnd = dict(spec.get("random_defects") or {})
        if defect_seed is not None:
            rnd["seed"] = defect_seed
        if defect_count is not None:
            rnd["count"] = defect_count
        rnd.setdefault("count", 12)
        spec["random_defects"] = rnd
    return build_map(spec)
