"""A picture of a build for the Builder tab: the simulator's own vehicle model plus the parts inside it.

The chassis and wheels come from the MuJoCo backend's scene export (exactly what the Lab draws); the chassis is made
see-through and the battery, controller board, driver, motors, lamp and sensors are added at their mounts, with a view cone
for each camera. Nothing here runs a simulation: the vehicle is placed once and shown still.
"""
from __future__ import annotations

import math

from ..spec.vehicle import VehicleSpec
from ..vehicles.ground import track_width, wheel_width, wheel_xs
from .build import Build

CELL_DENSITY = 2500.0          # kg/m^3, to size the battery box from the cells' mass
QUAT_Y_AXIS = [0.70711, 0.70711, 0.0, 0.0]        # turns a cylinder's axis (local z) to the vehicle's y (across)
QUAT_X_AXIS = [0.70711, 0.0, 0.70711, 0.0]        # ... to the vehicle's x (along): a thruster pointing aft


def _box(body: int, name: str, size: list[float], pos: list[float], rgba: list[float]) -> dict:
    return {"body": body, "type": "box", "size": [round(s / 2, 5) for s in size], "pos": [round(p, 5) for p in pos], "quat": [1.0, 0.0, 0.0, 0.0],
            "rgba": rgba, "name": name}


def _cone(pos: list[float], tilt_deg: float, fov_deg: float, aspect: float, length: float) -> dict:
    """Pyramid with its apex at the lens: vertices and triangle indices in the vehicle frame."""
    t = math.radians(tilt_deg)
    fwd, up = (math.cos(t), 0.0, -math.sin(t)), (math.sin(t), 0.0, math.cos(t))      # as the MuJoCo camera mount (tilt > 0 looks down)
    hw = length * math.tan(math.radians(fov_deg) / 2.0)
    hh = hw / aspect
    corners = [[pos[0] + length * fwd[0] + s * hh * up[0], pos[1] + side * hw, pos[2] + length * fwd[2] + s * hh * up[2]]
               for side, s in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    verts = [pos, *corners]
    faces = [0, 1, 2, 0, 2, 3, 0, 3, 4, 0, 4, 1, 1, 3, 2, 1, 4, 3]
    return {"verts": [round(c, 5) for v in verts for c in v], "faces": faces}


def preview(build: Build, spec: VehicleSpec) -> dict:
    """{"scene": <Viewer3D scene JSON>, "poses": [...]} for the build's vehicle."""
    from ..backends.mujoco.backend import MujocoBackend          # imported here: MuJoCo is only needed for pictures and runs
    from ..tasks.base import World
    be = MujocoBackend(spec, World([], (4.0, 4.0)))
    be.reset((0.0, 0.0, 0.0, 0.0))
    scene, poses = be.scene(), be.poses().tolist()
    if scene.get("visual"):
        scene["visual"]["ghost"] = True                      # the viewer draws the real model of the machine see-through, so the parts inside show
    body, z0 = scene["vehicle_body"], be.plant.origin_height
    res = build.resolve()
    b = spec.body
    length, width, height = b["length"], b["width"], b["height"]
    for g in scene["geoms"]:
        if g.get("name") == be.plant.chassis_geom:
            g["rgba"] = [g["rgba"][0], g["rgba"][1], g["rgba"][2], 0.3]
    geoms, meshes = scene["geoms"], scene.setdefault("meshes", {})
    n_shell = len(geoms)                                     # everything added after this is a part inside the machine (the viewer must not hide it with the plain collision shapes)

    # battery: a box with the volume of the cells, low and at the rear
    pack = build.power["series"] * build.power["parallel"]
    vol = pack * res.battery.mass_kg / CELL_DENSITY
    hz = min(0.45 * height, max(0.008, vol ** (1 / 3)))
    hx = min(0.4 * length, math.sqrt(max(vol / hz, 1e-6) * 1.5))
    hy = min(0.9 * width, max(vol / (hz * hx), 0.01))
    geoms.append(_box(body, "battery", [hx, hy, hz], [-0.22 * length, 0.0, -height / 2 + hz / 2 + 0.003], [0.95, 0.75, 0.2, 1.0]))
    # controller board and driver on top of the chassis, a motor beside each wheel position
    geoms.append(_box(body, "controller", [min(0.05, 0.3 * length), min(0.026, 0.3 * width), 0.004], [0.2 * length, 0.0, height / 2 + 0.003], [0.15, 0.6, 0.3, 1.0]))
    if build.platform == "ground":
        geoms.append(_box(body, "driver", [min(0.02, 0.15 * length), min(0.02, 0.2 * width), 0.004], [-0.02 * length, 0.0, height / 2 + 0.003], [0.75, 0.2, 0.2, 1.0]))
        wr, drive = spec.params["wheel_radius"], spec.params["drive"]
        track, n = track_width(width, wr), build.drive["motor_count"]
        xs = wheel_xs(drive, length)
        for i in range(n // 2):
            x = xs[min(i, len(xs) - 1)]
            for side, tag in ((1, "l"), (-1, "r")):
                geoms.append({"body": body, "type": "cylinder", "size": [0.011, 0.0175], "pos": [round(x, 5), round(side * (track / 2 - wheel_width(wr) / 2 - 0.02), 5), -height / 2 + 0.014],
                              "quat": QUAT_Y_AXIS, "rgba": [0.55, 0.57, 0.6, 1.0], "name": f"motor_{tag}{i}"})
    elif build.platform == "water":
        # the thrusters sit at the stern, low, one per unit spread across the hull (the model's own outboard and propeller are drawn see-through around them)
        n = max(1, int(build.drive.get("count", 1)))
        r, half = max(0.015, 0.06 * width), max(0.025, 0.05 * length)
        span = 0.5 * width
        for i in range(n):
            y = 0.0 if n == 1 else -span / 2 + i * span / (n - 1)
            geoms.append({"body": body, "type": "cylinder", "size": [round(r, 5), round(half, 5)], "pos": [round(-0.5 * length + 0.12 * length, 5), round(y, 5), round(-height / 2 + 0.25 * height, 5)],
                          "quat": QUAT_X_AXIS, "rgba": [0.25, 0.55, 0.85, 1.0], "name": f"thruster{i}"})
    elif build.platform == "air":
        geoms.append(_box(body, "esc", [min(0.04, 0.3 * length), min(0.03, 0.3 * width), 0.006], [-0.15 * length, 0.0, height / 2 + 0.006], [0.75, 0.2, 0.2, 1.0]))
    if res.lamp:
        geoms.append({"body": body, "type": "sphere", "size": [0.008, 0.008, 0.008], "pos": [round(length / 2, 5), 0.0, round(0.0, 5)], "quat": [1.0, 0.0, 0.0, 0.0],
                      "rgba": [1.0, 0.95, 0.5, 1.0], "name": "lamp"})

    # sensors at their mounts
    cone_id = 1000
    for use, part in res.sensors:
        m = use.mount
        pos = [m["forward"], 0.0, m["height"] - z0]
        if part.kind == "sensor_camera":
            geoms.append(_box(body, f"sensor_{use.name}", [0.025, 0.03, 0.02], pos, [0.1, 0.1, 0.12, 1.0]))
            p = part.params
            meshes[str(cone_id)] = _cone(pos, m["tilt_deg"], p["fov_deg"], p["width_px"] / p["height_px"], max(0.25, 1.6 * length))
            geoms.append({"body": body, "type": "mesh", "mesh": cone_id, "size": [1.0, 1.0, 1.0], "pos": [0.0, 0.0, 0.0], "quat": [1.0, 0.0, 0.0, 0.0],
                          "rgba": [0.3, 0.8, 1.0, 0.22], "name": f"view_{use.name}"})
            cone_id += 1
        elif part.kind == "sensor_range":
            geoms.append(_box(body, f"sensor_{use.name}", [0.012, 0.045, 0.02], [pos[0], 0.0, pos[2]], [0.2, 0.45, 0.9, 1.0]))
        else:
            geoms.append(_box(body, f"sensor_{use.name}", [0.015, 0.015, 0.006], [pos[0] - 0.4 * length, 0.0, height / 2 + 0.005], [0.7, 0.4, 0.9, 1.0]))
    for g in geoms[n_shell:]:
        g["part"] = True
    scene["vehicle_size"] = max(length, width)
    return {"scene": scene, "poses": poses}
