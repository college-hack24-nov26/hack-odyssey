"""What the viewer receives to draw a vehicle: a visual description from the real dimensions, which bodies are the vehicle, and moving parts that stream."""
import json

import numpy as np
import pytest

pytest.importorskip("mujoco")
from simlab import get_task_spec, get_vehicle  # noqa: E402
from simlab.envs import Sim  # noqa: E402


def scene_of(vehicle, task):
    sim = Sim(get_vehicle(vehicle), get_task_spec(task))
    sim.reset(seed=1)
    return sim, sim.backend.scene()


def test_the_boat_describes_itself_from_its_real_size_and_streams_its_propeller_and_rudder():
    sim, sc = scene_of("boat_workboat", "sandbox_water")
    v = sc["visual"]
    assert v["model"] == "rescue_boat" and (v["L"], v["W"], v["H"]) == (3.0, 1.2, 0.7) and 0 < v["draft"] < v["H"]
    assert sc["bodies"][sc["vehicle_body"]] == "vehicle" and {"prop", "rudder"} <= set(sc["bodies"])
    assert set(sc["vehicle_bodies"]) == {sc["bodies"].index(n) for n in ("vehicle", "prop", "rudder")}
    assert all("group" in g for g in sc["geoms"]) and json.dumps(sc)
    i, j = sc["bodies"].index("prop"), sc["bodies"].index("rudder")
    before = sim.backend.poses().copy()
    for _ in range(25):
        sim.step_physical([1.0, 1.0])
    after = sim.backend.poses()
    assert not np.allclose(before[i, 3:], after[i, 3:])                                      # the propeller turned
    yaw_boat = float(sim.obs.state.yaw)
    # the rudder blade's own yaw relative to the boat equals the helm angle
    qb, qr = after[sc["vehicle_body"], 3:], after[j, 3:]
    yaw = lambda q: np.arctan2(2 * (q[0] * q[3] + q[1] * q[2]), 1 - 2 * (q[2] ** 2 + q[3] ** 2))   # noqa: E731
    rel = (yaw(qr) - yaw(qb) + np.pi) % (2 * np.pi) - np.pi
    assert rel == pytest.approx(np.radians(get_vehicle("boat_workboat").params["rudder_max_deg"]), abs=0.02) and abs(yaw_boat) > 0.0   # the preset's rudder limit, at full helm


def test_physics_hull_and_picture_agree_on_where_the_boat_sits():
    sim, sc = scene_of("boat_small", "sandbox_water")
    z0 = float(sim.backend.poses()[sc["vehicle_body"], 2])
    v = sc["visual"]
    assert z0 == pytest.approx(v["H"] / 2 - v["draft"], abs=1e-3)                                 # the model's waterline (draft - H/2 in the body frame) is the water at z = 0


def test_rovers_and_drones_describe_their_wheels_rotors_and_cameras_by_body_name():
    for name, task, model in (("mid_4wd", "goto_empty", "rover"), ("tracked_crawler", "goto_empty", "rover"), ("quad_250", "hover_hold", "drone"), ("hex_450", "hover_hold", "drone")):
        sim, sc = scene_of(name, task)
        v = sc["visual"]
        assert v["model"] == model and json.dumps(v)
        moving = [w["body"] for w in v.get("wheels", [])] + [r["body"] for r in v.get("rotors", [])]
        assert moving and all(b in sc["bodies"] for b in moving), (name, moving)                        # every spinning part is a streamed body
        assert len(v["cameras"]) == 1 and set(v["cameras"][0]) == {"forward", "z", "tilt", "fov"}
    _, sc = scene_of("quad_250", "hover_hold")
    assert len(sc["visual"]["rotors"]) == 4 and {r["dir"] for r in sc["visual"]["rotors"]} == {1, -1}
    _, sc = scene_of("hex_450", "hover_hold")
    assert len(sc["visual"]["rotors"]) == 6 and sc["visual"]["prop_r"] > 0
    _, sc = scene_of("tracked_crawler", "goto_empty")
    assert sc["visual"]["drive"] == "tracked" and len(sc["visual"]["wheels"]) == 8


def test_pipe_defects_are_solid_and_walls_stay_see_through():
    sim, sc = scene_of("mid_4wd", "pipe_short")
    walls = [g for g in sc["geoms"] if g["name"].startswith(("pipe_", "chamber_")) and g["type"] == "mesh"]
    defects = [g for g in sc["geoms"] if g["name"].startswith("defect_")]
    assert walls and all(g["rgba"][3] <= 0.31 for g in walls)
    assert len(defects) == 4 and all(g["rgba"][3] == 1.0 for g in defects)
    assert len({tuple(g["rgba"][:3]) for g in defects}) == 2                                              # cracks and leaks look different


def test_only_water_worlds_get_the_sea_texture_so_other_scenes_and_their_detector_images_stay_as_they_were():
    import mujoco

    def textures(vehicle, task):
        m = Sim(get_vehicle(vehicle), get_task_spec(task)).backend.model
        return {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_TEXTURE, i) for i in range(m.ntex)}
    assert "sea" in textures("boat_workboat", "water_rescue")
    assert "sea" not in textures("mid_4wd", "goto_empty") and "grid" in textures("mid_4wd", "goto_empty")


def test_the_builder_preview_draws_the_real_model_as_a_ghost_with_the_parts_marked_inside_it():
    from simlab import VehicleSpec
    spec = get_vehicle("mid_4wd").to_dict()
    sim = Sim(VehicleSpec.from_dict({**spec, "kind": "ground_wheeled"}), get_task_spec("goto_empty"))
    assert sim.backend.scene()["vehicle_bodies"]
    from simlab.parts import Build, build_to_vehicle
    from simlab.parts.preview import preview
    from simlab.parts.templates import example_builds
    extra = {"small_boat": "thruster0", "esp32_pipe_rover": "motor_l0", "quad_5in_racer": "esc"}          # what each kind of machine adds on top of battery and controller
    for name, model in (("small_boat", "rescue_boat"), ("esp32_pipe_rover", None), ("quad_5in_racer", None)):
        b = Build.from_dict(example_builds()[name])
        built, _ = build_to_vehicle(b)
        sc = preview(b, built)["scene"]
        assert sc["visual"] and sc["visual"]["ghost"] is True and (model is None or sc["visual"]["model"] == model)
        names = {g["name"] for g in sc["geoms"] if g.get("part")}
        assert {"battery", "controller", extra[name]} <= names                             # marked, so the viewer keeps them while it hides the plain collision shapes
        assert all(g["body"] == sc["vehicle_body"] for g in sc["geoms"] if g.get("part"))
