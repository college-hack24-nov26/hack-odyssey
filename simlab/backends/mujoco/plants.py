"""Plants: the vehicle-kind-specific part of the MuJoCo backend (its bodies, how commands become forces, its state).

A plant adds its bodies to the world XML, applies forces every physics substep, and reports a State. To support a new
vehicle kind in MuJoCo, subclass Plant and add it to PLANTS under the kind's name.
"""
from __future__ import annotations

import math

import numpy as np

from ...registry import Registry
from ...spec.vehicle import VehicleSpec
from ...vehicles.base import box_inertia
from ...vehicles.ground import GroundWheeled, track_width, wheel_width, wheel_xs
from ...vehicles.multirotor import Autopilot, allocation_matrix, prop_radius, rotors
from ..base import State
from .xml import X

G = 9.81
PLANTS: Registry = Registry("MuJoCo plant")
DECOR = 3   # geom group for visual-only parts: hidden from the onboard camera and ignored by range rays


def yaw_quat(yaw: float) -> np.ndarray:
    return np.array([math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)])


def quat_yaw(q) -> float:
    w, x, y, z = q
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def camera_xyaxes(tilt_deg: float) -> tuple:
    """MuJoCo camera frame looking along body +x, pitched down by tilt_deg (negative = up)."""
    t = math.radians(tilt_deg)
    return (0.0, -1.0, 0.0, math.sin(t), 0.0, math.cos(t))


class Plant:
    kind = ""
    body_name = "vehicle"
    chassis_geom = "chassis"
    contype, conaffinity = 1, 1        # collide with scene items; the ground plane is type 2
    ground_flags = (2, 2)              # contype, conaffinity of the arena floor (a vehicle that rolls on it needs (1, 1))

    def __init__(self, vehicle: VehicleSpec, dt: float):
        self.vehicle, self.dt = vehicle, dt

    def extend_root(self, root: X) -> None:
        """Hook: add the vehicle's own model sections (assets, actuators, sensors ...) to the <mujoco> element."""

    @property
    def origin_height(self) -> float:
        """Height of the body origin above the vehicle's lowest point (mount heights are measured from the bottom)."""
        raise NotImplementedError

    def xml(self, world: X) -> X:
        """Add the vehicle body to <worldbody>; returns it so the backend can mount sensors on it."""
        raise NotImplementedError

    def bind(self, model, data) -> None:
        self.model, self.data = model, data

    def reset(self, spawn, rng: np.random.Generator, randomize: dict) -> None:
        raise NotImplementedError

    def set_command(self, space: str, u: np.ndarray) -> None:
        raise NotImplementedError

    def teleport(self, x: float, y: float, z: float, yaw: float) -> None:
        """Place the vehicle at rest at an arbitrary pose (dataset capture, scripted scenes)."""
        raise NotImplementedError

    def set_hazards(self, hazards: dict) -> None:
        """Apply fixed stress conditions after reset(). Keys a plant does not know are ignored (see simlab/hazards)."""
        self.hazards = dict(hazards)

    def visual(self) -> dict | None:
        """A description of how the vehicle should LOOK, from its real dimensions: {model: name, ...}. The viewer builds a detailed model from it and hides
        the plain collision shapes. None keeps the plain shapes (imported robots, unknown kinds)."""
        return None

    def env(self) -> dict:
        """What the viewer needs besides poses to draw the surroundings truthfully (the boat: waves, wind, current). Sent with every state."""
        return {}

    def apply(self) -> None:
        """Called before every mj_step."""

    def state(self, t: float) -> State:
        raise NotImplementedError


class GroundPlant(Plant):
    """Planar rigid body (x, y, yaw joints) driven by acceleration-limited traction servos.

    The commanded left/right side speeds become a forward speed and a yaw rate (yaw scaled by the vehicle's measured
    yaw_gain: skid-steer slips). Sideways slip is damped by tyre friction. Wheels are visual only and spin with the
    ground speed. This reproduces navigation-level behaviour at any size or speed; wheel-level contact physics is what
    the Webots backend is for.
    """
    kind = "ground_wheeled"
    TAU_V, TAU_W, TAU_LAT = 0.12, 0.12, 0.03

    def __init__(self, vehicle, dt):
        super().__init__(vehicle, dt)
        p, b = vehicle.params, vehicle.body
        self.m0, self.l, self.w, self.h = b["mass"], b["length"], b["width"], b["height"]
        self.r, self.drive = p["wheel_radius"], p["drive"]
        self.clearance = 0.6 * self.r
        self.z0 = self.clearance + self.h / 2
        self.track = track_width(self.w, self.r)
        self.izz0 = box_inertia(self.m0, self.l, self.w, self.h)[2]
        self.vmax, self.amax, self.wmax, self.yaw_gain = p["max_speed"], p["max_accel"], p["max_yaw_rate"], p["yaw_gain"]
        self.kind_plugin = GroundWheeled()
        self.cmd = (0.0, 0.0)
        self.m, self.izz, self.mu, self.gain = self.m0, self.izz0, 0.8, 1.0
        self.extra_drag, self.side_gain, self.cmd_delay, self.drop_p = 0.0, (1.0, 1.0), 0.0, 0.0
        self.energy, self.drain, self._q, self._clock = 1.0, 0.0, [], 0.0
        self.rng = np.random.default_rng(0)
        self.wheels = [(f"wheel_{'l' if side > 0 else 'r'}{i}", x, side)
                       for i, x in enumerate(wheel_xs(self.drive, self.l)) for side in (+1, -1)]

    @property
    def origin_height(self) -> float:
        return self.z0

    def xml(self, world: X) -> X:
        b = world.add("body", name=self.body_name, pos=(0, 0, self.z0))
        for name, axis, typ in (("jx", (1, 0, 0), "slide"), ("jy", (0, 1, 0), "slide"), ("jyaw", (0, 0, 1), "hinge")):
            b.add("joint", name=name, type=typ, axis=axis, damping=0)
        b.add("inertial", pos=(0, 0, 0), mass=self.m0, diaginertia=box_inertia(self.m0, self.l, self.w, self.h))
        b.add("geom", name=self.chassis_geom, type="box", size=(self.l / 2, self.w / 2, self.h / 2), rgba=(0.93, 0.55, 0.14, 1),
              contype=self.contype, conaffinity=self.conaffinity, friction=(0.3, 0.005, 0.0001))
        b.add("geom", name="nose", type="box", size=(0.03 * self.l + 0.004, self.w * 0.22, 0.008), group=DECOR,
              pos=(self.l / 2 - 0.03 * self.l, 0, self.h / 2 + 0.006), rgba=(0.96, 0.96, 0.9, 1), contype=0, conaffinity=0)
        ww = wheel_width(self.r)
        if self.drive == "tracked":
            for side in (+1, -1):
                b.add("geom", type="box", group=DECOR, size=(self.l * 0.5 + self.r * 0.4, ww / 2, self.r),
                      pos=(0, side * self.track / 2, self.r - self.z0), rgba=(0.12, 0.12, 0.13, 1), contype=0, conaffinity=0)
        for name, x, side in self.wheels:
            wb = b.add("body", name=name, pos=(x, side * self.track / 2, self.r - self.z0))
            wb.add("joint", name=f"j_{name}", type="hinge", axis=(0, 1, 0), damping=0)
            wb.add("inertial", pos=(0, 0, 0), mass=1e-3, diaginertia=(1e-6, 1e-6, 1e-6))
            wb.add("geom", type="cylinder", size=(self.r, ww / 2), euler=(math.pi / 2, 0, 0), group=DECOR,
                   rgba=(0.08, 0.08, 0.09, 1), contype=0, conaffinity=0)
            wb.add("geom", type="box", size=(self.r * 0.92, ww / 2 * 1.04, self.r * 0.13), group=DECOR,   # a spoke, so spin is visible
                   rgba=(0.75, 0.77, 0.8, 1), contype=0, conaffinity=0)
        return b

    def visual(self):
        cams = [{"forward": s.options["forward"], "z": s.options["height"] - self.z0, "tilt": s.options["tilt_deg"], "fov": s.options["fov_deg"]}
                for s in self.vehicle.sensors if s.type == "camera"]
        return {"model": "rover", "L": self.l, "W": self.w, "H": self.h, "z0": self.z0, "wheel_r": self.r, "wheel_w": wheel_width(self.r), "track": self.track,
                "drive": self.drive, "wheels": [{"body": n, "x": x, "side": side} for n, x, side in self.wheels], "cameras": cams,
                "light": float(self.vehicle.params.get("light_intensity", 0.0))}

    def bind(self, model, data):
        super().bind(model, data)
        j = lambda n: model.joint(n)  # noqa: E731
        self.qa = [int(j(n).qposadr[0]) for n in ("jx", "jy", "jyaw")]
        self.da = [int(j(n).dofadr[0]) for n in ("jx", "jy", "jyaw")]
        self.wheel_dof = [(int(j(f"j_{n}").dofadr[0]), side) for n, _, side in self.wheels]
        self.body_id = model.body(self.body_name).id
        self.inertia0 = model.body_inertia[self.body_id].copy()

    def reset(self, spawn, rng, randomize):
        r = lambda k: 1.0 + (rng.uniform(-1, 1) * randomize.get(k, 0.0))  # noqa: E731
        self.m, self.izz = self.m0 * r("mass"), self.izz0 * r("mass")
        self.mu, self.gain = 0.8 * r("friction"), r("motor_strength")
        self.model.body_mass[self.body_id] = self.m
        self.model.body_inertia[self.body_id] = self.inertia0 * (self.m / self.m0)
        d = self.data
        d.qpos[self.qa[0]], d.qpos[self.qa[1]], d.qpos[self.qa[2]] = spawn[0], spawn[1], spawn[3]
        self.cmd = (0.0, 0.0)
        self.rng = rng
        self.extra_drag, self.side_gain, self.cmd_delay, self.drop_p = 0.0, (1.0, 1.0), 0.0, 0.0
        self.energy, self.drain, self._q, self._clock = 1.0, 0.0, [], 0.0

    def teleport(self, x, y, z, yaw):
        d = self.data
        d.qpos[self.qa[0]], d.qpos[self.qa[1]], d.qpos[self.qa[2]] = x, y, yaw
        d.qvel[:] = 0.0
        self.cmd = (0.0, 0.0)

    def set_hazards(self, h):
        """grip_mu: tyre grip coefficient (0.8 dry; sludge or ice < 0.1) · drag_ms2: pulling force used up by mud/debris, in m/s^2 (top speed falls by drag/max_accel) ·
        side_gain: (left, right) motor strength, (0, 1) = left motor dead · cmd_delay_s / cmd_dropout: slow or lost commands ·
        runtime_s: battery life at this load · energy_frac: charge left at the start (motors stop at empty)."""
        super().set_hazards(h)
        if "grip_mu" in h:
            self.mu = float(h["grip_mu"])
        self.extra_drag = float(h.get("drag_ms2", 0.0))
        self.side_gain = tuple(float(x) for x in h.get("side_gain", (1.0, 1.0)))
        self.cmd_delay, self.drop_p = float(h.get("cmd_delay_s", 0.0)), float(h.get("cmd_dropout", 0.0))
        rt = float(h.get("runtime_s", 0.0) or 0.0)
        self.drain = 1.0 / rt if rt > 0 else 0.0
        self.energy = float(h.get("energy_frac", 1.0))

    def set_command(self, space, u):
        if space == "unicycle":
            new = self.kind_plugin.mix(self.vehicle, float(u[0]), float(u[1]))
        elif space == "wheels":
            new = (float(u[0]), float(u[1]))
        else:
            raise KeyError(f"ground vehicles have no command space {space!r}")
        if self.drop_p and self.rng.random() < self.drop_p:
            return                                          # the command was lost: the old one stays in force
        if self.cmd_delay > 0:
            self._q.append((self._clock + self.cmd_delay, new))
        else:
            self.cmd = new

    def apply(self):
        d = self.data
        yaw = d.qpos[self.qa[2]]
        c, s = math.cos(yaw), math.sin(yaw)
        vxw, vyw, w = d.qvel[self.da[0]], d.qvel[self.da[1]], d.qvel[self.da[2]]
        vx, vy = c * vxw + s * vyw, -s * vxw + c * vyw
        self._clock += self.dt
        while self._q and self._q[0][0] <= self._clock:
            self.cmd = self._q.pop(0)[1]
        if self.drain:
            self.energy = max(0.0, self.energy - self.drain * self.dt)
        left, right = self.cmd
        if self.energy <= 0.0:
            left = right = 0.0                                  # flat battery: motors off
        left, right = left * self.side_gain[0], right * self.side_gain[1]
        vt, wt = 0.5 * (left + right), self.yaw_gain * (right - left) / self.track
        if self.extra_drag:                                     # linear motor curve: resistance eating a share of the pulling force cuts speed by that share
            k = max(0.0, 1.0 - self.extra_drag / self.amax)
            vt, wt = vt * k, wt * k
        grip = self.mu * self.m * G
        fx = self.m * max(-self.amax, min(self.amax, (vt - vx) / self.TAU_V)) * self.gain
        fx = max(-grip, min(grip, fx))
        al_max = 4.0 * self.wmax * self.gain
        torque = self.izz * max(-al_max, min(al_max, (wt - w) / self.TAU_W))
        fy = max(-grip, min(grip, -self.m * vy / self.TAU_LAT))
        q = d.qfrc_applied
        q[self.da[0]], q[self.da[1]], q[self.da[2]] = c * fx - s * fy, s * fx + c * fy, torque
        for dof, side in self.wheel_dof:                      # wheels roll at the speed of their side of the body
            d.qvel[dof] = (vx - side * w * self.track / 2) / self.r

    def state(self, t):
        d = self.data
        yaw = float(d.qpos[self.qa[2]])
        c, s = math.cos(yaw), math.sin(yaw)
        vxw, vyw, w = float(d.qvel[self.da[0]]), float(d.qvel[self.da[1]]), float(d.qvel[self.da[2]])
        vx, vy = c * vxw + s * vyw, -s * vxw + c * vyw
        return State(t, np.array([d.qpos[self.qa[0]], d.qpos[self.qa[1]], self.z0]), yaw_quat(yaw), yaw,
                     np.array([vxw, vyw, 0.0]), np.array([vx, vy, 0.0]), np.array([0.0, 0.0, w]),
                     extra={"left": vx - w * self.track / 2, "right": vx + w * self.track / 2, "soc": self.energy})


PLANTS.add(GroundPlant, name="ground_wheeled")


class MultirotorPlant(Plant):
    """Rigid body with a free joint. Each rotor's thrust follows its command through a first-order motor lag; the
    resulting wrench is applied at the centre of mass, with linear drag toward the wind. The vehicle kind's Autopilot
    turns commands in any space into per-rotor thrust requests, exactly as a flight controller would."""
    kind = "multirotor"
    contype, conaffinity = 1, 3                  # also collides with the ground (type 2): landing and crashing are physical
    VISUAL_SPIN = 60.0                           # rad/s of propeller spin at full thrust (slow enough to see)
    P_PER_KG_HOVER = 150.0                       # W per kg of vehicle at hover: sets the battery drain

    def __init__(self, vehicle, dt):
        super().__init__(vehicle, dt)
        p, b = vehicle.params, vehicle.body
        self.m0, self.l, self.w, self.h = b["mass"], b["length"], b["width"], b["height"]
        self.rot = rotors(vehicle)
        self.n = len(self.rot)
        self.A = allocation_matrix(vehicle)
        self.ap = Autopilot(vehicle, dt)
        self.tmax0, self.drag = p["thrust_max"], p["drag"]
        self.alpha = 1.0 - math.exp(-dt / p["motor_tau"])
        self.hover_t = self.m0 * G / self.n
        self.prop_r = prop_radius(vehicle)
        self.k_ang = 0.02 * box_inertia(self.m0, self.l, self.w, self.h)[0]      # small rotational damping (air + gyro)
        bat = vehicle.sensor("battery")
        self.battery = bat.options if bat else None
        if self.battery:
            cell_v = 1.2 if self.battery["chemistry"] == "nimh" else 3.7
            self.v_nom = cell_v * self.battery["cells"]
            self.e0 = self.battery["capacity_mah"] / 1000.0 * self.v_nom * 3600.0
        self.m, self.gain, self.tmax = self.m0, 1.0, self.tmax0
        self.space, self.u = vehicle.default_space, vehicle.space().zeros()
        self.T = np.zeros(self.n)
        self.wind = np.zeros(3)
        self.gust = np.zeros(3)
        self.wind_speed = 0.0
        self.rng = np.random.default_rng(0)
        self.energy, self.power, self.empty = 0.0, 0.0, False
        self.eff, self.quad_area, self._wind_ang = np.ones(self.n), 0.0, 0.0

    @property
    def origin_height(self) -> float:
        return self.h / 2

    def xml(self, world: X) -> X:
        b = world.add("body", name=self.body_name, pos=(0, 0, self.h / 2))
        b.add("freejoint", name="root")
        b.add("inertial", pos=(0, 0, 0), mass=self.m0, diaginertia=box_inertia(self.m0, self.l, self.w, self.h))
        b.add("geom", name=self.chassis_geom, type="box", size=(self.l / 2, self.w / 2, self.h / 2), rgba=(0.16, 0.17, 0.2, 1),
              contype=self.contype, conaffinity=self.conaffinity, friction=(0.6, 0.005, 0.0001))
        b.add("geom", name="nose", type="box", size=(0.1 * self.l, 0.2 * self.w, 0.006 + 0.05 * self.h), group=DECOR,
              pos=(self.l * 0.4, 0, self.h / 2 - 0.002), rgba=(0.95, 0.5, 0.1, 1), contype=0, conaffinity=0)
        zr = self.h / 2 + 0.012 + 0.04 * self.prop_r
        arm_r = max(0.002, 0.04 * self.prop_r)
        for i, (x, y, s) in enumerate(self.rot):
            b.add("geom", type="capsule", fromto=(0, 0, self.h / 2 - 0.004, x, y, zr - 0.01), size=arm_r, group=DECOR,
                  rgba=(0.28, 0.29, 0.33, 1), contype=0, conaffinity=0)
            rb = b.add("body", name=f"rotor{i}", pos=(x, y, zr))
            rb.add("joint", name=f"jr{i}", type="hinge", axis=(0, 0, 1), damping=0)
            rb.add("inertial", pos=(0, 0, 0), mass=1e-5, diaginertia=(1e-9, 1e-9, 1e-9))
            rb.add("geom", type="cylinder", size=(0.9 * arm_r + 0.004, 0.012), group=DECOR, rgba=(0.1, 0.1, 0.12, 1),
                   contype=0, conaffinity=0)
            col = (0.95, 0.35, 0.3, 0.9) if s < 0 else (0.35, 0.8, 0.95, 0.9)   # the two spin directions differ in colour
            rb.add("geom", type="box", size=(self.prop_r, 0.012 * self.prop_r + 0.0015, 0.0012), pos=(0, 0, 0.014), group=DECOR,
                   rgba=col, contype=0, conaffinity=0)
        return b

    def visual(self):
        cams = [{"forward": s.options["forward"], "z": s.options["height"] - self.h / 2, "tilt": s.options["tilt_deg"], "fov": s.options["fov_deg"]}
                for s in self.vehicle.sensors if s.type == "camera"]
        return {"model": "drone", "L": self.l, "W": self.w, "H": self.h, "prop_r": self.prop_r, "z_rotor": self.h / 2 + 0.012 + 0.04 * self.prop_r,
                "rotors": [{"body": f"rotor{i}", "x": x, "y": y, "dir": s} for i, (x, y, s) in enumerate(self.rot)], "cameras": cams,
                "mass": self.m0, "light": float(self.vehicle.params.get("light_intensity", 0.0))}

    def bind(self, model, data):
        super().bind(model, data)
        self.body_id = model.body(self.body_name).id
        self.rotor_dof = [int(model.joint(f"jr{i}").dofadr[0]) for i in range(self.n)]
        self.q0 = int(model.joint("root").qposadr[0])
        self.inertia0 = model.body_inertia[self.body_id].copy()

    def reset(self, spawn, rng, randomize):
        self.rng = rng
        r = lambda k: 1.0 + rng.uniform(-1, 1) * randomize.get(k, 0.0)  # noqa: E731
        self.m = self.m0 * r("mass")
        self.model.body_mass[self.body_id] = self.m
        self.model.body_inertia[self.body_id] = self.inertia0 * (self.m / self.m0)
        self.gain = r("motor_strength")
        self.tmax = self.tmax0 * self.gain
        d = self.data
        x, y, z, yaw = spawn
        d.qpos[self.q0:self.q0 + 3] = (x, y, max(z, self.h / 2))
        d.qpos[self.q0 + 3:self.q0 + 7] = yaw_quat(yaw)
        airborne = z > self.h / 2 + 0.05
        self.T = np.full(self.n, self.m0 * G / self.n if airborne else 0.0)
        self.ap.reset()
        ang, speed = rng.uniform(0, 2 * math.pi), rng.uniform(0, 1) * randomize.get("wind", 0.0)
        self.wind = np.array([speed * math.cos(ang), speed * math.sin(ang), 0.0])
        self.wind_speed, self.gust = speed, np.zeros(3)
        self._wind_ang, self.eff, self.quad_area = ang, np.ones(self.n), 0.0
        self.space, self.u = self.vehicle.default_space, self.vehicle.space().zeros()
        self.energy = self.e0 if self.battery else 0.0
        self.power = 0.0
        self.empty = False

    def set_hazards(self, h):
        """wind_ms: steady wind in a random (but seed-fixed) direction, with gusts and quadratic drag on the airframe ·
        rotor_eff: per-rotor strength, e.g. [1, 1, 0, 1] = rotor 2 dead · energy_frac: charge at the start · cold: capacity factor."""
        super().set_hazards(h)
        if "wind_ms" in h:
            s = float(h["wind_ms"])
            self.wind = np.array([s * math.cos(self._wind_ang), s * math.sin(self._wind_ang), 0.0])
            self.wind_speed = s
            self.quad_area = (self.l + 2 * self.prop_r) * (self.h + 0.04)         # side area the wind pushes on, m^2
        if "rotor_eff" in h:
            e = np.ones(self.n)
            vals = np.asarray(h["rotor_eff"], float).reshape(-1)[:self.n]
            e[:len(vals)] = np.clip(vals, 0.0, 1.0)
            self.eff = e
        if self.battery and ("energy_frac" in h or "cold" in h):
            self.energy = self.e0 * float(h.get("energy_frac", 1.0)) * float(h.get("cold", 1.0))

    def set_command(self, space, u):
        self.space, self.u = space, np.asarray(u, float)

    def teleport(self, x, y, z, yaw):
        d = self.data
        d.qpos[self.q0:self.q0 + 3] = (x, y, max(z, self.h / 2))
        d.qpos[self.q0 + 3:self.q0 + 7] = yaw_quat(yaw)
        d.qvel[:] = 0.0
        self.T = np.full(self.n, self.m * G / self.n if z > self.h / 2 + 0.05 else 0.0)
        self.ap.reset()

    def apply(self):
        d = self.data
        pos, q, vel, omega = d.qpos[self.q0:self.q0 + 3], d.qpos[self.q0 + 3:self.q0 + 7], d.qvel[0:3], d.qvel[3:6]
        cmd = self.ap.update(self.space, self.u, pos, vel, q, omega)
        if self.battery and self.energy <= 0.02 * self.e0:
            self.empty = True
        cmd = np.clip(cmd, 0.0, self.tmax) * self.eff * (0.0 if self.empty else 1.0)
        self.T += (cmd - self.T) * self.alpha
        wrench = self.A @ self.T
        R = d.xmat[self.body_id].reshape(3, 3)
        if self.wind_speed > 0:                                   # Ornstein-Uhlenbeck gusts around the steady wind
            self.gust += (-self.gust * self.dt / 2.0 + 0.25 * self.wind_speed * math.sqrt(self.dt) * self.rng.normal(0, 1, 3))
        air = self.wind + self.gust
        force = R @ np.array([0.0, 0.0, wrench[0]]) + self.drag * (air - vel)
        if self.quad_area:
            rel = air - vel
            force = force + 0.5 * 1.2 * 1.0 * self.quad_area * np.linalg.norm(rel) * rel     # Cd about 1 on a bluff airframe
        torque = R @ (wrench[1:] - self.k_ang * omega)
        d.xfrc_applied[self.body_id, :3] = force
        d.xfrc_applied[self.body_id, 3:] = torque
        spin = self.VISUAL_SPIN * np.sqrt(self.T / max(self.tmax0, 1e-9))
        for dof, (_, _, s), w in zip(self.rotor_dof, self.rot, spin):
            d.qvel[dof] = -s * w
        if self.battery:
            self.power = self.P_PER_KG_HOVER * self.m0 / (self.n * self.hover_t ** 1.5) * float(np.sum(self.T ** 1.5)) + 3.0
            self.energy = max(0.0, self.energy - self.power * self.dt)

    def state(self, t):
        d = self.data
        q = d.qpos[self.q0 + 3:self.q0 + 7].copy()
        R = d.xmat[self.body_id].reshape(3, 3)
        vel = d.qvel[0:3].copy()
        extra = {"thrust": self.T.copy(), "throttle": float(self.T.mean() / max(self.tmax0, 1e-9)), "wind": self.wind + self.gust}
        if self.battery:
            soc = self.energy / self.e0
            sag = 1.0 - 0.06 * min(1.0, self.power / (3.0 * (self.P_PER_KG_HOVER * self.m0 + 3.0)))     # load sag, up to 6 %
            extra.update(soc=soc, voltage=self.v_nom * (0.85 + 0.15 * soc) * sag, power_w=self.power, battery_empty=self.empty)
        return State(t, d.qpos[self.q0:self.q0 + 3].copy(), q, quat_yaw(q), vel, R.T @ vel, d.qvel[3:6].copy(), extra=extra)


PLANTS.add(MultirotorPlant, name="multirotor")
