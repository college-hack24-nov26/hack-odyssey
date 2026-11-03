"""Example plug-in: a boat. Adds a vehicle kind and its MuJoCo plant without touching any simlab core module.

A plug-in is an ordinary module listed in config/plugins.txt (or SIMLAB_PLUGINS). On import it registers what it brings:
    KINDS.add(...)      the vehicle kind: parameter schema, command spaces, checks, derived facts
    PLANTS.add(...)     how that kind moves in the MuJoCo backend
    register_vehicle_dir(...)   preset vehicle files (here: boat_presets/*.yaml)

Physics, all in the water frame (z = 0 is the surface): the hull is a box. Buoyancy is sampled at a grid of points on
its bottom, so the boat floats at the right draft and rights itself when it rolls; water drag is quadratic per axis and
much larger sideways than forwards; a propeller at the stern gives thrust through a first-order lag and a rudder turns
it by pushing the stern sideways, using the propeller's slipstream as well as the boat's speed.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from simlab.backends.base import State
from simlab.backends.mujoco.plants import DECOR, G, PLANTS, Plant, quat_yaw, yaw_quat
from simlab.backends.mujoco.xml import X
from simlab.catalog import register_vehicle_dir
from simlab.registry import KINDS
from simlab.spec.caps import Caps
from simlab.spec.commands import Channel, CommandSpace
from simlab.spec.params import Issue, Param
from simlab.vehicles.base import Fact, VehicleKind, box_inertia

RHO = 1000.0                       # water density, kg/m^3


def draft_of(spec) -> float:
    b = spec.body
    return b["mass"] / (RHO * b["length"] * b["width"])


class Boat(VehicleKind):
    name = "boat"
    title = "Boat (plug-in)"
    description = "A displacement hull with a propeller and a rudder; floats on the water surface."
    schema = (
        Param("thrust_max", "float", 60.0, 1.0, 20000.0, "N", "Propeller thrust at full throttle"),
        Param("rudder_max_deg", "float", 35.0, 5.0, 60.0, "deg", "Largest rudder angle"),
        Param("drag_scale", "float", 1.0, 0.2, 5.0, "", "Multiplier on hull water drag (rougher hull > 1)"),
    )
    body_defaults = {"mass": 20.0, "length": 1.2, "width": 0.5, "height": 0.3}
    default_space = "helm"

    def spaces(self, spec):
        return {"helm": CommandSpace("helm", (Channel("throttle", -1.0, 1.0), Channel("rudder", -1.0, 1.0)),
                                     "Throttle (negative = reverse) and rudder (positive turns left).")}

    def validate(self, spec, caps: Caps | None = None):
        issues = super().validate(spec, caps)
        b = spec.body
        full = RHO * b["length"] * b["width"] * b["height"]
        if b["mass"] > 0.9 * full:
            issues.append(Issue("error", "body.mass", f"the hull would sink: it displaces {full:.0f} kg when submerged, the boat weighs {b['mass']:g} kg"))
        elif draft_of(spec) > 0.7 * b["height"]:
            issues.append(Issue("warn", "body.mass", f"very little freeboard: the hull sits {100 * draft_of(spec) / b['height']:.0f} % under water"))
        return issues

    def derived(self, spec):
        b, p = spec.body, spec.params
        d = draft_of(spec)
        area = b["width"] * max(d, 1e-3)
        top = math.sqrt(p["thrust_max"] / (0.5 * RHO * 0.3 * p["drag_scale"] * area))
        return [Fact("draft", "Draft (depth under water)", d, "m"), Fact("freeboard", "Freeboard", b["height"] - d, "m"),
                Fact("top_speed", "Estimated top speed", top, "m/s"), Fact("bollard", "Thrust / weight", p["thrust_max"] / (b["mass"] * G))]


class BoatPlant(Plant):
    kind = "boat"
    NX, NY = 5, 3                                  # buoyancy sample grid on the hull bottom
    NW = 5                                         # wave components
    WSHARE = np.array([0.1, 0.2, 0.4, 0.2, 0.1])    # share of the wave variance per component
    WPER = np.array([0.8, 0.9, 1.0, 1.15, 1.3])     # component periods relative to the peak period
    TAU_THRUST = 0.5

    def __init__(self, vehicle, dt):
        super().__init__(vehicle, dt)
        b, p = vehicle.body, vehicle.params
        self.m, self.L, self.W, self.H = b["mass"], b["length"], b["width"], b["height"]
        self.tmax, self.rmax, self.drag = p["thrust_max"], math.radians(p["rudder_max_deg"]), p["drag_scale"]
        self.draft0 = draft_of(vehicle)
        xs = (np.arange(self.NX) + 0.5) / self.NX * self.L - self.L / 2
        ys = (np.arange(self.NY) + 0.5) / self.NY * self.W - self.W / 2
        self.pts = np.array([(x, y, -self.H / 2) for x in xs for y in ys])
        self.dA = self.L * self.W / len(self.pts)
        k_heave = RHO * G * self.L * self.W
        self.c_heave = 1.0 * math.sqrt(k_heave * self.m)                   # about half of critical damping
        i = box_inertia(self.m, self.L, self.W, self.H)
        self.c_roll = 1.0 * math.sqrt(RHO * G * self.L * self.W ** 3 / 12 * i[0])
        self.c_pitch = 1.0 * math.sqrt(RHO * G * self.W * self.L ** 3 / 12 * i[1])
        self.alpha = 1.0 - math.exp(-dt / self.TAU_THRUST)
        self.thrust, self.cmd = 0.0, np.zeros(2)
        self.wave_hs, self.wave_tp, self.wind_ms, self.current_ms = 0.0, 0.0, 0.0, 0.0
        self.thr_eff, self.rudder_ok, self.energy, self.drain, self._clock = 1.0, True, 1.0, 0.0, 0.0
        self.dirs = np.zeros(3)                                       # wave, wind, current directions (rad), drawn at reset
        self.phase = np.zeros(self.NW)

    @property
    def origin_height(self) -> float:
        return self.H / 2

    def xml(self, world: X) -> X:
        b = world.add("body", name=self.body_name, pos=(0, 0, self.H / 2 - self.draft0))
        b.add("freejoint", name="root")
        b.add("inertial", pos=(0, 0, 0), mass=self.m, diaginertia=box_inertia(self.m, self.L, self.W, self.H))
        b.add("geom", name=self.chassis_geom, type="box", size=(self.L / 2, self.W / 2, self.H / 2), rgba=(0.92, 0.93, 0.95, 1),
              contype=1, conaffinity=1, friction=(0.2, 0.005, 0.0001))
        b.add("geom", type="box", size=(0.18 * self.L, 0.32 * self.W, 0.45 * self.H), pos=(-0.08 * self.L, 0, self.H / 2 + 0.45 * self.H),
              rgba=(0.2, 0.35, 0.65, 1), group=DECOR, contype=0, conaffinity=0)
        b.add("geom", type="box", size=(0.06 * self.L, 0.3 * self.W, 0.05 * self.H), pos=(self.L / 2 - 0.06 * self.L, 0, self.H / 2 + 0.05 * self.H),
              rgba=(0.95, 0.5, 0.1, 1), group=DECOR, contype=0, conaffinity=0)
        # a propeller and a rudder as real child bodies, so their turning streams like a wheel's (the plain shapes above are hidden when the viewer has a model)
        for name, joint, axis, pos in (("prop", "j_prop", (1, 0, 0), self.prop_pos()), ("rudder", "j_rudder", (0, 0, 1), self.rudder_pos())):
            cb = b.add("body", name=name, pos=pos)
            cb.add("joint", name=joint, type="hinge", axis=axis, damping=0)
            cb.add("inertial", pos=(0, 0, 0), mass=1e-5, diaginertia=(1e-9, 1e-9, 1e-9))
            cb.add("geom", type="sphere", size=(0.01,), group=DECOR, rgba=(0.5, 0.5, 0.5, 0.02), contype=0, conaffinity=0)
        return b

    def prop_pos(self):
        return (-self.L / 2 - 0.05 * self.L, 0.0, -self.H / 2 + 0.04 * self.H)

    def rudder_pos(self):
        p = self.prop_pos()
        return (p[0] - 0.035 * self.L, 0.0, p[2] + 0.02 * self.H)

    def visual(self):
        return {"model": "rescue_boat", "L": self.L, "W": self.W, "H": self.H, "draft": self.draft0, "prop": "prop", "rudder": "rudder",
                "prop_pos": list(self.prop_pos()), "rudder_pos": list(self.rudder_pos()), "rudder_max": self.rmax}

    def bind(self, model, data):
        super().bind(model, data)
        self.body_id = model.body(self.body_name).id
        self.q0 = int(model.joint("root").qposadr[0])
        self.prop_dof = int(model.joint("j_prop").dofadr[0])
        self.rud_q = int(model.joint("j_rudder").qposadr[0])

    def reset(self, spawn, rng, randomize):
        self.teleport(spawn[0], spawn[1], 0.0, spawn[3])
        self.dirs = rng.uniform(0, 2 * math.pi, 3)                    # always drawn, so a seed gives the same scene at every severity
        self.phase = rng.uniform(0, 2 * math.pi, self.NW)
        self.wave_hs = self.wave_tp = self.wind_ms = self.current_ms = 0.0
        self.thr_eff, self.rudder_ok, self.energy, self.drain, self._clock = 1.0, True, 1.0, 0.0, 0.0

    def set_hazards(self, h):
        """wave_hs: significant wave height (m) · wind_ms / current_ms: speeds, directions fixed by the seed ·
        thruster_eff: 0..1 (0 = thruster dead) · rudder_stuck · runtime_s / energy_frac: battery. The waves act through
        buoyancy only (no diffraction, slamming or horizontal wave force): indicative, not a seakeeping prediction."""
        super().set_hazards(h)
        self.wave_hs = float(h.get("wave_hs", 0.0))
        self.wave_tp = float(h.get("wave_tp", 3.9 * math.sqrt(max(self.wave_hs, 1e-6))))    # fully developed sea
        self.wind_ms, self.current_ms = float(h.get("wind_ms", 0.0)), float(h.get("current_ms", 0.0))
        self.thr_eff, self.rudder_ok = float(h.get("thruster_eff", 1.0)), not h.get("rudder_stuck", False)
        rt = float(h.get("runtime_s", 0.0) or 0.0)
        self.drain, self.energy = (1.0 / rt if rt > 0 else 0.0), float(h.get("energy_frac", 1.0))

    def env(self):
        """Exactly what surface() needs, so the viewer's water shader can draw the same waves the physics uses."""
        return {"wave": {"hs": round(self.wave_hs, 4), "tp": round(self.wave_tp, 4), "dir": round(float(self.dirs[0]), 5), "t": round(self._clock, 4),
                         "phase": [round(float(x), 5) for x in self.phase], "share": [float(x) for x in self.WSHARE], "per": [float(x) for x in self.WPER]},
                "wind": {"ms": self.wind_ms, "dir": round(float(self.dirs[1]), 5)}, "current": {"ms": self.current_ms, "dir": round(float(self.dirs[2]), 5)}}

    def surface(self, x, y):
        """Water surface height at points (x, y) now: a few linear deep-water waves with a Pierson-Moskowitz-like spread."""
        if self.wave_hs <= 0:
            return np.zeros(np.shape(x))
        sigma2 = (self.wave_hs / 4.0) ** 2
        tp = self.wave_tp * self.WPER
        om = 2 * math.pi / tp
        k = om ** 2 / G
        a = np.sqrt(2 * sigma2 * self.WSHARE)
        ux, uy = math.cos(self.dirs[0]), math.sin(self.dirs[0])
        proj = np.asarray(x)[..., None] * ux + np.asarray(y)[..., None] * uy
        return (a * np.cos(k * proj - om * self._clock + self.phase)).sum(-1)

    def teleport(self, x, y, z, yaw):
        d = self.data
        d.qpos[self.q0:self.q0 + 3] = (x, y, self.H / 2 - self.draft0)
        d.qpos[self.q0 + 3:self.q0 + 7] = yaw_quat(yaw)
        d.qvel[:] = 0.0
        self.thrust, self.cmd = 0.0, np.zeros(2)

    def set_command(self, space, u):
        if space != "helm":
            raise KeyError("boats are driven in the 'helm' command space")
        self.cmd = np.asarray(u, float)

    def apply(self):
        d = self.data
        self._clock += self.dt
        if self.drain:
            self.energy = max(0.0, self.energy - self.drain * self.dt)
        R = d.xmat[self.body_id].reshape(3, 3)
        com = d.xpos[self.body_id]
        v, w_b = d.qvel[0:3], d.qvel[3:6]
        pts_w = com + self.pts @ R.T
        depth = np.clip(self.surface(pts_w[:, 0], pts_w[:, 1]) - pts_w[:, 2], 0.0, self.H)
        f = RHO * G * self.dA * depth                                       # buoyant force at each sample point (N, upward)
        force = np.array([0.0, 0.0, f.sum()])
        r = pts_w - com
        torque = np.array([(r[:, 1] * f).sum(), (-r[:, 0] * f).sum(), 0.0])
        wet = float(depth.mean())
        s = min(1.0, wet / max(self.draft0, 1e-6))
        cur = self.current_ms * np.array([math.cos(self.dirs[2]), math.sin(self.dirs[2]), 0.0])
        vb = R.T @ (v - cur)                                                # the water moves, the hull is dragged along
        live = self.thr_eff if self.energy > 0.0 else 0.0
        self.thrust += (self.cmd[0] * self.tmax * live - self.thrust) * self.alpha
        drag = np.array([-0.5 * RHO * 0.3 * self.drag * self.W * wet * abs(vb[0]) * vb[0],
                         -0.5 * RHO * 1.1 * self.drag * self.L * wet * abs(vb[1]) * vb[1],
                         -self.c_heave * vb[2] * s])
        stern = np.array([-self.L / 2, 0.0, -self.H / 4])
        slip = math.sqrt(abs(self.thrust) / (RHO * math.pi * (0.15 * self.W) ** 2 * 2.0))     # propeller slipstream speed
        flow2 = vb[0] * abs(vb[0]) + math.copysign(slip * slip, self.thrust) * 0.5
        rudder_force = -0.5 * RHO * 0.6 * (0.12 * wet * self.L) * flow2 * math.sin((self.cmd[1] if self.rudder_ok else 0.0) * self.rmax) * s
        f_body = np.array([self.thrust, rudder_force, 0.0]) + drag
        if self.wind_ms > 0:                                                # windage on the part of the hull above the water
            wv = self.wind_ms * np.array([math.cos(self.dirs[1]), math.sin(self.dirs[1]), 0.0])
            rel = R.T @ (wv - v)
            area = self.L * max(self.H - self.draft0, 0.05) * 1.3                  # hull side plus a little superstructure
            fw = 0.5 * 1.2 * 1.0 * area * np.linalg.norm(rel) * rel
            wind_f = np.array([fw[0], fw[1], 0.0])
            f_body = f_body + wind_f
        else:
            wind_f = np.zeros(3)
        torque_b = np.cross(stern, np.array([self.thrust, rudder_force, 0.0]))
        torque_b += np.cross(np.array([0.0, 0.0, max(0.1 * self.H, self.draft0 / 2)]), wind_f)       # windage heels the hull a little
        torque_b += np.array([-self.c_roll * w_b[0], -self.c_pitch * w_b[1], 0.0]) * s
        torque_b[2] += -(0.5 * RHO * 0.04 * self.drag * wet * self.L ** 3 * self.W * 0.5) * abs(w_b[2]) * w_b[2] - 0.15 * self.m * self.L * w_b[2] * s
        d.xfrc_applied[self.body_id, :3] = force + R @ f_body
        d.xfrc_applied[self.body_id, 3:] = torque + R @ torque_b
        d.qvel[self.prop_dof] = -55.0 * float(np.clip(self.thrust / max(self.tmax, 1e-9), -1.0, 1.0))      # the propeller turns with the thrust
        d.qpos[self.rud_q] = (self.cmd[1] if self.rudder_ok else 0.0) * self.rmax                          # the rudder blade follows the helm

    def state(self, t):
        d = self.data
        R = d.xmat[self.body_id].reshape(3, 3)
        q = d.qpos[self.q0 + 3:self.q0 + 7].copy()
        vel = d.qvel[0:3].copy()
        pw = d.xpos[self.body_id] + self.pts @ R.T
        raw = self.surface(pw[:, 0], pw[:, 1]) - pw[:, 2]
        depth = np.clip(raw, 0.0, self.H)
        deck_wet = float(max(0.0, raw.max() - self.H))                 # how far the water stands over the deck (m): swamping
        roll = math.degrees(math.atan2(R[2, 1], R[2, 2]))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, -R[2, 0]))))
        return State(t, d.qpos[self.q0:self.q0 + 3].copy(), q, quat_yaw(q), vel, R.T @ vel, d.qvel[3:6].copy(),
                     extra={"draft": float(depth.mean()), "thrust": self.thrust, "soc": self.energy,
                            "roll_deg": roll, "pitch_deg": pitch, "deck_wet": deck_wet})


KINDS.add(Boat())
PLANTS.add(BoatPlant, name="boat")
register_vehicle_dir(Path(__file__).parent / "boat_presets")
