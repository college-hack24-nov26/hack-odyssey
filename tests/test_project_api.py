"""The project HTTP API through a TestClient: route order against catch-all routes shaped like sewerbot's own, list/create/get/update, the guarded steps, errors."""
import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from simlab.parts.templates import example_builds
from simlab.project import ProjectService, ProjectStore

ROOT = Path(__file__).resolve().parents[1]
project_router = importlib.import_module("simlab.dashboard.project_router")        # the module (`from simlab.dashboard import project_router` is the router object)
ROVER = example_builds()["esp32_pipe_rover"]
ENV = {"name": "tile floor, tape line, two bags, one box", "surface": "tile floor", "objects": [{"name": "bag", "count": 2}]}


def build_app(decoy_first: bool = False) -> FastAPI:
    """The dashboard's order: the simlab routers first, then catch-all routes shaped like sewerbot's own."""
    app = FastAPI()

    def decoys():
        @app.get("/api/{kind}/{what}")
        def _any(kind: str, what: str):
            return {"decoy": [kind, what]}

        @app.get("/api/{kind}/state")
        def _state(kind: str):
            return {"decoy": kind}

        @app.post("/api/{kind}/detector")
        def _det(kind: str):
            return {"decoy": kind}
    if decoy_first:
        decoys()
    app.include_router(project_router.router)
    if not decoy_first:
        decoys()
    return app


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMLAB_INVENTORY", str(tmp_path / "inventory.yaml"))
    project_router.set_service(ProjectService(ProjectStore(tmp_path / "projects")))
    with TestClient(build_app()) as c:
        yield c
    project_router.set_service(None)


def new(c, **body) -> str:
    r = c.post("/api/project", json={"name": "Bag rover", **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ route order
def test_the_project_routes_are_not_shadowed_by_the_catch_all_routes(api):
    assert api.get("/api/project").json() == {"projects": []}
    pid = new(api)
    got = api.get(f"/api/project/{pid}").json()
    assert "decoy" not in got and got["id"] == pid
    assert api.get(f"/api/project/{pid}/compare").status_code == 422                  # a project route answered (no blueprint yet), not the decoy
    assert api.get("/api/lab/state").json() == {"decoy": ["lab", "state"]}              # the decoys really are there for everything else


def test_the_test_would_notice_shadowing(tmp_path):
    project_router.set_service(ProjectService(ProjectStore(tmp_path)))
    try:
        with TestClient(build_app(decoy_first=True)) as c:
            assert c.get("/api/project/anything").json() == {"decoy": ["project", "anything"]}
    finally:
        project_router.set_service(None)


def test_the_real_app_registers_the_project_router_before_its_catch_all_routes():
    src = (ROOT / "sewerbot" / "dashboard" / "app.py").read_text(encoding="utf-8")
    inc = src.index("app.include_router(project_router)")
    for catch_all in ('@app.get("/api/{kind}/state")', '@app.websocket("/ws/{kind}")', '@app.get("/api/{kind}/video")', '@app.post("/api/{kind}/detector")'):
        assert inc < src.index(catch_all), catch_all
    assert src.index("app.include_router(chat_router)") < inc
    init = (ROOT / "simlab" / "dashboard" / "__init__.py").read_text(encoding="utf-8")
    assert "project_router" in init and "project_router" in init.split("__all__")[1]


def test_the_real_app_serves_the_project_routes(tmp_path, monkeypatch):
    pytest.importorskip("mujoco")
    monkeypatch.setenv("SIMLAB_PROJECTS", str(tmp_path / "p"))
    project_router.set_service(None)
    try:
        from sewerbot.dashboard.app import app
        with TestClient(app) as c:                                                    # the whole dashboard, with its own catch-all routes
            r = c.post("/api/project", json={"name": "real app"})
            assert r.status_code == 200 and "decoy" not in r.json() and r.json()["name"] == "real app"
            pid = r.json()["id"]
            assert c.get(f"/api/project/{pid}").json()["id"] == pid
            assert [p["id"] for p in c.get("/api/project").json()["projects"]] == [pid]
    finally:
        project_router.set_service(None)


# ------------------------------------------------------------------ list / create / get / update
def test_create_get_list_update_round_trip(api):
    pid = new(api, text="follow a bag", owned={"esp32_devkit": 1})
    v = api.get(f"/api/project/{pid}").json()
    assert v["requirements"]["text"] == "follow a bag" and v["owned"] == {"esp32_devkit": 1} and v["stages"]["current"] == "blueprint"
    r = api.put(f"/api/project/{pid}", json={"name": "Renamed", "environment": ENV, "blueprint": {"build": ROVER, "goal": "sewer_inspect"}})
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["environment"]["name"] == ENV["name"] and r.json()["blueprint"]["check"]["ok"]
    assert api.get("/api/project").json()["projects"][0]["name"] == "Renamed"
    assert api.get(f"/api/project/{pid}").json()["equipment"]["rows"]


def test_unknown_damaged_and_malformed_requests_get_plain_errors(api):
    assert api.get("/api/project/" + "0" * 12).status_code == 404
    assert api.get("/api/project/not-an-id").status_code == 404
    assert api.put("/api/project/" + "0" * 12, json={"name": "x"}).status_code == 404
    assert api.post("/api/project/" + "0" * 12 + "/runs", json={"kind": "sim"}).status_code == 404
    assert api.get("/api/project/..%2f..%2fetc").status_code == 404
    pid = new(api)
    r = api.put(f"/api/project/{pid}", json={"environment": {"surface": "floor"}})
    assert r.status_code == 422 and "name" in r.json()["detail"]
    assert api.put(f"/api/project/{pid}", json={"bogus": 1}).status_code == 422
    assert api.put(f"/api/project/{pid}", json=[1, 2]).status_code == 422
    assert api.post("/api/project", json={"owned": {"flux_capacitor": 1}}).status_code == 422
    path = project_router.service().store.folder(pid) / "project.json"
    path.write_text("{oops", encoding="utf-8")
    r = api.get(f"/api/project/{pid}")
    assert r.status_code == 409 and "damaged" in r.json()["detail"]
    assert api.get("/api/project").json()["projects"][0]["damaged"] is True
    assert api.delete(f"/api/project/{pid}").json() == {"ok": True} and api.get("/api/project").json() == {"projects": []}


# ------------------------------------------------------------------ the guarded steps
def test_a_sensor_is_verified_only_by_the_confirm_route_with_confirm_true(api):
    pid = new(api)
    assert api.put(f"/api/project/{pid}", json={"sensors": {"hcsr04": "verified"}}).status_code == 422
    assert api.post(f"/api/project/{pid}/sensors/hcsr04/confirm", json={"confirm": True}).status_code == 422             # not fitted yet
    assert api.put(f"/api/project/{pid}", json={"sensors": {"hcsr04": "fitted"}}).status_code == 200
    for body in ({}, {"note": "looks fine"}, {"confirm": False}):
        r = api.post(f"/api/project/{pid}/sensors/hcsr04/confirm", json=body)
        assert r.status_code == 422 and "confirm" in r.json()["detail"]
    assert api.get(f"/api/project/{pid}").json()["sensors"]["hcsr04"]["level"] == "fitted"
    r = api.post(f"/api/project/{pid}/sensors/hcsr04/confirm", json={"confirm": True, "note": "saw 12-200 cm"})
    assert r.status_code == 200 and r.json()["sensors"]["hcsr04"]["level"] == "verified" and r.json()["sensors"]["hcsr04"]["note"] == "saw 12-200 cm"
    assert api.post(f"/api/project/{pid}/sensors/lidar/confirm", json={"confirm": True}).status_code == 422
    assert api.post(f"/api/project/{pid}/sensors/hcsr04/confirm", json={"confirm": "yes please"}).status_code == 422       # not a boolean


def test_parts_stage_and_runs_through_the_routes(api):
    pid = new(api, text="x", owned={"esp32_devkit": 1})
    assert api.post(f"/api/project/{pid}/parts/confirm").status_code == 422                                                # no blueprint yet
    assert api.post(f"/api/project/{pid}/runs", json={"kind": "sim", "summary": "no scene"}).status_code == 422             # no environment yet
    api.put(f"/api/project/{pid}", json={"blueprint": {"build": ROVER}, "environment": ENV, "prices": {"hc_sr04": 2.0}})
    assert api.post(f"/api/project/{pid}/parts/confirm").json()["parts_confirmed"] is True
    r = api.post(f"/api/project/{pid}/runs", json={"kind": "sim", "summary": "laps", "ok": True, "data": {"laps": 3}})
    assert r.status_code == 200 and r.json()["environment"] == ENV["name"]
    assert api.post(f"/api/project/{pid}/runs", json={"kind": "real"}).status_code == 422                                  # a real run must say how it connected
    assert api.post(f"/api/project/{pid}/stage/deploy", json={}).status_code == 422                                          # out of order
    assert api.post(f"/api/project/{pid}/stage/test", json={}).status_code == 422                                            # not markable
    v = api.post(f"/api/project/{pid}/stage/connect", json={"note": "COM5"}).json()
    assert v["marks"]["connect"]["note"] == "COM5" and v["stages"]["current"] == "test"
    assert api.post(f"/api/project/{pid}/stage/gaps").status_code == 422                                                      # (no body at all is fine; the order is not)
    assert api.get(f"/api/project/{pid}").json()["run_count"] == 1


def test_compare_over_http(api):
    kit = {"esp32_devkit": 1, "esp32cam_gc2145_board": 1, "tt_motor_6v": 2, "wheel_65mm_tt": 2, "drv8833_module_pw": 1, "aa_nimh_2000": 4}
    pid = new(api, owned=kit)
    api.put(f"/api/project/{pid}", json={"blueprint": {"build": ROVER}})
    c = api.get(f"/api/project/{pid}/compare").json()
    assert {s["swap_to"] for s in c["swaps"]} >= {"drv8833_module_pw", "esp32cam_gc2145_board"} and c["saved"] is None and "no price" in c["note"]
