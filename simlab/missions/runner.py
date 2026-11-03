"""MissionRunner: turns camera frames and a gyro heading into bounded drive commands, one control tick at a time.

The runner knows nothing about the simulator. Each tick it is given an `Obs` (the newest RGB frame, the heading integrated
from the gyro since the start, the time) and answers with a `Drive(left_duty, right_duty, ms)`: exactly what the real rover's
bounded `D <left> <right> <ms>` command carries. The rover moves for `ms` milliseconds and then stops by itself, so a lost
link or a crashed laptop always ends with the wheels stopped. The runner works stop-and-look: pulse, settle, look, decide.

Behaviour per step (see steps.py for the vocabulary):
  go_to    scan if the target is not in view, turn toward it, drive up to `stop_at_cm`, stop.
  follow   hold a gap to a target that moves; never reverses.
  orbit    polygon hops around the target: turn to the tangent by gyro, hop, turn back until the camera sees the target again and
           re-measure radius and angle. Laps = net gyro heading change / 360 degrees.
  stop_at  go_to, then confirm the goal is within `within_cm` on consecutive looks; ends the mission.
Every step has a time limit and a path-length limit (the path is dead-reckoned from the commands, since the gyro gives no distance).
A target that is not seen for `lost_ticks` ticks in a row stops the mission.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Generator, Mapping

import numpy as np

from .steps import Mission, Step
from .targets import CameraModel, Sighting, Target

__all__ = ["CameraModel", "Drive", "DriveModel", "MissionRunner", "Obs", "STOP", "StepFailed"]


# ------------------------------------------------------------------------------------------------ commands
@dataclass(frozen=True)
class Drive:
    """Left and right motor duty (-255..255) held for `ms` milliseconds, then stop."""
    left: int
    right: int
    ms: int

    def line(self) -> str:
        """The serial/Wi-Fi command for the rover firmware."""
        return f"D {self.left} {self.right} {self.ms}"

    @property
    def stopped(self) -> bool:
        return self.ms <= 0 or (self.left == 0 and self.right == 0)


STOP = Drive(0, 0, 0)


@dataclass
class DriveModel:
    """How duty maps to motion on this rover (calibrate `max_speed` and `scale` from a timed straight run on the real one).

    The firmware raises any non-zero duty below `min_duty` to `min_duty` (the motors only hum below it). Slower motion than that
    is made by shorter pulses, so the runner asks for an *average* velocity over a tick and this model picks duty and pulse length.
    """
    max_speed: float = 0.25          # m/s at duty 255
    track: float = 0.104             # m between the wheel centres
    min_duty: int = 90
    max_duty: int = 255
    max_ms: int = 350                # the longest single pulse (the firmware enforces its own bound too)
    min_ms: int = 60                 # the shortest pulse worth sending: the motors need about this long to start moving
    scale: float = 1.0               # measured distance / nominal distance (config speed_scale)

    def speed(self, duty: float) -> float:
        """Wheel ground speed in m/s for a duty, including the firmware's minimum-duty rule."""
        d = abs(duty)
        if d < 1:
            return 0.0
        return math.copysign(self.max_speed * min(max(d, self.min_duty), self.max_duty) / 255.0, duty)

    def drive(self, v: float, w: float, window_s: float) -> Drive:
        """Duty and pulse length that give an average forward speed `v` (m/s) and turn rate `w` (rad/s, + = left) over `window_s`."""
        if abs(v) < 1e-4 and abs(w) < 1e-3:
            return STOP
        half = self.track / 2.0
        sl, sr = v - w * half, v + w * half
        peak = max(abs(sl), abs(sr))
        window_ms = window_s * 1000.0
        ms = min(float(self.max_ms), window_ms)
        duty = 255.0 * (peak / self.scale) * (window_ms / ms) / self.max_speed          # duty needed if the pulse lasts `ms`
        if duty > self.max_duty:
            duty = float(self.max_duty)
        elif duty < self.min_duty:
            ms = max(float(self.min_ms), ms * duty / self.min_duty)                   # same average motion: a shorter pulse at minimum duty
            duty = float(self.min_duty)
        k = duty / peak
        left, right = round(sl * k), round(sr * k)
        left, right = (self._lift(left), self._lift(right))
        return Drive(left, right, round(ms))

    def _lift(self, duty: int) -> int:
        """What the firmware would do to a duty: a non-zero one below the minimum becomes the minimum. (Applying it here keeps the
        runner's own picture of the motion honest; an arc whose inner wheel falls below it turns tighter than asked for.)"""
        return int(math.copysign(self.min_duty, duty)) if 0 < abs(duty) < self.min_duty else duty

    def distance(self, d: Drive) -> float:
        """Path length (m) a drive command covers: dead reckoning from the duties."""
        return abs(0.5 * (self.speed(d.left) + self.speed(d.right))) * d.ms / 1000.0 * self.scale


@dataclass
class Obs:
    """What the runner is given each tick."""
    frame: np.ndarray | None         # newest camera frame, RGB uint8 (None: no frame yet)
    yaw: float                       # heading integrated from the gyro since the start, rad (counter-clockwise positive, not wrapped)
    t: float                         # seconds since the start


class StepFailed(Exception):
    """A step cannot continue; `code` is a short machine word, the message says why in plain words."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# ------------------------------------------------------------------------------------------------ the runner
class MissionRunner:
    ARRIVE_TOL = 0.03                # m: closer than stop distance + this counts as arrived
    TURN_FIRST = 0.30                # rad: a target this far off-axis is turned toward before driving
    CENTRED = 0.09                   # rad: "facing the target"
    TURN_TOL = 0.07                  # rad: a gyro-closed turn is finished within this
    HOP_DEG = 30.0                   # orbit: angle around the target covered by one hop

    def __init__(self, mission: Mission, targets: Mapping[str, Target] | object, *, camera: CameraModel | None = None,
                 drive: DriveModel | None = None, tick_s: float = 0.5, lost_ticks: int = 5):
        self.mission = mission
        self._targets = targets
        self.camera = camera or CameraModel()
        self.model = drive or DriveModel()
        self.tick_s = float(tick_s)
        self.lost_ticks = int(lost_ticks)
        self.v_cap = 0.8 * self.model.max_speed * min(self.model.max_ms / (self.tick_s * 1000.0), 1.0)
        self.status = "idle"                    # idle | running | done | failed
        self.reason = ""                        # why it failed (empty otherwise)
        self.message = ""
        self.step_index = -1
        self.events: list[dict] = []
        self.odometer = 0.0                     # m of path driven, dead-reckoned, whole mission
        self.laps = 0.0                         # orbit progress, from gyro heading
        self.last: Drive = STOP
        self.obs: Obs = Obs(None, 0.0, 0.0)
        self._gen: Generator | None = None
        self._step_t0 = 0.0
        self._step_dist = 0.0
        self._limit_t, self._limit_d = 0.0, 0.0
        self._miss = 0
        self._seen_once = False
        self._scan_dir = 1.0

    # ---- public
    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")

    @property
    def step(self) -> Step | None:
        return self.mission.steps[self.step_index] if 0 <= self.step_index < len(self.mission.steps) else None

    def progress(self) -> dict:
        s = self.step
        return {"status": self.status, "reason": self.reason, "message": self.message, "step": self.step_index + 1, "of": len(self.mission.steps),
                "op": s.op if s else None, "target": s.target if s else None, "laps": round(self.laps, 2),
                "path_m": round(self.odometer, 2)}

    def tick(self, obs: Obs) -> Drive:
        """One control tick: look at `obs`, return the next bounded drive command."""
        if self.finished:
            return STOP
        self.obs = obs
        try:
            if self._gen is None:
                self.status, self._gen = "running", self._run()
            elif self.step is not None:
                if obs.t - self._step_t0 > self._limit_t:
                    raise StepFailed("timeout", f"step {self.step_index + 1} ({self.step.op}) ran out of time ({self._limit_t:.0f} s)")
                if self._step_dist > self._limit_d:
                    raise StepFailed("distance_limit", f"step {self.step_index + 1} ({self.step.op}) drove more than {self._limit_d * 100:.0f} cm")
            self.last = next(self._gen)                  # the generator reads the new observation from self.obs
        except StopIteration:
            self.status, self.last = "done", STOP
            self._event("mission complete")
        except StepFailed as e:
            self.status, self.reason, self.message, self.last = "failed", e.code, str(e), STOP
            self._event(f"stopped: {e}")
        return self.last

    # ---- plumbing used by the step generators
    def _event(self, text: str) -> None:
        self.events.append({"t": round(self.obs.t, 2), "step": self.step_index + 1, "text": text})

    def _target(self, tid: str) -> Target:
        return self._targets.get(tid) if hasattr(self._targets, "get") else self._targets[tid]

    def _cmd(self, v: float, w: float) -> Drive:
        d = self.model.drive(v, w, self.tick_s)
        dist = self.model.distance(d)
        self.odometer += dist
        self._step_dist += dist
        return d

    def _pivot(self, err: float) -> Drive:
        return self._cmd(0.0, 0.9 * err / self.tick_s)

    def _peek(self, t: Target) -> Sighting | None:
        f = self.obs.frame
        return t.locate(f, self.camera) if f is not None else None

    def _see(self, t: Target) -> Sighting | None:
        """Like _peek, but a target missing for too long ends the mission (the rover stands still while it is missing)."""
        s = self._peek(t)
        if s is None:
            self._miss += 1
            if self._miss > self.lost_ticks and self._seen_once:
                raise StepFailed("lost_target", f"lost sight of {t.id!r} for {self._miss} looks in a row: stopping")
        else:
            self._miss, self._seen_once = 0, True
            self._scan_dir = 1.0 if s.bearing >= 0 else -1.0
        return s

    def _begin(self, st: Step) -> None:
        self._step_t0, self._step_dist = self.obs.t, 0.0
        self._limit_t, self._limit_d = st.limits()
        self._miss, self._seen_once = 0, False
        self._event(f"start {st.op} {st.target}")

    def _run(self) -> Generator:
        for i, st in enumerate(self.mission.steps):
            self.step_index = i
            self._begin(st)
            yield from getattr(self, "_do_" + st.op)(st)
            self._event(f"{st.op} {st.target} done")
        return

    # ---- reusable movements
    def _turn_to(self, heading: float) -> Generator:
        """Pivot until the gyro heading is `heading` (rad, same unbounded frame as obs.yaw)."""
        for _ in range(60):
            err = wrap(heading - self.obs.yaw)
            if abs(err) <= self.TURN_TOL:
                return
            yield self._pivot(err)
        raise StepFailed("turn_stalled", "the rover could not complete a turn: check the wheels and the gyro")

    def _straight(self, length: float, heading: float) -> Generator:
        """Drive about `length` metres, holding the gyro heading."""
        start = self._step_dist
        while self._step_dist - start < length:
            left = length - (self._step_dist - start)
            err = wrap(heading - self.obs.yaw)
            yield self._cmd(min(self.v_cap, max(0.03, 0.8 * left / self.tick_s)), 0.7 * err / self.tick_s)

    def _acquire(self, t: Target) -> Generator:
        """Find the target in view: look, and if it is not there turn (toward where it was last seen) up to one full circle."""
        s = self._peek(t)
        origin, swept = self.obs.yaw, 0.0
        while s is None:
            if swept > 2 * math.pi + 0.3:
                raise StepFailed("target_not_found", f"cannot find {t.id!r}: turned a full circle without seeing it")
            yield from self._turn_to(self.obs.yaw + self._scan_dir * 0.45)
            swept = abs(self.obs.yaw - origin)
            s = self._peek(t)
        self._seen_once, self._miss = True, 0
        return s

    def _centre(self, t: Target) -> Generator:
        """Pivot until the target is straight ahead (camera closed loop); returns the sighting."""
        for _ in range(40):
            s = self._see(t)
            if s is not None and abs(s.bearing) < self.CENTRED:
                return s
            yield self._pivot(s.bearing) if s is not None else STOP
        raise StepFailed("turn_stalled", f"could not turn to face {t.id!r}")

    def _reacquire(self, t: Target, facing: float) -> Generator:
        """After a blind turn toward `facing`: find the target again, sweeping a little either side, and centre on it."""
        s = self._peek(t)
        for k in range(1, 8):
            if s is not None:
                break
            yield from self._turn_to(facing + (0.5 * k if k % 2 else -0.5 * k))
            s = self._peek(t)
        if s is None:
            raise StepFailed("lost_target", f"lost sight of {t.id!r} while circling it: stopping")
        self._miss = 0
        return (yield from self._centre(t))

    def _approach(self, t: Target, stop_m: float) -> Generator:
        """Drive toward the target until its near surface is `stop_m` from the camera; returns the last sighting."""
        yield from self._acquire(t)
        while True:
            s = self._see(t)
            if s is None:
                yield STOP
                continue
            if s.distance <= stop_m + self.ARRIVE_TOL and abs(s.bearing) < 0.25:
                return s
            if abs(s.bearing) > self.TURN_FIRST or s.distance <= stop_m + self.ARRIVE_TOL:
                yield self._pivot(s.bearing)
            else:
                yield self._cmd(min(self.v_cap, 0.6 * (s.distance - stop_m) / self.tick_s), 0.9 * s.bearing / self.tick_s)

    # ---- the steps
    def _do_go_to(self, st: Step) -> Generator:
        yield from self._approach(self._target(st.target), st.stop_at_cm / 100.0)

    def _do_stop_at(self, st: Step) -> Generator:
        t, within = self._target(st.target), st.within_cm / 100.0
        yield from self._approach(t, max(0.15, within - 0.05))
        ok = 0
        for _ in range(12):                                     # confirm on consecutive looks while standing still
            s = self._see(t)
            ok = ok + 1 if s is not None and s.distance <= within else 0
            if ok >= 2:
                return
            yield STOP
        raise StepFailed("goal_not_confirmed", f"stopped near {t.id!r} but could not confirm it within {st.within_cm:g} cm")

    def _do_follow(self, st: Step) -> Generator:
        t, gap = self._target(st.target), st.gap_cm / 100.0
        yield from self._acquire(t)
        end = self.obs.t + st.duration_s
        while self.obs.t < end:
            s = self._see(t)
            if s is None:
                yield STOP
                continue
            err = s.distance - gap
            if abs(s.bearing) > self.TURN_FIRST:
                yield self._pivot(s.bearing)
            elif err > self.ARRIVE_TOL:
                yield self._cmd(min(self.v_cap, 0.7 * err / self.tick_s), 0.9 * s.bearing / self.tick_s)
            elif abs(s.bearing) > 0.12:
                yield self._pivot(s.bearing)
            else:
                yield STOP

    def _do_orbit(self, st: Step) -> Generator:
        t = self._target(st.target)
        radius, foot, dirn = st.radius_cm / 100.0, t.footprint_m, (1.0 if st.direction == "ccw" else -1.0)
        yield from self._approach(t, radius - foot)
        s = yield from self._centre(t)
        hop = math.radians(self.HOP_DEG)
        length = 2.0 * radius * math.sin(hop / 2.0)
        psi0 = self.obs.yaw + s.bearing                         # direction of the line of sight in the gyro frame: it turns 360 degrees per lap
        total = st.laps * 2.0 * math.pi
        self._event(f"on the circle: {(s.distance + foot) * 100:.0f} cm from the centre, counting laps")
        while True:
            dist = s.distance + foot                            # camera to the target's centre
            psi = self.obs.yaw + s.bearing
            progress = dirn * (psi - psi0)
            self.laps = progress / (2.0 * math.pi)
            if progress >= total - 0.4 * hop:
                self._event(f"orbit finished: {self.laps:.2f} laps, radius {dist * 100:.0f} cm")
                return
            # the hop: perpendicular to the line of sight, tilted inward or outward so that the new distance is the radius again
            sin_e = max(-0.6, min(0.6, (dist * dist + length * length - radius * radius) / (2.0 * dist * length)))
            eps = math.asin(sin_e)
            heading = psi - dirn * (math.pi / 2.0 - eps)
            yield from self._turn_to(heading)
            yield from self._straight(length, heading)
            facing = psi + dirn * math.atan2(length * math.cos(eps), dist - length * sin_e)   # where the target should be now
            yield from self._turn_to(facing)
            s = yield from self._reacquire(t, facing)
