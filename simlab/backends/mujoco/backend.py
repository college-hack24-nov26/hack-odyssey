"""MuJoCo backend: fast, headless, deterministic, one process per instance (so training can vectorise it)."""
from __future__ import annotations

import math
import threading
import uuid

import mujoco
import numpy as np

from ...spec.caps import Caps
from ...spec.vehicle import VehicleSpec
from ...tasks.base import World
from ..base import Backend, Observation
from .glthread import gl
from .plants import G, PLANTS, camera_xyaxes, yaw_quat
from .sensors import BatterySensor, ImuSensor, PoseSensor, RangeSensor
from .xml import X

GEOM_TYPES = {0: "plane", 2: "sphere", 3: "capsule", 4: "ellipsoid", 5: "cylinder", 6: "box", 7: "mesh"}
HIDDEN_GROUP = 4          # geom group the onboard camera does not draw (visual groups are 0-2) but contacts and range rays still see
RANDOMIZE_KEYS = ("mass", "motor_strength", "friction", "wind", "sensor_noise", "latency")


_ORPHANS: list = []       # GL objects found on a non-GL thread: kept alive on purpose rather than freed in the wrong context


class _GLRenderer(mujoco.Renderer):
    """A MuJoCo renderer that can only be freed on the GL thread. Normal clean-up is explicit (_close_all); this is
    the safety net for one that escapes it: the garbage collector may run on any thread, and freeing OpenGL objects
    from a thread that does not own the context crashes the process, so such an object is parked instead."""

    def __del__(self) -> None:
        if threading.current_thread() is gl().thread:
            super().__del__()
        else:
            _ORPHANS.append((self._mjr_context, self._gl_context))
            self._mjr_context = self._gl_context = None


def _close_all(renderers: dict) -> None:
    """Free renderers on the GL thread. Each must have its *own* GL context current while it frees its resources
    (Renderer.close() does not do that), and must never be left half-open: a renderer that is later finalised by the
    garbage collector on some other thread frees GL objects in the wrong context and crashes the process."""
    for r in list(renderers.values()):
        ctx = getattr(r, "_gl_context", None)
        try:
            if ctx:
                ctx.make_current()
            r.close()
        except Exception:  # noqa: BLE001 - best effort during shutdown
            pass
        finally:
            r._mjr_context = None
            r._gl_context = None
    renderers.clear()


class MujocoBackend(Backend):
    name = "mujoco"
    caps = Caps("mujoco", parallel=True, ground_truth=("pose", "segmentation"))

    def __init__(self, vehicle: VehicleSpec, world: World, *, policy_hz: float = 20.0, dt: float = 0.005, seed: int = 0):
        self.vehicle = vehicle.check(self.caps)
        self.world = world
        self.rng = np.random.default_rng(seed)
        self.substeps = max(1, round(1.0 / (policy_hz * dt)))
        self.dt = 1.0 / (policy_hz * self.substeps)
        self.policy_dt = self.dt * self.substeps
        self.plant = PLANTS.get(vehicle.kind)(vehicle, self.dt)
        self.model = mujoco.MjModel.from_xml_string(self._build_xml(), dict(world.assets))
        self.data = mujoco.MjData(self.model)
        self.plant.bind(self.model, self.data)
        self.cameras = {s.name: s.options for s in vehicle.sensors if s.type == "camera"}
        self._item_z = {it.name: it.pos[2] if it.rest_z is None else it.rest_z for it in world.items}
        self._renderers: dict = {}
        self._build_sensors()
        self._t = 0.0
        self._prev_vel = None
        self._rand = dict.fromkeys(RANDOMIZE_KEYS, 0.0)
        self._scene_id = uuid.uuid4().hex[:8]
        self._mesh_colour = {m.name: ((0.13, 0.08, 0.06, 1.0) if m.label == "crack" else (0.08, 0.2, 0.26, 1.0) if m.label == "leak" else (*m.rgba[:3], min(m.rgba[3], 0.3)))
                             for m in world.meshes}      # walls are see-through in the 3D viewer, so the vehicle inside shows; defect patches are solid
        self._bodies = sorted({int(b) for b in self.model.geom_bodyid})

    # ---------------------------------------------------------------- model
    def _build_xml(self) -> str:
        v, w = self.vehicle, self.world
        ax, ay = w.arena
        cams = [s.options for s in v.sensors if s.type == "camera"]
        root = X.root("mujoco", model="simlab")
        root.add("compiler", angle="radian", autolimits="true")
        root.add("option", timestep=self.dt, gravity=(0, 0, -G), integrator="implicitfast")
        # MuJoCo derives its camera clip planes from the model extent; pin it so far-away items cannot clip the floor
        root.add("statistic", extent=max(ax, ay), center=(0, 0, 0))
        vis = root.add("visual")
        vis.add("map", znear=0.002, zfar=60)
        vis.add("global", offwidth=max([c["width_px"] for c in cams] + [64]), offheight=max([c["height_px"] for c in cams] + [64]))
        pipe = w.theme == "pipe"                                   # a dark interior: only the vehicle's lamp and a faint fill light it
        if pipe:
            vis.add("headlight", ambient=(0.22, 0.22, 0.22), diffuse=(0.0, 0.0, 0.0), specular=(0.0, 0.0, 0.0))     # the fill stands in for bounce light
        else:
            vis.add("headlight", ambient=(0.42, 0.42, 0.42), diffuse=(0.55, 0.55, 0.55), specular=(0.05, 0.05, 0.05))
        asset = root.add("asset")
        if pipe:
            asset.add("texture", type="skybox", builtin="flat", rgb1=(0, 0, 0), rgb2=(0, 0, 0), width=8, height=8)
        elif w.theme == "water":                                   # open water: a pale horizon fading to deeper blue (no fog: it would hide the person the boat is looking for)
            asset.add("texture", type="skybox", builtin="gradient", rgb1=(0.74, 0.84, 0.94), rgb2=(0.34, 0.5, 0.7), width=256, height=256)
        else:
            asset.add("texture", type="skybox", builtin="gradient", rgb1=(0.62, 0.74, 0.92), rgb2=(0.16, 0.2, 0.3), width=256, height=256)
        asset.add("texture", name="grid", type="2d", builtin="checker", rgb1=(0.30, 0.34, 0.38), rgb2=(0.24, 0.28, 0.32),
                  width=512, height=512, mark="edge", markrgb=(0.45, 0.5, 0.55))
        if w.theme == "water":                                     # speckled, so the camera sees a surface it can tell is moving past, not one flat colour
            asset.add("texture", name="sea", type="2d", builtin="flat", rgb1=(0.08, 0.31, 0.48), width=512, height=512,
                      mark="random", markrgb=(0.62, 0.8, 0.92), random=0.06)
            asset.add("material", name="grid", texture="sea", texuniform="true", texrepeat=(0.25, 0.25), reflectance=0.0)      # (repeats per metre: one tile per 4 m)
        else:
            asset.add("material", name="grid", texture="grid", texuniform="true", texrepeat=(1, 1), reflectance=0.0)     # no mirror ghosts
        textured = {m.texture for m in w.meshes if m.texture}
        for key in sorted(textured):
            asset.add("texture", name="tex_" + key, type="2d", file=key)
            asset.add("material", name="mat_" + key, texture="tex_" + key, reflectance=0.0, specular=0.1, shininess=0.1)
        for m in w.meshes:
            verts, faces = np.asarray(m.verts, float).reshape(-1, 3), np.asarray(m.faces, int).reshape(-1, 3)
            uv = None if m.uv is None else np.asarray(m.uv, float).reshape(-1, 2)
            asset.add("mesh", name="mesh_" + m.name, vertex=np.round(verts, 5).ravel(), face=faces.ravel(),
                      texcoord=None if uv is None else np.round(uv, 5).ravel(), inertia="shell")
        self.plant.extend_root(root)
        wb = root.add("worldbody")
        if not pipe:
            wb.add("light", name="sun", pos=(0, 0, 25), dir=(0.15, 0.1, -1), directional="true", diffuse=(0.75, 0.75, 0.75),
                   specular=(0.1, 0.1, 0.1))
        gc, ga = self.plant.ground_flags
        wb.add("geom", name="ground", type="plane", size=(ax / 2 + 2, ay / 2 + 2, 0.1), material="grid", contype=gc,
               conaffinity=ga, friction=(w.ground_friction, 0.005, 0.0001), group=3 if pipe else None)    # a pipe's own floor is drawn instead
        for it in w.items:
            col = dict(contype=1 if it.collide else 0, conaffinity=1 if it.collide else 0, group=HIDDEN_GROUP if it.hidden else None)
            if it.movable:
                parent = wb.add("body", name=it.name, mocap="true", pos=it.pos, euler=(0, 0, it.yaw))
                parent.add("geom", name=it.name + "_geom", type=it.shape, size=it.size, rgba=it.rgba, **col)
            elif it.quat is not None:
                wb.add("geom", name=it.name, type=it.shape, size=it.size, pos=it.pos, quat=it.quat, rgba=it.rgba, **col)
            else:
                wb.add("geom", name=it.name, type=it.shape, size=it.size, pos=it.pos, euler=(0, 0, it.yaw), rgba=it.rgba, **col)
        for m in w.meshes:                                           # visual only: no contacts, so the collision boxes decide where the vehicle can go
            wb.add("geom", name=m.name, type="mesh", mesh="mesh_" + m.name, material=("mat_" + m.texture) if m.texture else None,
                   rgba=None if m.texture else m.rgba, contype=0, conaffinity=0)
        body = self.plant.xml(wb)
        if pipe and v.params.get("light_intensity", 0) > 0:          # the vehicle's own lamp, pointing where it drives
            body.add("light", name="headlamp", pos=(v.body["length"] / 2, 0, v.body["height"] / 2), dir=(1, 0, -0.02), cutoff=75, exponent=1.0,
                     diffuse=(min(1.0, 0.8 * v.params["light_intensity"]),) * 3, specular=(0, 0, 0), attenuation=(1, 0, 0.08), castshadow=False)
        for s in v.sensors:
            if s.type == "camera" and not s.options["mjcf_camera"]:       # (an imported model's own camera needs no mount)
                o = s.options
                fovy = math.degrees(2 * math.atan(math.tan(math.radians(o["fov_deg"]) / 2) * o["height_px"] / o["width_px"]))
                body.add("camera", name=s.name, pos=(o["forward"], 0, o["height"] - self.plant.origin_height),
                         xyaxes=camera_xyaxes(o["tilt_deg"]), fovy=fovy)
        return root.tostring()

    def _build_sensors(self):
        v = self.vehicle
        self.pose_s, self.imu_s, self.range_s, self.batt_s = {}, {}, {}, {}
        for s in v.sensors:
            if s.type == "battery":
                self.batt_s[s.name] = BatterySensor()
            if s.type == "pose":
                self.pose_s[s.name] = PoseSensor(s.options, self.rng)
            elif s.type == "imu":
                self.imu_s[s.name] = ImuSensor(s.options, self.rng)
            elif s.type == "rangefinder":
                self.range_s[s.name] = RangeSensor(s.options, self.rng, self.model, self.plant.body_id, v.body["length"] / 2)

    # ---------------------------------------------------------------- Backend API
    @property
    def time(self) -> float:
        return self._t

    def reset(self, spawn, placements=None, randomize=None, rng=None) -> Observation:
        if rng is not None:
            self.rng = rng
            for s in (*self.pose_s.values(), *self.imu_s.values(), *self.range_s.values()):
                s.rng = rng
        mujoco.mj_resetData(self.model, self.data)
        for name, p in (placements or {}).items():
            mid = int(self.model.body_mocapid[self.model.body(name).id])
            if mid < 0:
                raise KeyError(f"item {name!r} is not movable")
            self.data.mocap_pos[mid] = (p[0], p[1], p[3] if len(p) > 3 else self._item_z[name])    # (x, y, yaw[, z])
            self.data.mocap_quat[mid] = yaw_quat(p[2])
        self._rand = {**dict.fromkeys(RANDOMIZE_KEYS, 0.0), **(randomize or {})}
        self.plant.reset(spawn, self.rng, self._rand)
        self.plant.set_hazards(getattr(self, "hazards", None) or {})
        mujoco.mj_forward(self.model, self.data)
        self._t, self._prev_vel = 0.0, None
        for s in self.pose_s.values():
            s.reset()
        return self._observe(False, False)

    def step(self, command, space: str | None = None) -> Observation:
        space = space or self.vehicle.default_space
        self.plant.set_command(space, self.vehicle.space(space).vector(command))
        collided = grounded = False
        for _ in range(self.substeps):
            self.plant.apply()
            mujoco.mj_step(self.model, self.data)
            if self.data.ncon:
                hit, ground = self._contacts()
                collided, grounded = collided or hit, grounded or ground
        self._t += self.policy_dt
        return self._observe(collided, grounded)

    def _contacts(self) -> tuple[bool, bool]:
        """(touching something other than the ground, touching the ground) for the vehicle's body this step."""
        d, m = self.data, self.model
        hit = ground = False
        for i in range(d.ncon):
            g1, g2 = int(d.contact[i].geom1), int(d.contact[i].geom2)
            if self.plant.body_id not in (int(m.geom_bodyid[g1]), int(m.geom_bodyid[g2])):
                continue
            if m.geom_type[g1] == 0 or m.geom_type[g2] == 0:          # mjGEOM_PLANE
                ground = True
            else:
                hit = True
        return hit, ground

    def _observe(self, collided: bool, grounded: bool = False) -> Observation:
        st = self.plant.state(self._t)
        st.collided = collided
        st.extra["on_ground"] = grounded
        scale = 1.0 + self._rand["sensor_noise"]
        w, x, y, z = st.quat
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                      [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                      [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
        a_world = np.zeros(3) if self._prev_vel is None else (st.vel - self._prev_vel) / self.policy_dt
        self._prev_vel = st.vel.copy()
        accel_body = R.T @ (a_world + np.array([0.0, 0.0, G]))
        sensors = {n: s.read(st, scale) for n, s in self.pose_s.items()}
        sensors.update({n: s.read(st, accel_body, scale) for n, s in self.imu_s.items()})
        sensors.update({n: s.read(self.data, scale) for n, s in self.range_s.items()})
        sensors.update({n: s.read(st) for n, s in self.batt_s.items()})
        return Observation(st, sensors)

    # ---------------------------------------------------------------- scripted scenes and dataset capture
    def teleport(self, x: float, y: float, z: float, yaw: float) -> Observation:
        """Put the vehicle at rest at a pose, ignoring dynamics; returns what its sensors read there."""
        self.plant.teleport(x, y, z, yaw)
        mujoco.mj_forward(self.model, self.data)
        self._prev_vel = None
        return self._observe(False, False)

    def label_map(self) -> dict[int, str]:
        """MuJoCo geom id -> perception class, for every world item that carries a label (segmentation ground truth)."""
        out = {}
        for it in self.world.items:
            if it.label:
                gid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, it.name + "_geom" if it.movable else it.name)
                if gid >= 0:
                    out[gid] = it.label
        for m in self.world.meshes:
            if m.label:
                gid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, m.name)
                if gid >= 0:
                    out[gid] = m.label
        return out

    def _vehicle_bodies(self) -> set[int]:
        """The vehicle's body and every body attached below it (wheels, rotors)."""
        if not hasattr(self, "_veh_bodies"):
            m, root = self.model, self.plant.body_id
            self._veh_bodies = set()
            for b in range(m.nbody):
                a = b
                while a > 0 and a != root:
                    a = int(m.body_parentid[a])
                if a == root:
                    self._veh_bodies.add(b)
        return self._veh_bodies

    def randomize_appearance(self, rng: np.random.Generator, strength: float = 1.0) -> None:
        """Domain randomisation for perception: lighting, floor tint, colours of unlabelled objects, camera optics.
        Every call starts again from the nominal look, so repeated calls do not drift."""
        m, k = self.model, float(strength)
        if not hasattr(self, "_look0"):
            self._look0 = {"amb": m.vis.headlight.ambient.copy(), "dif": m.vis.headlight.diffuse.copy(), "light": m.light_diffuse.copy(),
                           "mat": m.mat_rgba.copy(), "geom": m.geom_rgba.copy(), "fovy": m.cam_fovy.copy(), "quat": m.cam_quat.copy()}
        a = self._look0
        gain = rng.uniform(1 - 0.5 * k, 1 + 0.35 * k)
        m.vis.headlight.ambient[:] = np.clip(a["amb"] * gain * rng.uniform(1 - 0.2 * k, 1 + 0.2 * k, 3), 0, 1)
        m.vis.headlight.diffuse[:] = np.clip(a["dif"] * gain * rng.uniform(1 - 0.2 * k, 1 + 0.2 * k, 3), 0, 1)
        m.light_diffuse[:] = np.clip(a["light"] * rng.uniform(1 - 0.6 * k, 1 + 0.3 * k) * rng.uniform(1 - 0.15 * k, 1 + 0.15 * k, 3), 0, 1)
        if self.world.theme == "pipe":      # one mild tint for every surface (a patch must keep matching its wall); sewers are not magenta
            m.mat_rgba[:, :3] = np.clip(a["mat"][:, :3] * rng.uniform(1 - 0.2 * k, 1 + 0.06 * k, 3), 0, 1)
        else:
            m.mat_rgba[:, :3] = np.clip(a["mat"][:, :3] * rng.uniform(1 - 0.35 * k, 1 + 0.15 * k, 3), 0, 1)
        labelled, veh = set(self.label_map()), self._vehicle_bodies()
        for g in range(m.ngeom):
            if g not in labelled and int(m.geom_bodyid[g]) not in veh and m.geom_type[g] != 0 and int(m.geom_matid[g]) < 0:
                m.geom_rgba[g, :3] = np.clip(a["geom"][g, :3] + rng.normal(0, 0.14 * k, 3), 0.02, 1)
        for cid in range(m.ncam):
            m.cam_fovy[cid] = a["fovy"][cid] * rng.uniform(1 - 0.08 * k, 1 + 0.08 * k)
            dq = np.zeros(4)
            mujoco.mju_axisAngle2Quat(dq, rng.normal(size=3), np.radians(rng.normal(0, 2.0 * k)))
            out = np.zeros(4)
            mujoco.mju_mulQuat(out, a["quat"][cid], dq)
            m.cam_quat[cid] = out

    # ---------------------------------------------------------------- visuals
    def render(self, camera: str, segmentation: bool = False) -> np.ndarray:
        return gl().call(self._render, camera, segmentation)

    def _renderer(self, w: int, h: int, segmentation: bool):
        """Segmentation images encode object ids as colours, so anti-aliasing would blend ids into wrong ones (it did,
        depending on how the GL driver resolves multisampling): they get their own renderer with multisampling off."""
        key = (w, h, segmentation)
        if key not in self._renderers:
            q = self.model.vis.quality
            saved = q.offsamples
            q.offsamples = 0 if segmentation else saved
            try:
                r = _GLRenderer(self.model, h, w)
            finally:
                q.offsamples = saved
            if segmentation:
                r.enable_segmentation_rendering()
            self._renderers[key] = r
        return self._renderers[key]

    def _render(self, camera: str, segmentation: bool) -> np.ndarray:
        o = self.cameras[camera]
        r = self._renderer(o["width_px"], o["height_px"], segmentation)
        r.update_scene(self.data, camera=o["mjcf_camera"] or camera)
        if segmentation:
            return r.render().copy()
        img = r.render().copy()
        if o["noise"] > 0:
            img = np.clip(img + self.rng.normal(0, o["noise"] * 255.0, img.shape), 0, 255).astype(np.uint8)
        if o["auto_exposure"] and self.world.theme == "pipe":         # a dark pipe lit by a lamp needs what a real sensor does on-chip
            from sewerbot.core.imaging import auto_expose             # (noise first: analogue gain amplifies it, exactly as on a sensor)
            img = auto_expose(img)
        return img

    def scene(self) -> dict:
        m = self.model
        index = {b: i for i, b in enumerate(self._bodies)}
        geoms, meshes = [], {}
        for g in range(m.ngeom):
            if m.geom_rgba[g][3] <= 0 or int(m.geom_type[g]) not in GEOM_TYPES:
                continue
            if self.world.theme == "pipe" and int(m.geom_type[g]) == 0:       # the hidden ground plane: the pipes have their own floor
                continue
            entry = {"body": index[int(m.geom_bodyid[g])], "type": GEOM_TYPES[int(m.geom_type[g])],
                     "size": [round(float(s), 5) for s in m.geom_size[g]], "pos": [round(float(p), 5) for p in m.geom_pos[g]],
                     "quat": [round(float(q), 6) for q in m.geom_quat[g]], "rgba": [round(float(c), 3) for c in m.geom_rgba[g]],
                     "name": m.geom(g).name, "group": int(m.geom_group[g])}
            if entry["name"] in self._mesh_colour:                        # textured meshes: the viewer cannot show the texture, so it gets a representative colour
                entry["rgba"] = list(self._mesh_colour[entry["name"]])
            if entry["type"] == "mesh":                              # each mesh is sent once, however many geoms share it
                mid = int(m.geom_dataid[g])
                if mid not in meshes:
                    va, vn, fa, fn = (int(m.mesh_vertadr[mid]), int(m.mesh_vertnum[mid]), int(m.mesh_faceadr[mid]), int(m.mesh_facenum[mid]))
                    meshes[mid] = {"verts": np.round(m.mesh_vert[va:va + vn], 4).ravel().tolist(), "faces": m.mesh_face[fa:fa + fn].ravel().tolist()}
                entry["mesh"] = mid
            geoms.append(entry)
        b = self.vehicle.body
        return {"id": self._scene_id, "arena": list(self.world.arena), "theme": self.world.theme, "geoms": geoms, "meshes": meshes,
                "vehicle_size": max(b["length"], b["width"]),
                "bodies": [m.body(b).name for b in self._bodies], "vehicle_body": index[self.plant.body_id],
                "vehicle_bodies": sorted(index[b] for b in self._vehicle_bodies() if b in index), "visual": self.plant.visual(),
                "cameras": list(self.cameras)}

    def poses(self) -> np.ndarray:
        b = self._bodies
        return np.concatenate([self.data.xpos[b], self.data.xquat[b]], axis=1).astype(np.float32)

    def close(self) -> None:
        if self._renderers:
            gl().call(_close_all, self._renderers)

    def __del__(self):
        r = getattr(self, "_renderers", None)
        if r:
            gl().post(_close_all, r)                  # never destroy a GL context from whichever thread the GC picked
