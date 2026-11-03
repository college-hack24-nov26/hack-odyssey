"""Simulation manager: launches Webots worlds, talks to the Webots adapter and runs missions."""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..core import protocol
from ..core.mapspec import SewerMap, load_map
from ..core.profiles import PRESETS, RoverProfile, get_profile
from .navigation import PathFollower, plan_inspect_all, plan_route
from .report import write_report
from .session import Session
from .tracker import CameraModel, PipeFrame, evaluate_against_truth

ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = ROOT / "maps"


def list_maps() -> list[str]:
    return sorted(p.stem for p in MAPS_DIR.glob("*.yaml"))


class SimManager:
    def __init__(self, detector):
        self.session = Session("sim", detector)
        self.map: SewerMap | None = None
        self.profile: RoverProfile | None = None
        self.meta: dict | None = None
        self.world_path: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.follower: PathFollower | None = None
        self.mission = {"mode": "manual", "goal": None}
        self.mission_state = "idle"
        self.route: list[str] = []
        self.visited_goals: list[str] = []
        self.skipped_goals: list[str] = []
        self.manual = (0.0, 0.0, 0.0)
        self.link_state = "no simulation"
        self.report_dir: Path | None = None
        self.evaluation: dict | None = None
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()
        self._srv = None
        self._stop = False
        self.session.pipe_for_pose = self._pipe_for_pose
        threading.Thread(target=self._serve, daemon=True, name="sim-link").start()

    # ------------------------------------------------------------------ launching
    def launch(self, map_name: str, rover: str, overrides: dict | None = None, mission: dict | None = None,
               ambient: float = 0.35, mode: str = "realtime", defect_seed: int | None = None,
               defect_count: int | None = None) -> dict:
        from tools.gen_world import generate
        from tools.webots_runner import find_webots, kill_webots
        profile = get_profile(rover, **(overrides or {}))
        m = load_map(MAPS_DIR / f"{map_name}.yaml", defect_seed, defect_count)
        # refuse rovers that fit nowhere near the start
        fits = {e.id: profile.fits(e.section) for e in m.edges.values()}
        start_edges = [e for e in m.incident(m.start_node)]
        if not any(fits[e.id]["fits"] for e in start_edges):
            raise ValueError("this rover does not fit any pipe leaving the start chamber: "
                             + "; ".join(f"{e.id}: {fits[e.id]['reason']}" for e in start_edges))
        mission = mission or {"mode": "manual", "goal": None}
        goal = mission.get("goal")
        self.stop()
        wbt = generate(MAPS_DIR / f"{map_name}.yaml", profile, ambient=ambient, mission_goal=goal,
                       defect_seed=defect_seed, defect_count=defect_count)
        self.world_path = wbt
        self.meta = json.loads(wbt.with_suffix(".json").read_text())
        self.map, self.profile = m, profile
        self.session.reset()
        self.session.configure(CameraModel.from_profile(profile))
        self.mission_state = "waiting for rover"
        self.link_state = "starting Webots"
        self.report_dir = None
        self.evaluation = None
        self.set_mission(mission)
        kill_webots()
        self.proc = subprocess.Popen([str(find_webots()), f"--mode={mode}", str(wbt)])
        self.session.note(f"launched Webots: {map_name} / {profile.name}")
        return {"world": str(wbt), "fits": fits}

    def stop(self):
        try:
            if self._conn:
                self._conn.sendall(protocol.pack({"t": "quit"}))
        except OSError:
            pass
        from tools.webots_runner import kill_webots
        kill_webots()
        self.proc = None
        self.link_state = "stopped" if self.map else "no simulation"
        self.mission_state = "idle"

    # ------------------------------------------------------------------ missions
    def set_mission(self, mission: dict):
        self.mission = {"mode": mission.get("mode", "manual"), "goal": mission.get("goal")}
        self.follower, self.route, self.visited_goals, self.skipped_goals = None, [], [], []
        self.evaluation = None
        self._reported = False
        if not self.map or not self.profile:
            return
        mode = self.mission["mode"]
        start = self.map.start_node
        if mode == "goto":
            gid = self.mission["goal"] or next(iter(self.map.goals))
            node = self.map.goals[gid].node
            self.route = plan_route(self.map, self.profile, start, node)
            self.visited_goals = [gid]
        elif mode == "inspect_all":
            self.route, self.visited_goals, self.skipped_goals = plan_inspect_all(self.map, self.profile, start)
        if self.route:
            self.follower = PathFollower(self.map, self.profile, self.route)
            self.mission_state = "running"
            self.session.note(f"mission {mode}: route {' > '.join(self.route)}"
                              + (f" (skipped {self.skipped_goals})" if self.skipped_goals else ""))
        else:
            self.mission_state = "manual" if mode == "manual" else "idle"

    def drive(self, v: float, w: float):
        self.manual = (v, w, time.time() + 0.6)

    # ------------------------------------------------------------------ geometry for the tracker
    def _pipe_for_pose(self, pose: dict) -> PipeFrame | None:
        if not self.map:
            return None
        loc = self.map.locate(pose["x"], pose["y"])
        if "edge" not in loc:
            return None
        e = self.map.edges[loc["edge"]]
        o, f, _ = self.map.edge_frame(e.id)
        return PipeFrame(e.id, o, f, e.section, e.s_min, e.s_max)

    # ------------------------------------------------------------------ evaluation / report
    def finish(self):
        if not self.map or getattr(self, "_reported", False):
            return
        self._reported = True
        route_edges = {self.map.edge_between(self.route[i], self.route[i + 1]).id for i in range(len(self.route) - 1)} \
            if len(self.route) > 1 else set(self.map.edges)
        truth = [d for d in (self.meta or {}).get("defects", []) if d["edge"] in route_edges]
        found = [d for d in (self.session.tracker.defects if self.session.tracker else []) if d.confirmed]
        self.evaluation = evaluate_against_truth(found, truth)
        self.report_dir = write_report(ROOT / "runs" / "reports", self.map, self.profile, self.route, found, truth,
                                       self.evaluation, self.session.detector.name if self.session.detector else "-",
                                       self.mission, self.session.state()["log"])
        self.session.note(f"report written: {self.report_dir.name}  recall {self.evaluation['recall']:.0%}")

    # ------------------------------------------------------------------ TCP link to the Webots adapter
    def _serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Windows: SO_REUSEADDR lets a second process bind the same port and steal connections (two dashboards, a test run
        # and a dashboard ...); SO_EXCLUSIVEADDRUSE makes the second one fail loudly instead.
        srv.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            srv.bind((protocol.HOST, protocol.PORT))
        except OSError as e:
            self.link_state = f"port {protocol.PORT} unavailable (is another dashboard or Webots run using it?): {e}"
            return
        srv.listen(1)
        self._srv = srv
        while not self._stop:
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            self._conn = conn
            try:
                self._handle(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                self._conn = None
                if self.link_state.startswith("connected"):
                    self.link_state = "rover disconnected"

    def _handle(self, conn: socket.socket):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        rd = protocol.Reader()
        conn.settimeout(2.0)
        while not self._stop:
            try:
                data = conn.recv(1 << 20)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            for h, payload in rd.feed(data):
                t = h.get("t")
                if t == "hello":
                    self.link_state = "connected: " + (h.get("map") or "")
                    if self.mission_state == "waiting for rover":
                        self.mission_state = "running" if self.follower else "manual"
                    self.session.note("rover connected")
                elif t == "obs":
                    self._on_obs(conn, h, payload)

    def _on_obs(self, conn: socket.socket, h: dict, payload: bytes):
        bgr = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            return
        pose = h["pose"]
        self.session.push(bgr, h["time"], pose, h.get("ranges"), h.get("gt"))
        v = w = 0.0
        if self.follower and self.mission_state == "running":
            v, w, st = self.follower.step(pose, h.get("ranges"))
            if st.state in ("arrived", "done"):
                self.mission_state = "finished"
                self.finish()
            elif st.state == "blocked":
                self.mission_state = "blocked: " + st.note
        elif self.manual[2] > time.time():
            v, w = self.manual[0], self.manual[1]
        try:
            conn.sendall(protocol.pack(protocol.cmd_header("unicycle", {"v": v, "w": w})))
        except OSError:
            pass

    # ------------------------------------------------------------------ dashboard state
    def state(self) -> dict:
        s = self.session.state()
        nav = self.follower.status.__dict__ if self.follower else None
        s.update({
            "link": self.link_state, "mission": self.mission, "mission_state": self.mission_state,
            "route": self.route, "nav": nav, "visited_goals": self.visited_goals, "skipped_goals": self.skipped_goals,
            "map": self.map.name if self.map else None, "rover": self.profile.to_dict() if self.profile else None,
            "evaluation": self.evaluation, "report": self.report_dir.name if self.report_dir else None,
            "webots_running": bool(self.proc and self.proc.poll() is None),
        })
        return s
