"""Plant for imported models: the user's MJCF/URDF bodies, joints and actuators are grafted into the arena as they are."""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from ...vehicles.custom import chosen_actuators
from ...vehicles.importer import inspect_model, to_mjcf
from ..base import State
from .plants import PLANTS, Plant, quat_yaw, yaw_quat
from .xml import X

SECTIONS = ("default", "asset", "actuator", "sensor", "contact", "equality", "tendon")


class CustomPlant(Plant):
    """The model keeps its own dynamics; this plant only places it, feeds its actuators and reads its base back.

    Its geoms use MuJoCo's default collision bits, so the arena floor is made collidable for it (ground_flags).
    """
    kind = "custom"
    ground_flags = (1, 1)

    def __init__(self, vehicle, dt):
        super().__init__(vehicle, dt)
        p = vehicle.params
        self.info = inspect_model(p["model"], p["base_body"])
        self.body_name = self.info["base_body"]
        self.acts = chosen_actuators(vehicle, self.info)
        self.file = to_mjcf(p["model"])
        self.src = ET.parse(self.file).getroot()
        self.ctrl = np.zeros(len(self.acts))
        self.gain = 1.0
        self.qadr: int | None = None

    @property
    def origin_height(self) -> float:
        return self.info["origin_height"]

    def extend_root(self, root: X) -> None:
        comp, here = self.src.find("compiler"), root.el.find("compiler")
        folder = self.file.parent
        if comp is not None:
            for k, v in comp.attrib.items():
                if k not in ("meshdir", "texturedir", "assetdir"):
                    here.set(k, v)
        for key in ("meshdir", "texturedir"):                      # relative asset paths are relative to the model file
            rel = comp.get(key, "") if comp is not None else ""
            here.set(key, str((folder / rel).resolve()))
        for tag in SECTIONS:
            for section in self.src.findall(tag):
                target = root.el.find(tag)
                if target is None:
                    target = ET.SubElement(root.el, tag)
                for child in section:
                    target.append(copy.deepcopy(child))

    def xml(self, world: X) -> X:
        for child in self.src.find("worldbody"):
            world.el.append(copy.deepcopy(child))
        for el in world.el.iter("body"):
            if el.get("name") == self.body_name:
                return X(el)
        raise ValueError(f"base body {self.body_name!r} not found in {self.file.name}")

    def bind(self, model, data):
        super().bind(model, data)
        self.body_id = model.body(self.body_name).id
        self.act_ids = [model.actuator(a["name"]).id for a in self.acts]
        for j in range(model.njnt):
            if int(model.jnt_bodyid[j]) == self.body_id and int(model.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE):
                self.qadr = int(model.jnt_qposadr[j])
        self.lo = np.array([a["lo"] for a in self.acts])
        self.hi = np.array([a["hi"] for a in self.acts])

    def _place(self, x: float, y: float, z: float, yaw: float) -> None:
        if self.qadr is None:
            return                                                  # a fixed base stays where the model put it
        d, a = self.data, self.qadr
        z0 = float(self.model.qpos0[a + 2])
        d.qpos[a:a + 3] = (x, y, max(z, z0))
        out = np.zeros(4)
        mujoco.mju_mulQuat(out, yaw_quat(yaw), self.model.qpos0[a + 3:a + 7])
        d.qpos[a + 3:a + 7] = out

    def reset(self, spawn, rng, randomize):
        self._place(*spawn)
        self.gain = 1.0 + rng.uniform(-1, 1) * randomize.get("motor_strength", 0.0)
        self.ctrl[:] = 0.0

    def teleport(self, x, y, z, yaw):
        self._place(x, y, z, yaw)
        self.data.qvel[:] = 0.0
        self.ctrl[:] = 0.0

    def set_command(self, space, u):
        if space != "raw":
            raise KeyError("imported models are driven in the 'raw' command space (one channel per actuator)")
        self.ctrl = np.asarray(u, float)

    def apply(self):
        if self.act_ids:
            self.data.ctrl[self.act_ids] = np.clip(self.ctrl * self.gain, self.lo, self.hi)

    def state(self, t):
        d, b = self.data, self.body_id
        R = d.xmat[b].reshape(3, 3)
        res = np.zeros(6)
        mujoco.mj_objectVelocity(self.model, d, mujoco.mjtObj.mjOBJ_BODY, b, res, 0)       # world frame: angular, then linear
        ang, lin = res[:3].copy(), res[3:].copy()
        q = d.xquat[b].copy()
        return State(t, d.xpos[b].copy(), q, quat_yaw(q), lin, R.T @ lin, R.T @ ang)


PLANTS.add(CustomPlant, name="custom")
