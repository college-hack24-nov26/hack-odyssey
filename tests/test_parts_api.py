"""The Builder's HTTP API: library, evaluate, preview, save/load, inventory, user parts."""
import copy

import pytest

pytest.importorskip("mujoco")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from simlab import get_vehicle  # noqa: E402
from simlab.dashboard.build_router import router  # noqa: E402
from simlab.parts.templates import example_builds  # noqa: E402

ROVER = example_builds()["esp32_pipe_rover"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inventory.yaml"), ("SIMLAB_BUILDS", "builds"), ("SIMLAB_VEHICLES", "vehicles")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def test_library_lists_every_kind_with_its_form_schema_and_examples(client):
    lib = client.get("/api/build/library").json()
    assert {"motor_dc", "wheel", "driver_dc", "battery", "mcu", "sensor_camera"} <= set(lib["kinds"])
    assert any(p["name"] == "tt_motor_6v" and p["confidence"] and p["source"] for p in lib["parts"]["motor_dc"])
    assert lib["kinds"]["motor_dc"]["params"][0]["name"] == "voltage_rated" and lib["drives"] == ["diff2wd", "skid4wd", "skid6wd", "tracked"]
    assert not lib["broken"]
    for name, b in lib["examples"].items():
        r = client.post("/api/build/evaluate", json={"build": b}).json()
        assert r["ok"], (name, r["findings"])


@pytest.mark.parametrize("body", [{"build": {}}, {"build": {"name": "x", "drive": 3}}, {"build": {"name": "ok", "sensors": [1, 2]}},
                                  {"build": {"name": "../../x"}}, {"build": {"power": {"series": "many"}}}, {"nothing": 1}, {"build": 5}])
def test_evaluate_never_answers_with_a_server_error(client, body):
    assert client.post("/api/build/evaluate", json=body).status_code in (200, 422)


def test_evaluate_gives_everything_the_tab_shows(client):
    r = client.post("/api/build/evaluate", json={"build": ROVER}).json()
    assert r["ok"] and r["vehicle"]["name"] == "esp32_pipe_rover"
    assert {"top_speed", "accel", "turn_rate", "climb", "runtime", "mass"} <= {f["key"] for f in r["facts"]}
    assert r["budget"]["chain"] and r["assumptions"] and r["budget"]["mass"]


def test_preview_draws_the_vehicle_and_the_parts_inside_it(client):
    out = client.post("/api/build/preview", json={"build": ROVER}).json()
    sc = out["scene"]
    names = [g["name"] for g in sc["geoms"]]
    assert {"chassis", "battery", "controller", "driver", "lamp", "sensor_front", "sensor_sonar", "view_front", "motor_l0", "motor_r0"} <= set(names)
    assert next(g for g in sc["geoms"] if g["name"] == "chassis")["rgba"][3] < 1.0                       # see-through, so the parts show
    cone = next(g for g in sc["geoms"] if g["name"] == "view_front")
    mesh = sc["meshes"][str(cone["mesh"])]
    assert len(mesh["verts"]) == 15 and max(mesh["faces"]) == 4                                           # a pyramid: apex + 4 corners
    assert len(out["poses"]) == len(sc["bodies"]) and all(len(p) == 7 for p in out["poses"])
    again = client.post("/api/build/preview", json={"build": ROVER}).json()
    strip = lambda s: [{k: v for k, v in g.items()} for g in s["geoms"]]                                  # noqa: E731
    assert strip(again["scene"]) == strip(sc)                                                             # same build, same picture
    assert client.post("/api/build/preview", json={"build": {"name": "x"}}).status_code == 422


def test_the_camera_cone_follows_the_mount_tilt(client):
    def apex_to_centre_z(tilt):
        b = copy.deepcopy(ROVER)
        b["sensors"][0]["mount"]["tilt_deg"] = tilt
        sc = client.post("/api/build/preview", json={"build": b}).json()["scene"]
        v = sc["meshes"][str(next(g for g in sc["geoms"] if g["name"] == "view_front")["mesh"])]["verts"]
        apex_z, base_z = v[2], sum(v[3 * i + 2] for i in range(1, 5)) / 4
        return base_z - apex_z
    assert apex_to_centre_z(-10) > 0 > apex_to_centre_z(25)                                              # tilted up rises, tilted down falls


def test_save_writes_the_build_and_a_vehicle_the_lab_can_use(client, tmp_path):
    r = client.post("/api/build/save", json={"build": ROVER})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (tmp_path / "builds" / "esp32_pipe_rover.yaml").is_file() and (tmp_path / "vehicles" / "esp32_pipe_rover.yaml").is_file()
    assert out["builds"] == ["esp32_pipe_rover"]
    v = get_vehicle("esp32_pipe_rover")                                                                  # the Lab finds it like any preset
    assert v.kind == "ground_wheeled" and v.params["max_speed"] == pytest.approx(out["vehicle"]["params"]["max_speed"])
    assert client.get("/api/build/list").json() == {"builds": ["esp32_pipe_rover"]}
    assert client.get("/api/build/get", params={"name": "esp32_pipe_rover"}).json()["controller"] == "esp32_devkit"
    assert client.get("/api/build/get", params={"name": "../secret"}).status_code == 404
    gone = client.post("/api/build/delete", json={"name": "esp32_pipe_rover"}).json()
    assert gone["builds"] == [] and not (tmp_path / "vehicles" / "esp32_pipe_rover.yaml").exists()
    assert client.post("/api/build/delete", json={"name": "esp32_pipe_rover"}).status_code == 404


def test_save_refuses_a_build_with_errors_or_a_built_in_name(client, tmp_path):
    broken = copy.deepcopy(ROVER)
    broken["power"]["regulator"] = ""                                                                    # the ESP32 would brown out
    r = client.post("/api/build/save", json={"build": broken})
    assert r.status_code == 422 and "brown out" in r.json()["detail"]
    assert not (tmp_path / "builds").exists()
    clash = copy.deepcopy(ROVER)
    clash["name"] = "mid_4wd"
    r = client.post("/api/build/save", json={"build": clash})
    assert r.status_code == 422 and "built-in" in r.json()["detail"]
    assert client.post("/api/build/save", json={"build": {**ROVER, "name": "../evil"}}).status_code == 422


def test_inventory_and_your_own_parts(client):
    assert client.post("/api/build/inventory", json={"owned": {"tt_motor_6v": 2, "hc_sr04": 0}}).json()["owned"] == {"tt_motor_6v": 2}
    lib = client.get("/api/build/library").json()
    assert next(p for p in lib["parts"]["motor_dc"] if p["name"] == "tt_motor_6v")["owned"] == 2
    part = {"name": "my_motor", "kind": "motor_dc", "title": "My motor", "source": "label on the can", "confidence": "datasheet", "mass_kg": 0.03,
            "params": {"voltage_rated": 6.0, "no_load_rpm": 180.0, "no_load_current_a": 0.1, "stall_torque_nm": 0.1, "stall_current_a": 1.2}}
    assert client.post("/api/build/part", json={"part": part}).status_code == 200
    mine = next(p for p in client.get("/api/build/library").json()["parts"]["motor_dc"] if p["name"] == "my_motor")
    assert mine["origin"] == "user"
    b = copy.deepcopy(ROVER)
    b["drive"]["motor"] = "my_motor"
    assert client.post("/api/build/evaluate", json={"build": b}).json()["ok"]
    bad = copy.deepcopy(part)
    bad["params"]["stall_torque_nm"] = 10.0                                                              # a unit mistake
    assert client.post("/api/build/part", json={"part": bad}).status_code == 422
    assert client.post("/api/build/part/delete", json={"name": "my_motor"}).status_code == 200
    assert client.post("/api/build/part/delete", json={"name": "tt_motor_6v"}).status_code == 404       # shipped parts are not deletable


def test_dashboard_scripts_do_not_redeclare_each_others_global_names():
    """The tabs' scripts share one global scope: a function declared twice silently replaces the first (this broke the Builder twice)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    files = [root / "sewerbot/dashboard/static/app.js", *sorted((root / "simlab/dashboard/static").glob("*.js"))]
    decl = re.compile(r"^(?:async\s+)?(?:function\s+(\w+)|(?:const|let|var|class)\s+(\w+))", re.M)
    seen: dict[str, str] = {}
    clashes = []
    for f in files:
        for m in decl.finditer(f.read_text(encoding="utf-8")):
            name = m.group(1) or m.group(2)
            if name in seen and seen[name] != f.name:
                clashes.append(f"{name}: {seen[name]} and {f.name}")
            seen.setdefault(name, f.name)
    assert not clashes, clashes
