"""Thin Webots adapter: wheels + camera + ground truth <-> the sewerbot brain (TCP, see core/protocol.py).

All intelligence (detection, navigation, dashboard) lives in the brain, so exactly the same code later drives the
real ESP32 rover. This controller only moves wheels, grabs frames and reports ground truth.

Test hook: set SEWERBOT_TEST=<path to json> to run a scripted drive without a brain, e.g.
  {"steps": [{"v": 0.3, "w": 0, "t": 6}], "out": "C:/tmp/run1", "save_every": 1.0}
"""
import json
import math
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from controller import Supervisor  # noqa: E402

from sewerbot.core.imaging import auto_expose  # noqa: E402
from sewerbot.core.profiles import RoverProfile, unicycle_to_sides, wheel_layout  # noqa: E402
from sewerbot.core import protocol  # noqa: E402

HOST = os.environ.get("SEWERBOT_HOST", protocol.HOST)
PORT = int(os.environ.get("SEWERBOT_PORT", protocol.PORT))
CMD_TIMEOUT = 0.6          # s without a command -> stop (same failsafe idea as the ESP32 firmware)
RECOG_MODELS = ("crack", "leak", "goal")


class Adapter:
    def __init__(self):
        self.robot = Supervisor()
        self.dt = int(self.robot.getBasicTimeStep())
        cfg = json.loads(self.robot.getCustomData() or "{}")
        self.cfg = cfg
        self.p = RoverProfile.from_dict(cfg["profile"])
        self.me = self.robot.getSelf()

        self.motors, self.sensors = {}, {}
        for name, _x, _side in wheel_layout(self.p):      # tracked rovers drive road wheels under a belt
            m = self.robot.getDevice(name)
            m.setPosition(float("inf"))
            m.setVelocity(0.0)
            self.motors[name] = m
            s = self.robot.getDevice(name + "_sensor")
            s.enable(self.dt)
            self.sensors[name] = s
        self.ds = {n: self.robot.getDevice(n) for n in ("ds_front", "ds_left", "ds_right")}
        for d in self.ds.values():
            d.enable(self.dt * 2)

        cam = self.p.camera
        self.cam = self.robot.getDevice("camera")
        self.cam_period = max(self.dt, int(round(1000.0 / cam.fps / self.dt)) * self.dt)
        self.cam.enable(self.cam_period)
        if self.cam.hasRecognition():
            self.cam.recognitionEnable(self.cam_period)
            if self.cam.hasRecognitionSegmentation():
                self.cam.enableRecognitionSegmentation()
        self.next_cam = 0.0

        self.instances = {}
        meta = cfg.get("meta")
        if meta and Path(meta).exists():
            self.instances = {int(k): v for k, v in json.loads(Path(meta).read_text())["instances"].items()}
        self.cmd_v = self.cmd_w = 0.0
        self.cur_v = self.cur_w = 0.0
        self.last_cmd = -1e9
        self.sock = None
        self.reader = protocol.Reader()
        self.last_try = -1e9
        self.connect_fails = 0
        self.prev_odo = None

        self.test = None
        tp = os.environ.get("SEWERBOT_TEST")
        if tp:
            self.test = json.loads(Path(tp).read_text())
            self.test_out = Path(self.test.get("out", ROOT / "runs" / "test"))
            self.test_out.mkdir(parents=True, exist_ok=True)
            self.next_save = 0.0
            self.log = open(self.test_out / "pose.csv", "w")
            self.log.write("t,x,y,z,yaw,v_cmd,w_cmd,front,left,right,odo_l,odo_r,pitch,wheel_z,wheel_y\n")
            self._dbg_wheel = self.me.getFromProtoDef("WHEEL_L0")
            self.saved = 0

    # ------------------------------------------------------------------ link
    def _connect(self, t):
        if self.sock or t - self.last_try < 1.0:
            return
        self.last_try = t
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.05)
            s.setblocking(False)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = s
            self.reader = protocol.Reader()
            self._send({"t": "hello", "proto": protocol.PROTOCOL_VERSION,
                        "caps": protocol.hello_caps(["unicycle"], ["camera", "ranges"], ["pose", "segmentation"]),
                        "profile": self.p.to_dict(), "map": self.cfg.get("map"),
                        "dt": self.dt, "start": self.cfg.get("start"), "yaw": self.cfg.get("yaw")})
            print(f"[sewerbot] connected to the brain at {HOST}:{PORT}", flush=True)
        except OSError as e:
            self.sock = None
            self.connect_fails += 1
            if self.connect_fails <= 3:                # the brain may simply not be listening yet; say so, but only a few times
                print(f"[sewerbot] cannot reach the brain at {HOST}:{PORT}: {e!r}", flush=True)

    def _send(self, header, payload=b""):
        if not self.sock:
            return
        try:
            data = protocol.pack(header, payload)
            self.sock.setblocking(True)
            self.sock.settimeout(0.5)
            self.sock.sendall(data)
            self.sock.setblocking(False)
        except OSError:
            self._drop()

    def _drop(self):
        try:
            if self.sock:
                self.sock.close()
        finally:
            self.sock = None

    def _wait_reply(self, t, timeout=0.6):
        """Lockstep: after an observation, wait (wall-clock) for the brain's command so the sim never runs ahead of it."""
        if not self.sock:
            return
        import select
        try:
            ready, _, _ = select.select([self.sock], [], [], timeout)
        except (OSError, ValueError):
            return
        if ready:
            self._poll(t)

    def _poll(self, t):
        if not self.sock:
            return
        try:
            data = self.sock.recv(65536)
            if not data:
                self._drop()
                return
            for h, _p in self.reader.feed(data):
                if h.get("t") == "cmd":
                    space, u = protocol.parse_cmd(h)
                    if space == "unicycle":
                        self.cmd_v, self.cmd_w, self.last_cmd = u.get("v", 0.0), u.get("w", 0.0), t
                elif h.get("t") == "quit":
                    self.robot.simulationQuit(0)
        except BlockingIOError:
            pass
        except (OSError, ValueError):
            self._drop()

    # ------------------------------------------------------------------ drive
    def _drive(self, t):
        if t - self.last_cmd > CMD_TIMEOUT:
            self.cmd_v = self.cmd_w = 0.0
        dt = self.dt / 1000.0
        dv = self.p.max_accel * dt
        dw = self.p.max_yaw_rate * 4.0 * dt
        self.cur_v += max(-dv, min(dv, self.cmd_v - self.cur_v))
        self.cur_w += max(-dw, min(dw, self.cmd_w - self.cur_w))
        left, right = unicycle_to_sides(self.p, self.cur_v, self.cur_w)
        r = self.p.wheel_radius
        for name, _x, side in wheel_layout(self.p):
            self.motors[name].setVelocity((left if side > 0 else right) / r)

    def _odometry(self):
        if not self.sensors:
            return None
        r = self.p.wheel_radius
        L = [s.getValue() for n, s in self.sensors.items() if "_l" in n]
        R = [s.getValue() for n, s in self.sensors.items() if "_r" in n]
        return [sum(L) / len(L) * r, sum(R) / len(R) * r]

    # ------------------------------------------------------------------ observations
    def _pose(self):
        x, y, z = self.me.getPosition()
        R = self.me.getOrientation()
        return {"x": x, "y": y, "z": z, "yaw": math.atan2(R[3], R[0]),
                "pitch": -math.asin(max(-1, min(1, R[6]))), "roll": math.atan2(R[7], R[8])}

    def _gt(self, bgr=None):
        """Pixel-exact ground truth. Every defect/goal has a unique segmentation colour (key = R + 256*G),
        so tight boxes come straight from the mask and occlusion is handled by the renderer.
        `lum` = mean brightness (0-255) of the defect's pixels in the exposed frame, so unseeable ones can be dropped."""
        out = []
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr is not None else None
        if not self.cam.hasRecognition() or not self.cam.isRecognitionSegmentationEnabled():
            return out
        seg = self.cam.getRecognitionSegmentationImage()
        if not seg:
            return out
        w, h = self.cam.getWidth(), self.cam.getHeight()
        a = np.frombuffer(seg, np.uint8).reshape(h, w, 4)
        key = a[:, :, 2].astype(np.int32) + 256 * a[:, :, 1].astype(np.int32)
        key[a[:, :, 0] != 0] = 0                      # blue must be 0 for a valid key
        dist = {}
        for o in self.cam.getRecognitionObjects():
            k = int(round(o.colors[0] * 255)) + 256 * int(round(o.colors[1] * 255))
            p = o.position
            dist[k] = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
        ks, counts = np.unique(key, return_counts=True)
        for k, n in zip(ks.tolist(), counts.tolist()):
            inst = self.instances.get(k)
            if k == 0 or inst is None or n < 12:
                continue
            ys, xs = np.nonzero(key == k)
            x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
            lum = float(gray[ys, xs].mean()) if gray is not None else -1.0
            out.append({"key": k, "id": inst["id"], "cls": inst["cls"], "box": [x0, y0, x1 - x0, y1 - y0],
                        "area": int(n), "dist": round(dist.get(k, -1.0), 3), "lum": round(lum, 1)})
        return out

    def _frame_bgr(self):
        w, h = self.cam.getWidth(), self.cam.getHeight()
        img = self.cam.getImage()
        if not img:
            return None
        bgr = np.ascontiguousarray(np.frombuffer(img, np.uint8).reshape(h, w, 4)[:, :, :3])
        return auto_expose(bgr) if self.p.camera.auto_exposure else bgr

    def _ranges(self):
        return {k.replace("ds_", ""): round(d.getValue(), 3) for k, d in self.ds.items()}

    def _observe(self, t):
        bgr = self._frame_bgr()
        if bgr is None:
            return
        pose = self._pose()
        if self.sock:
            ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                self._send({"t": "obs", "time": t, "pose": pose, "v": self.cur_v, "w": self.cur_w,
                            "odo": self._odometry(), "ranges": self._ranges(), "gt": self._gt(bgr),
                            "w_px": bgr.shape[1], "h_px": bgr.shape[0]}, jpg.tobytes())
        if self.test and t >= self.next_save:
            self.next_save = t + float(self.test.get("save_every", 1.0))
            cv2.imwrite(str(self.test_out / f"f{self.saved:04d}.jpg"), bgr)
            gt = self._gt(bgr)
            (self.test_out / f"f{self.saved:04d}.json").write_text(json.dumps({"pose": pose, "gt": gt}))
            self.saved += 1

    # ------------------------------------------------------------------ scripted test
    def _test_step(self, t):
        acc = 0.0
        for st in self.test["steps"]:
            if t < acc + st["t"]:
                self.cmd_v, self.cmd_w, self.last_cmd = st["v"], st["w"], t
                return False
            acc += st["t"]
        return True

    # ------------------------------------------------------------------ dataset generation
    def _dataset_run(self):
        """Teleport the rover to randomised viewpoints, randomise light/exposure, save frame + pixel-exact labels."""
        import random
        cfg = self.test["dataset"]
        rng = random.Random(cfg.get("seed", 0))
        meta = json.loads(Path(self.cfg["meta"]).read_text())
        nodes, edges = meta["map"]["nodes"], meta["map"]["edges"]
        ok_edges = [k for k, v in meta["fit"].items() if v["fits"]]
        if not ok_edges:
            print("dataset: rover fits no pipe on this map")
            return
        defects = [d for d in meta["defects"] if d["edge"] in ok_edges]
        lamp = self.me.getFromProtoDef("HEADLAMP")
        nominal = self.p.light_intensity
        self.cam.enable(self.dt)
        if self.cam.hasRecognition():
            self.cam.recognitionEnable(self.dt)
        wait = 6
        pos_f, rot_f = self.me.getField("translation"), self.me.getField("rotation")
        prefix = cfg.get("prefix", "s")
        for i in range(int(cfg["n"])):
            targeted = defects and rng.random() < cfg.get("p_defect", 0.7)
            sigma = rng.choice([1, -1])
            if targeted:
                d = rng.choice(defects)
                eid = d["edge"]
                e = edges[eid]
                s_r = d["s"] - sigma * rng.uniform(0.4, 3.5)
            else:
                eid = rng.choice(ok_edges)
                e = edges[eid]
                s_r = rng.uniform(e["s_min"] + 0.3, e["s_max"] - 0.3)
            s_r = min(max(s_r, e["s_min"] + 0.15), e["s_max"] - 0.15)
            ax, ay = nodes[e["a"]]["pos"]
            bx, by = nodes[e["b"]]["pos"]
            L = math.hypot(bx - ax, by - ay)
            dx, dy = (bx - ax) / L, (by - ay) / L
            lat_max = max(0.0, e["floor_width"] / 2 - self.p.width / 2 - 0.04)
            lat = rng.uniform(-lat_max, lat_max) * 0.8
            x, y = ax + dx * s_r + dy * lat, ay + dy * s_r - dx * lat
            yaw = math.atan2(sigma * dy, sigma * dx) + rng.gauss(0, 0.12)
            pos_f.setSFVec3f([x, y, 0.012])
            rot_f.setSFRotation([0, 0, 1, yaw])
            self.me.resetPhysics()
            factor = rng.uniform(0.6, 1.8)
            lamp.getField("intensity").setSFFloat(nominal * factor)
            exposure = rng.uniform(0.7, 1.4)
            self.cam.setExposure(exposure)
            for m in self.motors.values():
                m.setVelocity(0.0)
            for _ in range(wait):
                if self.robot.step(self.dt) == -1:
                    return
            bgr = self._frame_bgr()
            if bgr is None:
                continue
            gt = [g for g in self._gt(bgr) if g["cls"] in ("crack", "leak") and g["area"] >= 120 and g["lum"] >= 32]
            name = f"{prefix}{i:05d}"
            cv2.imwrite(str(self.test_out / f"{name}.jpg"), bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
            (self.test_out / f"{name}.json").write_text(json.dumps(
                {"gt": gt, "pose": self._pose(), "rover": self.p.name, "map": self.cfg.get("map"),
                 "lamp": round(factor, 3), "exposure": round(exposure, 3), "w": bgr.shape[1], "h": bgr.shape[0]}))
        self.robot.simulationQuit(0)

    def _park_viewpoint(self):
        """Debug aid (test mode only): put the Webots viewpoint beside the rover looking at it."""
        vp = None
        ch = self.robot.getRoot().getField("children")
        for i in range(ch.getCount()):
            n = ch.getMFNode(i)
            if n is not None and n.getTypeName() == "Viewpoint":
                vp = n
                break
        if vp is None:
            return
        p = self.me.getPosition()
        pos = [p[0] + 0.15, p[1] - 0.75, p[2] + 0.30]
        yaw, pitch = math.pi / 2, math.atan2(0.25, 0.75)
        wz, zz = math.cos(yaw / 2), math.sin(yaw / 2)
        wy, yy = math.cos(pitch / 2), math.sin(pitch / 2)
        # q = qz (x) qy
        w, x, y, z = wz * wy, -zz * yy, wz * yy, zz * wy
        n = math.sqrt(x * x + y * y + z * z)
        vp.getField("position").setSFVec3f(pos)
        vp.getField("orientation").setSFRotation([x / n, y / n, z / n, 2 * math.atan2(n, w)])

    def run(self):
        if self.test and "dataset" in self.test:
            self.robot.step(self.dt)
            self._dataset_run()
            return
        parked = False
        while self.robot.step(self.dt) != -1:
            if self.test and self.test.get("park_viewpoint") and not parked and self.robot.getTime() > 1.5:
                self._park_viewpoint()
                parked = True
            t = self.robot.getTime()
            if not self.test:
                self._connect(t)
                self._poll(t)
            else:
                done = self._test_step(t)
                if done:
                    self.log.close()
                    self.robot.simulationQuit(0)
                    break
            self._drive(t)
            if t >= self.next_cam:
                self.next_cam = t + self.cam_period / 1000.0
                self._observe(t)
                if not self.test:
                    self._wait_reply(t)
            if self.test:
                p = self._pose()
                rg = self._ranges()
                od = self._odometry() or [float("nan")] * 2
                self.log.write(f"{t:.3f},{p['x']:.4f},{p['y']:.4f},{p['z']:.4f},{p['yaw']:.4f},"
                               f"{self.cur_v:.3f},{self.cur_w:.3f},{rg['front']},{rg['left']},{rg['right']},"
                               f"{od[0]:.4f},{od[1]:.4f},{p['pitch']:.4f},"
                               f"{(self._dbg_wheel.getPosition()[2] if self._dbg_wheel else float('nan')):.4f},"
                               f"{(self._dbg_wheel.getPosition()[1] if self._dbg_wheel else float('nan')):.4f}\n")


if __name__ == "__main__":
    Adapter().run()
