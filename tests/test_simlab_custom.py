"""Custom machines: imported MJCF/URDF models, the sandbox task, the plug-in system and the boat plug-in."""
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("mujoco")

from simlab import TaskSpec, VehicleSpec, get_task_spec, get_vehicle, save_vehicle, vehicle_names  # noqa: E402
from simlab.catalog import broken_vehicles  # noqa: E402
from simlab.envs import Sim  # noqa: E402
from simlab.registry import PLUGIN_ERRORS, load_plugins  # noqa: E402
from simlab.spec import Param, SpecError  # noqa: E402
from simlab.vehicles import importer  # noqa: E402
from simlab.vehicles.importer import draft_spec, inspect_model  # noqa: E402
from simlab.vehicles.multirotor import euler  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
CART = str(FIX / "cart.xml")


def cart_spec(**kw) -> VehicleSpec:
    d = draft_spec(CART)
    d["sensors"] = [{"type": "camera", "name": "front", "mjcf_camera": "front_cam", "width_px": 320, "height_px": 240}]
    for k, v in kw.items():
        d["params"][k] = v
    return VehicleSpec.from_dict(d)


# ---------------------------------------------------------------- reading a model file
def test_inspect_reports_what_the_model_offers():
    info = inspect_model(CART)
    assert info["base_body"] == "cart" and info["free_base"] and info["cameras"] == ["front_cam"]
    assert [a["name"] for a in info["actuators"]] == ["left_motor", "right_motor"]
    assert info["actuators"][0]["lo"] == -12 and info["actuators"][0]["hi"] == 12 and info["actuators"][0]["limited"]
    assert info["mass"] == pytest.approx(2.45, abs=0.05) and 0.3 < info["extent"][0] < 0.6 and info["dofs"] == 6 + 2
    assert not info["has_own_plane"]


def test_inspect_errors_are_readable():
    with pytest.raises(FileNotFoundError, match="not found"):
        inspect_model(str(FIX / "nope.xml"))
    with pytest.raises(KeyError, match="no body named 'wing'"):
        inspect_model(CART, "wing")
    bad = FIX / "broken.xml"
    bad.write_text("<mujoco><worldbody><geom type='warp'/></worldbody></mujoco>")
    try:
        with pytest.raises(ValueError, match="could not load"):
            inspect_model(str(bad))
    finally:
        bad.unlink()


def test_a_urdf_is_converted_once_and_then_read_like_any_model(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "CONVERTED", tmp_path)
    info = inspect_model(str(FIX / "pendulum.urdf"))
    # MuJoCo welds a URDF's root link ("base") to the world, so the movable root is the arm: a fixed-base robot
    assert (tmp_path / "pendulum.xml").exists() and info["base_body"] == "arm" and info["dofs"] == 1 and not info["free_base"]
    assert info["mass"] == pytest.approx(0.3, abs=0.05)
    stamp = (tmp_path / "pendulum.xml").stat().st_mtime
    inspect_model(str(FIX / "pendulum.urdf"))
    assert (tmp_path / "pendulum.xml").stat().st_mtime == stamp                  # not converted again


def test_the_draft_spec_is_valid_and_exposes_every_actuator():
    d = draft_spec(CART)
    assert d["kind"] == "custom" and d["name"] == "cart" and d["params"]["actuators"] == ["left_motor", "right_motor"]
    assert d["sensors"][0]["mjcf_camera"] == "front_cam" and d["body"]["length"] == round(d["body"]["length"], 3)     # its own camera, tidy numbers
    spec = VehicleSpec.from_dict(d)
    assert spec.validate() == [] and spec.space().names == ("left_motor", "right_motor") and spec.default_space == "raw"
    assert {f.key for f in spec.derived()} >= {"mass", "actuators", "dofs"}
    sub = cart_spec(actuators=["right_motor"])
    assert sub.space().names == ("right_motor",)
    assert VehicleSpec.from_dict(spec.to_dict()) == spec


def test_a_list_of_names_may_be_given_as_comma_separated_text():
    p = Param("a", "strlist", [])
    assert p.coerce("x, y ,, z") == ["x", "y", "z"] and p.coerce(["x", " y "]) == ["x", "y"] and p.coerce(None) == []


def test_validation_explains_what_is_wrong_with_a_model():
    d = draft_spec(CART)
    d["params"]["model"] = str(FIX / "gone.xml")
    msgs = [str(i) for i in VehicleSpec.from_dict(d).validate()]
    assert any("params.model" in m and "not found" in m for m in msgs)
    d["params"]["model"] = CART
    d["params"]["base_body"] = "wing"
    assert any("params.base_body" in str(i) for i in VehicleSpec.from_dict(d).validate())
    d["params"]["base_body"] = "cart"
    d["params"]["actuators"] = ["left_motor", "rocket"]
    assert any("rocket" in str(i) for i in VehicleSpec.from_dict(d).validate())
    d["params"]["model"] = ""
    assert any("choose a model file" in str(i) for i in VehicleSpec.from_dict(d).validate())
    pend = draft_spec(str(FIX / "pendulum.urdf"))
    assert any(i.level == "warn" and "no actuators" in i.msg for i in VehicleSpec.from_dict(pend).validate())


# ---------------------------------------------------------------- driving an imported model
def test_an_imported_cart_drives_turns_and_sees_through_its_own_camera():
    sim = Sim(cart_spec(), get_task_spec("sandbox"), seed=1)
    sim.reset(seed=1)
    for _ in range(100):
        sim.step_physical([8, 8])
    st = sim.obs.state
    assert st.vel_body[0] == pytest.approx(8 * 0.06, rel=0.12) and abs(st.vel_body[1]) < 0.05 and st.pos[2] == pytest.approx(0.07, abs=0.02)
    for _ in range(60):
        sim.step_physical([-4, 4])
    assert sim.obs.state.omega_body[2] > 0.8                                         # spinning left on the spot
    img = sim.backend.render("front")
    assert img.shape == (240, 320, 3) and 20 < img.mean() < 235
    sc = sim.backend.scene()
    assert sc["bodies"][sc["vehicle_body"]] == "cart" and {"box", "cylinder", "sphere"} <= {g["type"] for g in sc["geoms"]}
    assert not sim.step_physical([30, 30])[4].get("success")                          # commands beyond the actuator range are clipped, not errors


def test_the_motor_range_and_failure_randomisation_apply():
    sim = Sim(cart_spec(), get_task_spec("sandbox"), seed=1)
    sim.reset(seed=1)
    for _ in range(40):
        sim.step_physical([12, 12])
    fast = sim.obs.state.vel_body[0]
    weak = Sim(cart_spec(), TaskSpec.from_dict({**get_task_spec("sandbox").to_dict(), "randomize": {"motor_strength": 0.9}}), seed=1)
    gains = set()
    for s in range(6):
        weak.reset(seed=s)
        gains.add(round(weak.backend.plant.gain, 4))
    assert len(gains) > 3 and min(gains) < 0.6 and 0.55 < fast < 0.75


def test_teleporting_and_colliding_an_imported_model():
    sim = Sim(cart_spec(), get_task_spec("sandbox"), seed=1)
    sim.reset(seed=1)
    o = sim.backend.teleport(3.0, -2.0, 0.0, 1.0)
    assert np.allclose(o.state.pos[:2], [3.0, -2.0]) and o.state.yaw == pytest.approx(1.0, abs=1e-6)
    sim.backend.teleport(9.0, 0.0, 0.0, 0.0)                                         # facing the east wall at x = 12
    hit = False
    for _ in range(250):
        sim.step_physical([12, 12])
        hit = hit or sim.obs.state.collided
    assert hit and sim.obs.state.pos[0] < 12.0


def test_relative_asset_paths_inside_the_model_folder_are_honoured(tmp_path):
    tri = [(0, 0, 0), (0.3, 0, 0), (0, 0.3, 0), (0, 0, 0.3)]
    faces = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)]
    blob = b"\0" * 80 + struct.pack("<I", len(faces))
    for f in faces:
        blob += struct.pack("<12fH", 0, 0, 0, *[c for i in f for c in tri[i]], 0)
    (tmp_path / "tet.stl").write_bytes(blob)
    (tmp_path / "meshbot.xml").write_text(
        '<mujoco><compiler meshdir="."/><asset><mesh name="tet" file="tet.stl"/></asset><worldbody>'
        '<body name="bot" pos="0 0 0.3"><freejoint/><geom type="mesh" mesh="tet" mass="1"/></body></worldbody></mujoco>')
    spec = VehicleSpec.from_dict(draft_spec(str(tmp_path / "meshbot.xml")))
    sim = Sim(spec, get_task_spec("sandbox"), seed=1)
    sim.reset(seed=1)
    sc = sim.backend.scene()
    mesh_geoms = [g for g in sc["geoms"] if g["type"] == "mesh"]
    assert mesh_geoms and len(sc["meshes"][mesh_geoms[0]["mesh"]]["verts"]) == 4 * 3 and len(sc["meshes"][mesh_geoms[0]["mesh"]]["faces"]) == 4 * 3
    for _ in range(40):
        sim.step_physical([0.0])
    assert sim.obs.state.pos[2] < 0.3                                                # it fell and rests on the floor


def test_the_sandbox_fits_every_kind_of_machine():
    for name in ("mid_4wd", "quad_250", "boat_small"):
        task = "sandbox_water" if name.startswith("boat") else "sandbox"
        sim = Sim(get_vehicle(name), get_task_spec(task), seed=1)
        vec = sim.reset(seed=1)
        assert vec.shape == (9,) and np.isfinite(vec).all() and sim.task.baseline() is None
        _, r, term, trunc, info = sim.step_physical(sim.space.zeros())
        assert r == 0.0 and not term and not trunc
    with pytest.raises(ValueError, match="no baseline"):
        from simlab.control import make_policy
        make_policy("baseline", sim)


# ---------------------------------------------------------------- the boat plug-in
def test_the_boat_plugin_is_loaded_from_the_config_file():
    assert "simlab.plugins.boat" in load_plugins() and not PLUGIN_ERRORS
    assert {"boat_small", "boat_workboat"} <= set(vehicle_names("boat"))
    assert get_vehicle("boat_small").space().names == ("throttle", "rudder")


@pytest.mark.parametrize("name", ["boat_small", "boat_workboat"])
def test_a_boat_floats_at_its_draft_and_stays_level(name):
    v = get_vehicle(name)
    draft = v.derived()[0].value
    sim = Sim(v, get_task_spec("sandbox_water"), seed=1)
    sim.reset(seed=1)
    for _ in range(100):
        sim.step_physical([0, 0])
    st = sim.obs.state
    r, p, _ = euler(st.quat)
    assert st.pos[2] == pytest.approx(v.body["height"] / 2 - draft, abs=0.01) and st.extra["draft"] == pytest.approx(draft, abs=0.01)
    assert max(abs(r), abs(p)) < math.radians(0.5) and np.linalg.norm(st.vel) < 0.02


@pytest.mark.parametrize("name", ["boat_small", "boat_workboat"])
def test_a_boat_accelerates_turns_symmetrically_and_coasts(name):
    v = get_vehicle(name)
    open_water = TaskSpec.from_dict({**get_task_spec("sandbox_water").to_dict(), "params": {"pillars": 0}, "world": {"width": 400, "depth": 400, "theme": "water"}})
    sim = Sim(v, open_water, seed=1)
    sim.reset(seed=1)
    for _ in range(300):
        sim.step_physical([1.0, 0.0])
    st = sim.obs.state
    top = v.derived()[2].value
    assert st.vel_body[0] == pytest.approx(top, rel=0.2) and abs(st.pos[1]) < 0.05
    r, p, _ = euler(st.quat)
    assert max(abs(r), abs(p)) < math.radians(6)
    for _ in range(120):
        sim.step_physical([1.0, 1.0])
    left = sim.obs.state.omega_body[2]
    for _ in range(120):
        sim.step_physical([1.0, -1.0])
    right = sim.obs.state.omega_body[2]
    assert left > 0.2 and right < -0.2 and abs(left + right) < 0.5 * abs(left)
    for _ in range(400):
        sim.step_physical([0.0, 0.0])
    assert abs(sim.obs.state.vel_body[0]) < 0.35 * top
    for _ in range(300):
        sim.step_physical([-1.0, 0.0])
    assert sim.obs.state.vel_body[0] < -0.4 * top                                   # reverse works


def test_a_hull_that_cannot_float_is_rejected_and_a_low_one_is_warned_about():
    d = get_vehicle("boat_small").to_dict()
    d["body"]["mass"] = 200.0
    issues = VehicleSpec.from_dict(d).validate()
    assert any(i.level == "error" and "sink" in i.msg for i in issues)
    d["body"]["mass"] = 144.0                                       # floats (the hull displaces 180 kg) but sits 80 % under water
    issues = VehicleSpec.from_dict(d).validate()
    assert not any(i.level == "error" for i in issues) and any("freeboard" in i.msg for i in issues)


# ---------------------------------------------------------------- the plug-in mechanism itself
def test_a_failing_plugin_is_recorded_and_never_breaks_loading(tmp_path, monkeypatch):
    from simlab import registry
    (tmp_path / "simlab_test_plugin_ok.py").write_text("LOADED = True\n")
    (tmp_path / "simlab_test_plugin_bad.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("SIMLAB_PLUGINS", "simlab_test_plugin_ok, simlab_test_plugin_bad, simlab_test_plugin_missing")
    try:
        ok = load_plugins()
        assert "simlab_test_plugin_ok" in ok and "simlab_test_plugin_bad" not in ok
        assert "boom" in PLUGIN_ERRORS["simlab_test_plugin_bad"] and "ModuleNotFoundError" in PLUGIN_ERRORS["simlab_test_plugin_missing"]
        assert sys.modules["simlab_test_plugin_ok"].LOADED
    finally:
        for n in ("simlab_test_plugin_ok", "simlab_test_plugin_bad", "simlab_test_plugin_missing"):
            registry._loaded.discard(n)
            PLUGIN_ERRORS.pop(n, None)


def test_a_vehicle_file_that_needs_a_missing_plugin_is_skipped_and_reported(tmp_path, monkeypatch):
    (tmp_path / "ghost_ship.yaml").write_text("name: ghost_ship\nkind: spaceship\nbody: {mass: 1}\n")
    get_vehicle("mid_4wd").override(name="fine").dump(tmp_path / "fine.yaml")
    monkeypatch.setenv("SIMLAB_VEHICLES", str(tmp_path))
    names = vehicle_names()
    assert "ghost_ship" not in names and "fine" in names and "mid_4wd" in names
    assert "spaceship" in broken_vehicles()["ghost_ship"]


def test_saved_custom_vehicles_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMLAB_VEHICLES", str(tmp_path))
    path = save_vehicle(cart_spec().override(name="my_cart"))
    assert path.parent == tmp_path and "my_cart" in vehicle_names("custom")
    assert get_vehicle("my_cart").params["actuators"] == ["left_motor", "right_motor"]
    with pytest.raises(SpecError):
        save_vehicle(cart_spec(model=str(FIX / "gone.xml")).override(name="bad_cart"))


# ---------------------------------------------------------------- the runner and the HTTP API
def test_the_runner_drives_a_custom_model_with_named_channels():
    from simlab.runtime import LabRunner
    r = LabRunner()
    try:
        r.start(cart_spec(), get_task_spec("sandbox"), policy="manual", speed=1.0)
        t0 = time.time()
        while r.state().get("status") != "running" and time.time() - t0 < 10:
            time.sleep(0.05)
        for _ in range(12):
            r.command({"left_motor": 6.0, "right_motor": 6.0})
            time.sleep(0.1)
        s = r.state()
        assert s["policy"] == "manual" and s["cmd"] == {"left_motor": 6.0, "right_motor": 6.0} and s["speed"] > 0.1
    finally:
        r.stop()


def test_import_endpoint_returns_a_checked_draft(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import importlib
    router_mod = importlib.import_module("simlab.dashboard.router")
    app = FastAPI()
    app.include_router(router_mod.router)
    with TestClient(app) as c:
        r = c.post("/api/lab/import", json={"path": CART}).json()
        assert r["ok"] and r["spec"]["kind"] == "custom" and r["spaces"]["raw"]["channels"][0]["name"] == "left_motor"
        assert c.post("/api/lab/import", json={"path": str(FIX / "gone.xml")}).status_code == 404
        assert c.post("/api/lab/import", json={"path": str(FIX / "cart.txt")}).status_code == 422
        cat = c.get("/api/lab/catalog").json()
        assert {"boat_small", "boat_workboat"} <= set(cat["vehicles"]) and cat["tasks"]["sandbox"]["baseline"] is False
        assert cat["tasks"]["goto_open"]["baseline"] is True and "custom" in cat["kinds"] and "boat" in cat["kinds"] and "problems" in cat
    router_mod.runner.stop()
