"""Run a mission inside the MuJoCo simulator, the way it will run on the real rover.

`MissionSim` plays the part of the rover and its link: every tick it renders the onboard 96 x 96 camera, integrates the gyro into a
heading, hands both to the `MissionRunner`, and plays back the `Drive(left, right, ms)` it gets: the wheels run at the commanded
duty for `ms` milliseconds and then stop, exactly as the firmware's bounded `D` command does. The runner never sees the simulator's
true positions; the task scores the run from them, and `MissionResult.trace` keeps them for tests and plots.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..catalog import get_vehicle
from ..envs import Sim
from ..spec.task import TaskSpec
from ..tasks import get_task_spec
from ..vehicles.ground import track_width
from .runner import CameraModel, DriveModel, MissionRunner, Obs
from .steps import Mission
from .targets import TargetRegistry


@dataclass
class MissionResult:
    status: str                                  # done | failed | timeout
    reason: str
    message: str
    sim_time: float
    ticks: int
    path_true_m: float                           # distance the rover really drove
    path_dead_reckoned_m: float                  # what the runner believed from the commands
    laps: float                                  # orbit progress the runner counted from the gyro
    collisions: int
    events: list = field(default_factory=list)
    trace: list = field(default_factory=list)    # per tick (and one last row for the final state): {"t", "x", "y", "yaw", "objects": {target: (x, y)}, "drive": (l, r, ms), "step"}
    frames: list = field(default_factory=list)   # RGB frames, when keep_frames was set
    task: dict = field(default_factory=dict)     # what the task scored from ground truth: success, status, collisions, dist_<target>

    @property
    def success(self) -> bool:
        return self.status == "done"


class MissionSim:
    """One rover, one task, one mission. `run()` returns a MissionResult."""

    def __init__(self, mission: Mission, *, task: str = "bag_mission", world: dict | None = None, vehicle: str = "micro_2wd", seed: int = 0,
                 tick_s: float = 0.5, latency_s: float = 0.0, gyro_bias: float = 0.0, hazards: dict | None = None,
                 registry: TargetRegistry | None = None, drive: DriveModel | None = None, keep_frames: bool = False):
        spec = get_task_spec(task)
        d = spec.to_dict()
        d["world"] = {**spec.world, **(world or {})}
        self.mission = mission
        self.seed, self.tick_s, self.latency_s, self.gyro_bias = seed, tick_s, latency_s, gyro_bias
        self.keep_frames = keep_frames
        base = get_vehicle(vehicle)
        v = base.override(cam_width_px=96, cam_height_px=96, cam_forward=base.body["length"] / 2)      # the real camera's frame size, mounted at the nose
        self.sim = Sim(v, TaskSpec.from_dict(d), space="wheels", seed=seed, hazards=hazards)
        self.registry = registry or self.sim.task.targets
        cam = self.sim.vehicle.sensor("camera")
        self.camera_name = cam.name
        self.camera = CameraModel(cam.options["width_px"], cam.options["height_px"], cam.options["fov_deg"])
        p = self.sim.vehicle.params
        self.model = drive or DriveModel(max_speed=p["max_speed"], track=track_width(self.sim.vehicle.body["width"], p["wheel_radius"]))
        self._hidden: set[str] = set()

    # ---- the world, for tests
    def hide(self, target_id: str) -> None:
        """Take every object of a target out of the scene from now on (a bag picked up, a box covered)."""
        self._hidden.add(target_id)

    def _object_ids(self) -> dict:
        return {o["name"]: o for o in self.sim.episode.info["objects"]}

    def _move_objects(self, t: float) -> None:
        be, task = self.sim.backend, self.sim.task
        for o in self.sim.episode.info["objects"]:
            moving, hidden = len(o["path"]) > 1, o["target"] in self._hidden
            if not (moving or hidden):
                continue
            x, y = task.object_pos(o, t) if moving else o["pos"]
            o["pos"] = (x, y)
            mid = int(be.model.body_mocapid[be.model.body(o["name"]).id])
            be.data.mocap_pos[mid] = (x, y, -10.0 if hidden else be._item_z[o["name"]])

    # ---- the rover
    def _render(self) -> np.ndarray:
        return self.sim.backend.render(self.camera_name)

    def run(self, max_time: float | None = None, on_tick=None) -> MissionResult:
        sim = self.sim
        sim.reset(seed=self.seed)
        runner = MissionRunner(self.mission, self.registry, camera=self.camera, drive=self.model, tick_s=self.tick_s)
        dt = sim.backend.policy_dt
        n_tick = max(1, round(self.tick_s / dt))
        limit = max_time if max_time is not None else float(sim.episode.info["max_time"])
        yaw, path, last_xy = 0.0, 0.0, sim.obs.state.pos[:2].copy()
        n_late = min(n_tick - 1, round(self.latency_s / dt))       # the frame the runner gets was taken this many sim steps before the tick ended
        frame = self._render()
        trace, kept, ticks, info = [], [], 0, {}
        while not runner.finished and sim.t < limit:
            if on_tick is not None:
                on_tick(self, ticks, runner)
            drive = runner.tick(Obs(frame, yaw, sim.t))
            if runner.finished and runner.status == "failed":
                drive = runner.last
            sl, sr = self.model.speed(drive.left), self.model.speed(drive.right)
            n_on = min(n_tick, round(drive.ms / 1000.0 / dt)) if not drive.stopped else 0
            trace.append({"t": sim.t, "x": float(sim.obs.state.pos[0]), "y": float(sim.obs.state.pos[1]), "yaw": float(sim.obs.state.yaw),
                          "objects": {o["target"]: tuple(o["pos"]) for o in sim.episode.info["objects"]},
                          "drive": (drive.left, drive.right, drive.ms), "step": runner.step_index + 1})
            for k in range(n_tick):
                self._move_objects(sim.t)
                _, _, term, trunc, info = sim.step_physical((sl, sr) if k < n_on else (0.0, 0.0))
                st = sim.obs.state
                yaw += (float(st.omega_body[2]) + self.gyro_bias) * dt
                path += float(np.hypot(*(st.pos[:2] - last_xy)))
                last_xy = st.pos[:2].copy()
                if k == n_tick - 1 - n_late:
                    frame = self._render()
            sim.episode.info["mission_status"] = runner.status
            ticks += 1
            if self.keep_frames:
                kept.append(frame)
        trace.append({"t": sim.t, "x": float(sim.obs.state.pos[0]), "y": float(sim.obs.state.pos[1]), "yaw": float(sim.obs.state.yaw),
                      "objects": {o["target"]: tuple(o["pos"]) for o in sim.episode.info["objects"]}, "drive": (0, 0, 0), "step": runner.step_index + 1})
        # let the task see the final status (scoring) and tell the runner the time is up if it was still running
        timed_out = not runner.finished
        sim.episode.info["mission_status"] = runner.status
        collisions = int(sim.episode.info["collisions"])
        scored = sim.task.step(sim.obs, np.zeros(sim.space.dim), sim.episode, sim.t).info
        return MissionResult("timeout" if timed_out else runner.status, runner.reason or ("ran out of time" if timed_out else ""),
                             runner.message, float(sim.t), ticks, path, runner.odometer, runner.laps, collisions, runner.events, trace, kept, scored)

    def close(self) -> None:
        self.sim.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def camera_xy(self, trace_row: dict) -> tuple[float, float]:
        fwd = self.sim.vehicle.sensor("camera").options["forward"]
        return trace_row["x"] + fwd * math.cos(trace_row["yaw"]), trace_row["y"] + fwd * math.sin(trace_row["yaw"])

    def true_gap(self, trace_row: dict, target_id: str) -> float:
        """Distance (m) from the camera to the nearest point of a target's surface, from a trace row (ground truth, for tests)."""
        ox, oy = trace_row["objects"][target_id]
        t = self.registry.get(target_id)
        fwd = self.sim.vehicle.sensor("camera").options["forward"]
        dx, dy = trace_row["x"] + fwd * math.cos(trace_row["yaw"]) - ox, trace_row["y"] + fwd * math.sin(trace_row["yaw"]) - oy
        if t.shape == "box":
            yaw = next(o["yaw"] for o in self.sim.episode.info["objects"] if o["target"] == target_id)
            c, s = math.cos(yaw), math.sin(yaw)
            lx, ly = c * dx + s * dy, -s * dx + c * dy
            half = t.footprint_m
            return math.hypot(max(abs(lx) - half, 0.0), max(abs(ly) - half, 0.0))
        return math.hypot(dx, dy) - t.footprint_m
