"""The reality-gap test protocol: short, bounded motions on a real (or emulated) rover, and what to record from each.

Every motion is a firmware-timed move (`!MOVE`) or a firmware-timed direct drive (`!D ... ms`), so the board ends it by itself even if the
laptop dies. Before each motion `guard()` must return "" (the app passes its armed/checklist state) and `abort` is checked; on any problem
the rover is stopped and ProtocolAborted says why. The wheels may be on the floor (straight runs need that) but the rover needs about 1.5 m
of clear floor in front of it for the straight runs.

Tests (each repeated, because one run proves nothing):
    rest         yaw drift of the gyro while still (the noise floor)
    straight_f   forward, no heading-hold: the turn it makes is the left/right imbalance
    straight_b   backward
    spin_l/r     spin on the spot each way: total turn rate, and symmetry
    single_l/r   one wheel alone: which wheels move, in which direction, and how hard
    thresh_l/r   one wheel at rising duty: the duty at which it really starts (a humming motor starts late)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

THRESH_DUTIES = (50, 60, 70, 80, 90, 100, 115, 130, 150)


class ProtocolAborted(RuntimeError):
    pass


@dataclass
class Dataset:
    """Raw results: name -> list of {'yaw': degrees turned, 'secs': float, ...}. `meta` holds duty, cfg and the brownout count seen."""
    runs: dict[str, list[dict]] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def add(self, name: str, **row):
        self.runs.setdefault(name, []).append(row)

    def yaws(self, name: str) -> list[float]:
        return [float(r["yaw"]) for r in self.runs.get(name, []) if r.get("yaw") is not None]

    def to_dict(self) -> dict:
        return {"runs": self.runs, "meta": self.meta, "log": self.log}

    @classmethod
    def from_dict(cls, d: dict) -> "Dataset":
        return cls(runs={k: list(v) for k, v in d.get("runs", {}).items()}, meta=dict(d.get("meta", {})), log=list(d.get("log", [])))


class Runner:
    def __init__(self, rover, guard: Callable[[], str] | None = None, abort: threading.Event | None = None,
                 progress: Callable[[str, int, int], None] | None = None, settle_s: float = 0.35):
        self.rover, self.guard, self.abort, self.progress, self.settle_s = rover, guard, abort, progress, settle_s
        self.data = Dataset()

    # ------------------------------------------------------------------ helpers
    def _check(self):
        if self.abort is not None and self.abort.is_set():
            raise ProtocolAborted("stopped by you")
        if self.rover.error:
            raise ProtocolAborted(self.rover.error)
        why = self.guard() if self.guard else ""
        if why:
            raise ProtocolAborted(why)

    def _yaw(self) -> float:
        g = self.rover.ask("GYRO", "gyro", 2.0)
        if not g or "yaw" not in g or g.get("ok") != 1.0:
            raise ProtocolAborted("the gyro stopped answering: check its wiring (the test needs it)")
        return float(g["yaw"])

    def _settle(self):
        end = time.time() + self.settle_s
        while time.time() < end:
            self._check()
            time.sleep(0.05)

    def _brownouts(self) -> int:
        try:
            return int(float(self.rover.hello.get("brownouts", 0)))
        except (TypeError, ValueError):
            return 0

    def _note(self, text: str):
        self.data.log.append(text)

    def _tick(self, name: str, i: int, n: int):
        if self.progress:
            self.progress(name, i, n)

    # ------------------------------------------------------------------ one motion each
    def move(self, name: str, direction: str, ms: int, repeats: int):
        for i in range(repeats):
            self._check()
            self._tick(name, i, repeats)
            b0 = self._brownouts()
            done = self.rover.run_move(direction, ms, hold=False)
            if done is None:
                self._note(f"{name} run {i + 1}: the rover did not report the end of the move")
                self.data.add(name, yaw=None, secs=ms / 1000.0, ok=False)
            else:
                self.data.add(name, yaw=float(done["yaw"]), secs=float(done["ms"]) / 1000.0, ok=True, why=done.get("why"))
            if self._brownouts() > b0:
                self._note(f"{name} run {i + 1}: the board reset (brownout) as the motors started")
            self._settle()

    def single(self, name: str, left: int, right: int, ms: int, repeats: int):
        for i in range(repeats):
            self._check()
            self._tick(name, i, repeats)
            b0 = self._brownouts()
            y0 = self._yaw()
            self.rover.drive_duty(left, right, ms)
            time.sleep(ms / 1000.0 + 0.25)
            y1 = self._yaw()
            self.data.add(name, yaw=y1 - y0, secs=ms / 1000.0, ok=True, duty=(left, right))
            if self._brownouts() > b0:
                self._note(f"{name} run {i + 1}: the board reset (brownout) as the motor started")
            self._settle()

    def rest(self, secs: float = 2.0):
        self._check()
        y0 = self._yaw()
        time.sleep(secs)
        self.data.add("rest", yaw=self._yaw() - y0, secs=secs, ok=True)

    def threshold(self, side: str):
        """One wheel alone at rising duty; the firmware's minimum duty is lowered for the test and put back."""
        cfg = self.rover.ask("CFG", "cfg", 2.0) or {}
        old = int(float(cfg.get("minduty", 90)))
        self.rover.ask("CFG minduty=40", "cfg", 2.0)
        try:
            for k, duty in enumerate(THRESH_DUTIES):
                self._check()
                self._tick(f"thresh_{side}", k, len(THRESH_DUTIES))
                y0 = self._yaw()
                l, r = (duty, 0) if side == "l" else (0, duty)
                self.rover.drive_duty(l, r, 400)
                time.sleep(0.65)
                self.data.add(f"thresh_{side}", yaw=self._yaw() - y0, secs=0.4, duty=duty, ok=True)
                self._settle()
        finally:
            self.rover.ask(f"CFG minduty={old}", "cfg", 2.0)

    # ------------------------------------------------------------------ the whole protocol
    def run(self, repeats: int = 3, move_ms: int = 1500, spin_ms: int = 1000, single_duty: int = 150,
            include: tuple[str, ...] = ("rest", "straight", "spin", "single", "thresh")) -> Dataset:
        r = self.rover
        try:
            hello = r.ask("HELLO", "hello", 2.5) or {}
            if not hello:
                raise ProtocolAborted("the rover does not speak the v2 protocol: flash firmware v2 first")
            if float(hello.get("mpu", 0)) != 1.0:
                raise ProtocolAborted("the gyro is not connected: the measurement needs the MPU6050 (or use the ruler method)")
            r.ask("CFG holddef=0", "cfg", 2.0)
            cfg = r.ask("CFG", "cfg", 2.0) or {}
            self.data.meta.update(repeats=repeats, move_ms=move_ms, spin_ms=spin_ms, single_duty=single_duty, cfg=cfg, level=getattr(r, "level", 180),
                                  fw=hello.get("fw"), brownouts_start=self._brownouts(), reset_start=hello.get("reset"))
            if "rest" in include:
                self.rest()
            if "straight" in include:
                self.move("straight_f", "F", move_ms, repeats)
                self.move("straight_b", "B", move_ms, max(2, repeats - 1))
            if "spin" in include:
                self.move("spin_l", "L", spin_ms, max(2, repeats - 1))
                self.move("spin_r", "R", spin_ms, max(2, repeats - 1))
            if "single" in include:
                self.single("single_l", single_duty, 0, 600, 2)
                self.single("single_r", 0, single_duty, 600, 2)
            if "thresh" in include:
                self.threshold("l")
                self.threshold("r")
            self.data.meta["brownouts_end"] = self._brownouts()
        finally:
            try:
                r.stop()
            except Exception:
                pass
        return self.data


def run_protocol(rover, **kw) -> Dataset:
    run_kw = {k: kw.pop(k) for k in ("repeats", "move_ms", "spin_ms", "single_duty", "include") if k in kw}
    return Runner(rover, **kw).run(**run_kw)
