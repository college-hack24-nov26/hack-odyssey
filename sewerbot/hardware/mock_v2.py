"""Firmware v2 emulator: the '!' protocol of firmware/esp32_rover_v2 with a simulated gyro and a rover whose motors can be weak.

Subclass of MockSerialPort (which stays the exact emulator of the older key-only firmware). What it models:
  * hello / gyro / calgyro / i2cscan / cfg / save / resetcfg / D / MOVE / stop, with the same reply lines as the real firmware
  * wheel speeds from duty x `side_gain` (left, right) x the stored trim; yaw rate from the wheel speed difference
  * a gyro with a constant bias plus noise, a mounting sign, and the real firmware's bounded heading-hold
  * faults to inject: side_gain (weak or dead motor), mpu_present / mpu_pins (not wired, wired to the wrong pins), gyro_mount_sign,
    gyro_bias, reset_reason="brownout", ultrasonic on/off
It is for development, tests and the clearly labelled "simulated rover" demo fallback; it says nothing about a real rover.
"""
from __future__ import annotations

import math
import random
import time

from .mock import MockSerialPort

MOTOR_PINS = {26, 27, 32, 33}
BAD_I2C_PINS = MOTOR_PINS | {6, 7, 8, 9, 10, 11}
DEFAULT_CFG = {"trimL": 1.0, "trimR": 1.0, "invL": 1, "invR": 1, "minduty": 90, "usguard": 0, "kp": 2.0, "kd": 0.3,
               "holdmax": 15, "gyrosign": 1, "holddef": 0}


class MockSerialPortV2(MockSerialPort):
    def __init__(self, side_gain=(1.0, 1.0), mpu_present: bool = True, mpu_pins=(21, 22), gyro_mount_sign: int = 1, gyro_bias: float = 0.3,
                 gyro_noise: float = 0.15, reset_reason: str = "poweron", brownouts: int = 0, track_m: float = 0.12, seed: int = 1,
                 wheel_sign=(1, 1), motor_min=(0, 0), sag_prob: float = 0.0, **kw):
        # faults: side_gain = (left, right) strength; wheel_sign = -1 for a motor wired backwards; motor_min = duty a motor needs before it
        # really turns (below that it only hums); sag_prob = chance that a move start drops the supply and resets the board (brownout)
        self.wheel_sign, self.motor_min, self.sag_prob = tuple(wheel_sign), tuple(motor_min), float(sag_prob)
        self.side_gain, self.mpu_present, self.mpu_pins = tuple(side_gain), mpu_present, tuple(mpu_pins)
        self.gyro_mount_sign, self.gyro_bias_true, self.gyro_noise = int(gyro_mount_sign), float(gyro_bias), float(gyro_noise)
        self.reset_reason, self.brownouts, self.track_m = reset_reason, int(brownouts), float(track_m)
        self.cfg, self.saved_cfg = dict(DEFAULT_CFG), dict(DEFAULT_CFG)
        self.rng = random.Random(seed)
        self.yaw_true = 0.0                       # degrees, counter-clockwise positive
        self.yaw_est, self.rate_est, self.bias_est, self.noise_est = 0.0, 0.0, 0.0, 0.0
        self.mode, self.base, self.mode_until = "idle", (0, 0), 0.0
        self.move: dict | None = None
        self.hold_on, self.hold_target, self.hold_corr = False, 0.0, 0.0
        self._line, self._in_line = "", False
        self.motor_ticks: list[tuple[float, int, int]] = []     # (time, left duty, right duty) as applied, to test ramps and the stagger
        self.cur = [0, 0]
        super().__init__(**kw)
        self.v2 = True
        if self.mpu_present and tuple(self.mpu_pins) == (21, 22):
            self._zero_gyro()
        self._hello()

    # ------------------------------------------------------------------ input
    def _key(self, c: str):
        lc = c.lower()
        if self._in_line:
            if c in "\r\n":
                self._in_line, line, self._line = False, self._line, ""
                self._command(line)
            else:
                self._line += c
            return
        if c == "!":
            self._in_line, self._line = True, ""
            return
        if lc in (" ", "x"):
            self._set_mode("idle", (0, 0))
            self._go("S", "stop")
            return
        if lc == "w":
            if self._blocked():
                self._set_mode("idle", (0, 0))
                self._go("S", "obstacle - refused")
                return
            self._set_mode("key", (self.level, self.level))
            self._go("F", "forward")
            self.hold_on = bool(self.cfg["holddef"]) and self._gyro_ok()
            self.hold_target, self.hold_corr = self.yaw_est, 0.0
        elif lc == "s":
            self._set_mode("key", (-self.level, -self.level))
            self._go("B", "backward")
        elif lc == "a":
            self._set_mode("key", (-self.level, self.level))
            self._go("L", "spin left")
        elif lc == "d":
            self._set_mode("key", (self.level, -self.level))
            self._go("R", "spin right")
        elif lc in "eq":
            super()._key(c)
            if self.mode == "key":
                self.base = tuple(int(math.copysign(self.level, v)) if v else 0 for v in self.base)
        elif lc == "?":
            self._tel()

    def _blocked(self) -> bool:
        return bool(self.cfg["usguard"] and self.ultrasonic and 0 <= self.obstacle_cm < self.STOP_CM)

    def _set_mode(self, mode: str, base, until: float = 0.0):
        self.mode, self.base, self.mode_until = mode, tuple(base), until
        if mode == "idle":
            self.hold_on, self.hold_corr, self.cur = False, 0.0, [0, 0]

    # ------------------------------------------------------------------ the '!' commands
    def _gyro_ok(self) -> bool:
        return self.mpu_present and tuple(self.mpu_pins) == (21, 22)

    def _zero_gyro(self) -> bool:
        if not self._gyro_ok():
            return False
        self.bias_est = self.gyro_bias_true + self.rng.gauss(0, 0.02)
        self.noise_est = abs(self.rng.gauss(self.gyro_noise, 0.01))
        self.yaw_est = 0.0
        return True

    def _hello(self):
        gyro = self._gyro_ok()
        self._say(f"hello fw=2.0.0-mock board=esp32 reset={self.reset_reason} brownouts={self.brownouts} mpu={int(gyro)} "
                  f"mpu_id={'0x68' if gyro else '0x00'} gyro_bias={self.bias_est:.2f} gyro_noise={self.noise_est:.2f} "
                  f"us={int(self.ultrasonic and self.obstacle_cm < 400)} wifi=0 ip=- port=8023 pins=26,27,32,33,21,22,18,35")
        self._cfg()

    def _cfg(self):
        c = self.cfg
        self._say(f"cfg trimL={c['trimL']:.3f} trimR={c['trimR']:.3f} invL={c['invL']} invR={c['invR']} minduty={c['minduty']} "
                  f"usguard={c['usguard']} kp={c['kp']:.2f} kd={c['kd']:.2f} holdmax={c['holdmax']} gyrosign={c['gyrosign']} holddef={c['holddef']}")

    def _command(self, line: str):
        parts = line.split()
        if not parts:
            return
        cmd, args = parts[0].upper(), parts[1:]
        if cmd == "HELLO":
            self._hello()
        elif cmd == "GYRO":
            ax, ay, az = (0.01, -0.02, 1.0) if self._gyro_ok() else (0.0, 0.0, 0.0)
            self._say(f"gyro ok={int(self._gyro_ok())} bias={self.bias_est:.2f} noise={self.noise_est:.2f} ax={ax:.2f} ay={ay:.2f} az={az:.2f} "
                      f"yaw={self.yaw_est:.1f} rate={self.rate_est:.1f} sign={self.cfg['gyrosign']} fails=0")
        elif cmd == "CALGYRO":
            if self.mode != "idle":
                self._say("err calgyro needs the rover stopped")
            elif self._zero_gyro():
                self._say(f"gyro zeroed bias={self.bias_est:.2f} noise={self.noise_est:.2f} ok=1")
            else:
                self._say(f"gyro zero failed noise={self.noise_est:.2f} (the rover must be still, and the gyro wired)")
        elif cmd == "I2CSCAN" and len(args) == 2:
            sda, scl = int(args[0]), int(args[1])
            if sda in BAD_I2C_PINS or scl in BAD_I2C_PINS or not (0 <= sda <= 33 and 0 <= scl <= 33):
                self._say(f"i2cscan refused sda={sda} scl={scl} (motor pin, input-only or flash pin)")
            else:
                found = "0x68" if self.mpu_present and (sda, scl) == tuple(self.mpu_pins) else "-"
                self._say(f"i2cscan sda={sda} scl={scl} found={found}")
        elif cmd == "CFG":
            for tok in args:
                k, _, v = tok.partition("=")
                if k not in self.cfg:
                    self._say(f"err unknown setting {k}")
                    continue
                f = float(v)
                if k in ("trimL", "trimR"):
                    f = min(1.3, max(0.7, f))
                elif k in ("invL", "invR", "gyrosign"):
                    f = -1 if f < 0 else 1
                elif k in ("usguard", "holddef"):
                    f = 1 if f else 0
                elif k == "minduty":
                    f = min(160, max(40, int(f)))
                elif k == "holdmax":
                    f = min(25, max(0, int(f)))
                self.cfg[k] = f
            self._cfg()
        elif cmd == "SAVE":
            self.saved_cfg = dict(self.cfg)
            self._say("saved")
        elif cmd == "RESETCFG":
            self.cfg = dict(DEFAULT_CFG)
            self._cfg()
            self._say("defaults in RAM; !SAVE to keep them")
        elif cmd == "STOP":
            self._set_mode("idle", (0, 0))
            self._go("S", "stop")
        elif cmd == "D" and len(args) >= 3:
            l, r, ms = int(args[0]), int(args[1]), int(args[2])
            if ms <= 0:
                self._set_mode("idle", (0, 0))
                self._go("S", "stop")
            elif l > 0 and r > 0 and self._blocked():
                self._say("obstacle - refused")
            elif self._brownout():
                pass
            else:
                self._set_mode("direct", (max(-255, min(255, l)), max(-255, min(255, r))), time.time() + min(max(ms, 20), 3000) / 1000.0)
                self.state = "A"
                self._say(f"d l={l} r={r} ms={ms}")
        elif cmd == "MOVE" and len(args) >= 2:
            d, ms = args[0].upper()[0], min(max(int(args[1]), 50), 5000)
            hold = len(args) > 2 and args[2][0] in "h1"
            v = self.level
            base = {"F": (v, v), "B": (-v, -v), "L": (-v, v), "R": (v, -v)}.get(d)
            if base is None:
                self._say("err move dir")
            elif d == "F" and self._blocked():
                self._say("obstacle - refused")
            elif self._brownout():
                pass
            else:
                self._set_mode("move", base, time.time() + ms / 1000.0)
                self.state = d
                self.hold_on = hold and d in "FB" and self._gyro_ok()
                self.hold_target, self.hold_corr = self.yaw_est, 0.0
                self.move = {"dir": d, "t0": time.time(), "ms": ms, "yaw0": self.yaw_est, "last": time.time(), "csum": 0.0, "cn": 0}
                self._say(f"mv start dir={d} ms={ms} hold={int(self.hold_on)} yaw0={self.yaw_est:.2f}")
        else:
            self._say(f"err unknown command {cmd}")

    # ------------------------------------------------------------------ physics
    def _tel(self):
        parts = [f"up={time.time() - self._t0:.1f}", f"speed={self.level}", f"dir={self.state}", f"hold={int(self.hold_on)}"]
        if self._gyro_ok():
            parts += [f"yaw={self.yaw_est:.1f}", f"rate={self.rate_est:.1f}"]
        if self.ultrasonic and self.obstacle_cm < 400:
            parts.append(f"dist={self.obstacle_cm:.1f}")
        self._say("tel " + " ".join(parts))

    def _wheel_speeds(self):
        l, r = self.base
        if self.hold_on and l == r and l != 0:
            l, r = l + self.hold_corr, r - self.hold_corr
        c = self.cfg
        duty = [l * c["trimL"] * c["invL"], r * c["trimR"] * c["invR"]]
        out = []
        for d, g, sgn, mn in zip(duty, self.side_gain, self.wheel_sign, self.motor_min):
            d = max(-255.0, min(255.0, d))
            if d != 0 and abs(d) < c["minduty"]:
                d = math.copysign(c["minduty"], d)               # the firmware lifts any non-zero duty to its minimum
            out.append(0.0 if abs(d) < mn else d * g * sgn * self.mps_per_level)       # below the motor's own threshold it only hums
        self.cur = [int(duty[0]), int(duty[1])]
        return out

    def _brownout(self) -> bool:
        """With probability sag_prob the supply dips as the motors start: the board resets, stops, and reports a brownout."""
        if not self.sag_prob or self.rng.random() >= self.sag_prob:
            return False
        self.brownouts += 1
        self.reset_reason = "brownout"
        self.cfg = dict(self.saved_cfg)                          # a reset forgets everything that was not saved
        self.move = None
        self._set_mode("idle", (0, 0))
        self._go("S", "stop")
        self._say("Rover ready. w/a/s/d to drive, space to stop, t for self-test, !HELLO for details.")
        self._hello()
        return True

    def _run(self):
        import queue
        last = last_tel = time.time()
        while not self._stop_evt.is_set():
            try:
                while True:
                    self._key(self._in.get_nowait())
                    self._last_byte = time.time()
            except queue.Empty:
                pass
            now = time.time()
            dt, last = min(now - last, 0.1), now
            if self.mode != "idle" and now - self._last_byte > self.WATCHDOG_S:
                if self.mode == "move":
                    self._end_move("link_lost")
                else:
                    self._set_mode("idle", (0, 0))
                    self._go("S", "link lost - stopped")
            if self.mode in ("direct", "move") and now >= self.mode_until:
                if self.mode == "move":
                    self._end_move("time")
                else:
                    self._set_mode("idle", (0, 0))
                    self._go("S", "stop")
            if self.mode != "idle" and self.base[0] > 0 and self.base[1] > 0 and self._blocked():
                self._set_mode("idle", (0, 0))
                self._go("S", "obstacle - stopped")
            vl, vr = self._wheel_speeds() if self.mode != "idle" else (0.0, 0.0)
            rate_true = math.degrees((vr - vl) / self.track_m)
            self.yaw_true += rate_true * dt
            self.odo_m += (vl + vr) / 2.0 * dt
            if self._gyro_ok():
                meas = self.gyro_mount_sign * rate_true + self.gyro_bias_true + self.rng.gauss(0, self.gyro_noise)
                self.rate_est = self.cfg["gyrosign"] * (meas - self.bias_est)
                self.yaw_est += self.rate_est * dt
                if self.hold_on:
                    lim = abs(self.base[0]) * self.cfg["holdmax"] / 100.0
                    self.hold_corr = max(-lim, min(lim, self.cfg["kp"] * (self.yaw_est - self.hold_target) + self.cfg["kd"] * self.rate_est))
                    if self.move:
                        self.move["csum"] += self.hold_corr
                        self.move["cn"] += 1
            elif self.hold_on:
                self.hold_on = False
                self._say("warn hold_off gyro_lost")
            if self.mode != "idle":
                self.motor_ticks.append((now - self._t0, self.cur[0], self.cur[1]))
                if self.move and now - self.move["last"] >= 0.1:
                    self.move["last"] = now
                    self._say(f"mv t={int((now - self.move['t0']) * 1000)} yaw={self.yaw_est - self.move['yaw0']:.2f} rate={self.rate_est:.2f} corr={self.hold_corr:.1f}")
            if now - last_tel > 0.5:
                last_tel = now
                self._tel()
            time.sleep(0.01)

    def _end_move(self, why: str):
        m = self.move or {"dir": "F", "t0": time.time(), "yaw0": self.yaw_est, "csum": 0.0, "cn": 0}
        avg = m["csum"] / m["cn"] if m["cn"] else 0.0
        self._say(f"mv done why={why} ms={int((time.time() - m['t0']) * 1000)} dir={m['dir']} yaw={self.yaw_est - m['yaw0']:.2f} corr_avg={avg:.2f}")
        self.move = None
        self._set_mode("idle", (0, 0))
        self._go("S", "stop")
