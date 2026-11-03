"""Task requirements, the checks of a build against them, and the part recommender."""
import copy

import pytest

from simlab import get_task_spec, get_vehicle
from simlab.parts import Part, evaluate, get_part, save_inventory, save_part
from simlab.parts.fit import check
from simlab.parts.recommend import apply_patch, candidates, make_patch
from simlab.parts.build import Build
from simlab.parts.derive import build_to_vehicle
from simlab.parts.templates import example_builds
from simlab.registry import TASKS
from simlab.tasks import Requirement

ROVER = example_builds()["esp32_pipe_rover"]


def rover(**changes):
    b = copy.deepcopy(ROVER)
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(b.get(k), dict):
            b[k].update(v)
        else:
            b[k] = v
    return b


def by_id(res, fid, scope=None):
    return [f for f in res["findings"] if f["id"] == fid and (scope is None or f["scope"] == scope)]


@pytest.fixture()
def user_dirs(tmp_path, monkeypatch):
    for var, sub in (("SIMLAB_PARTS", "parts"), ("SIMLAB_INVENTORY", "inventory.yaml"), ("SIMLAB_BUILDS", "builds"), ("SIMLAB_VEHICLES", "vehicles")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path


# ---------------------------------------------------------------- what tasks ask for
def reqs(task, vehicle="mid_4wd"):
    ts = get_task_spec(task)
    return TASKS.get(ts.task)(ts, get_vehicle(vehicle)).requirements()


def test_tasks_declare_what_they_need_from_the_hardware():
    goto = reqs("goto_open")
    assert any(r.kind == "sensor" and r.params["type"] == "rangefinder" and r.params["ideal"] for r in goto)      # the simulator adds this one itself
    cam = next(r for r in goto if r.params.get("type") == "camera")
    assert cam.params["optional"]                                                                              # the goal label only trains a detector
    assert any(r.kind == "endurance" and r.params["seconds"] > 0 for r in goto)
    sewer = {r.kind: r for r in reqs("sewer_star")}
    assert {"sensor", "light", "endurance", "fit"} <= set(sewer) and not sewer["sensor"].params.get("optional")
    assert sewer["fit"].params["start_ok"] and set(sewer["fit"].params["fits"]) >= {"east", "north"}
    search = next(r for r in reqs("search_targets", "quad_250") if r.params.get("type") == "camera")
    assert not search.params["optional"]


# ---------------------------------------------------------------- checking a build
def test_the_esp32_rover_suits_the_sewer_task_and_the_checklist_says_why():
    r = evaluate(ROVER, "sewer_star")
    assert r["ok"] and r["task_ok"]
    assert r["fit"]["title"] and [i["status"] for i in r["fit"]["items"]] == ["ok"] * 4
    assert not [f for f in r["findings"] if f["scope"] == "task"]


def test_missing_camera_and_lamp_fail_the_task_but_not_the_machine():
    r = evaluate(rover(sensors=[], lamp=None), "sewer_star")
    assert r["ok"] and not r["task_ok"]                                                                       # a fine machine that cannot do this job
    t1 = by_id(r, "T01")[0]
    assert t1["severity"] == "error" and t1["scope"] == "task" and t1["fix"]["kind"] == "sensor_camera"
    assert by_id(r, "T03")[0]["severity"] == "warn"
    assert {i["status"] for i in r["fit"]["items"]} >= {"fail", "warn"}
    sim_only = evaluate(rover(sensors=[]), "goto_open")                                                       # goto: the simulator adds its own rangefinder
    t1 = by_id(sim_only, "T01")
    assert t1 and t1[0]["severity"] == "warn" and "ideal" in t1[0]["detail"]
    assert sim_only["task_ok"]


def test_a_chassis_too_big_for_the_pipes_is_an_error_for_that_task_only():
    huge = {"width": 1.6, "height": 1.4, "length": 1.2}
    r = evaluate(rover(chassis=huge), "sewer_star")
    assert by_id(r, "T04")[0]["severity"] == "error" and not r["task_ok"]
    assert any(i["kind"] == "fit" and i["status"] == "fail" for i in r["fit"]["items"])
    mid = by_id(evaluate(rover(chassis={"width": 0.6, "height": 0.4, "length": 0.5}), "sewer_star"), "T04")
    assert mid and mid[0]["severity"] == "warn"                                                                # fits the big pipes only: an inspect-everything mission would skip some
    assert evaluate(rover(chassis=huge), "goto_open")["task_ok"]                                              # an open arena has no pipes


def test_battery_that_cannot_last_the_task_says_how_much_more_it_needs(user_dirs):
    save_part(Part("tiny_cell", "battery", "", dict(get_part("aa_nimh_2000").params, capacity_mah=10.0), 0.003, "test", "assumed"))
    r = evaluate(rover(power={"battery": "tiny_cell"}), "sewer_star")
    t5 = by_id(r, "T05")[0]
    assert t5["severity"] == "warn" and "mAh" in t5["detail"] and t5["fix"]["constraints"]["capacity_mah"][0] > 10


def test_slow_camera_speed_and_slope_requirements():
    b = Build.from_dict(rover())
    spec, dv = build_to_vehicle(b)
    items, finds = check(b, dv, [Requirement("speed", "go fast", {"min_mps": 5.0}), Requirement("slope", "climb", {"deg": 80.0}),
                                 Requirement("sensor", "fast camera", {"type": "camera", "min_fps": 60.0, "min_width": 1280})])
    assert {f.id for f in finds} == {"T06", "M04", "T02"} and all(f.scope == "task" for f in finds)
    assert [i["status"] for i in items] == ["warn"] * 3
    ok_items, ok_finds = check(b, dv, [Requirement("speed", "crawl", {"min_mps": 0.1}), Requirement("slope", "flat", {"deg": 5.0}),
                                       Requirement("sensor", "any camera", {"type": "camera", "min_fps": 5.0, "min_width": 320})])
    assert not ok_finds and all(i["status"] == "ok" for i in ok_items)


def test_a_task_for_another_kind_of_machine_is_reported_not_raised():
    r = evaluate(ROVER, "waypoints_easy")                                                                     # a drone task
    assert not r["task_ok"] and r["fit"]["items"][0]["status"] == "fail" and "ground" in r["fit"]["items"][0]["detail"]
    r = evaluate(ROVER, "no_such_task")
    assert not r["task_ok"] and r["fit"]["items"][0]["status"] == "fail" and r["ok"]


# ---------------------------------------------------------------- suggestions
def test_every_suggestion_fixes_its_finding_without_creating_an_error():
    broken = rover(power={"regulator": ""})
    r = evaluate(broken)
    err = next(f for f in r["findings"] if f["id"] == "E05" and f["severity"] == "error")
    cands = err["fix"]["candidates"]
    assert [c["part"] for c in cands] == ["mt3608_boost_module"]                                              # the LM2596 buck cannot start from a 4 V pack
    fixed = evaluate(apply_patch(broken, cands[0]["patch"]), suggest=False)
    assert fixed["ok"] and not [f for f in fixed["findings"] if f["severity"] == "error"]
    assert cands[0]["errors"] == 0


def test_a_part_that_would_cause_a_new_error_is_never_offered():
    r = evaluate(rover(sensors=[]), "sewer_star")
    cams = [c["part"] for c in by_id(r, "T01")[0]["fix"]["candidates"]]
    assert cams == ["esp32cam_stream_board"]                                                                  # ov2640_on_esp32cam is built onto a different board (E08)
    cam_build = apply_patch(rover(sensors=[]), by_id(r, "T01")[0]["fix"]["candidates"][0]["patch"])
    assert cam_build["sensors"][0]["name"] == "camera1" and cam_build["sensors"][0]["mount"]["height"] > 0
    fixed = evaluate(cam_build, "sewer_star")
    assert not by_id(fixed, "T01") and fixed["task_ok"]


def test_parts_you_own_come_first(user_dirs):
    r = evaluate(rover(lamp=None), "sewer_star")
    lamps = [c["part"] for c in by_id(r, "T03")[0]["fix"]["candidates"]]
    assert set(lamps) == {"led_headlamp_small", "led_headlamp_bright"}
    save_inventory({"led_headlamp_bright": 1})
    r = evaluate(rover(lamp=None), "sewer_star")
    cands = by_id(r, "T03")[0]["fix"]["candidates"]
    assert cands[0]["part"] == "led_headlamp_bright" and cands[0]["owned"] == 1


def test_the_gearing_suggestion_for_a_rover_that_cannot_start():
    heavy = rover(chassis={"frame_mass_kg": 25.0})
    r = evaluate(heavy)
    m2 = by_id(r, "M02")
    assert m2 and m2[0]["fix"]["kind"] == "motor_dc"
    for c in m2[0]["fix"].get("candidates", []):
        assert not [f for f in evaluate(apply_patch(heavy, c["patch"]), suggest=False)["findings"] if f["id"] == "M02"]


def test_patches_cover_every_kind_and_never_mutate_the_original():
    b = rover()
    snapshot = copy.deepcopy(b)
    for name, path in (("tt_motor_6v", "drive.motor"), ("wheel_85mm", "drive.wheel"), ("l298n_module", "drive.driver"), ("li_ion_18650_25r", "power.battery"),
                       ("esp32cam_ai_thinker", "controller"), ("lm2596_buck_module", "power.regulator")):
        p = make_patch(b, get_part(name))
        assert p == {"set": path, "value": name}
    assert make_patch(b, get_part("led_headlamp_bright"))["value"] == {"part": "led_headlamp_bright", "count": 1}
    imu = make_patch(b, get_part("mpu6050"))
    out = apply_patch(b, imu)
    assert out["sensors"][-1]["part"] == "mpu6050" and len(out["sensors"]) == 3 and len(b["sensors"]) == 2 and b == snapshot
    assert make_patch(b, get_part("hc_sr04"))["value"]["name"] == "range1"
    assert candidates(b, {"id": "X", "severity": "warn", "fix": {"action": "change_value", "kind": "chassis"}}, lambda x: []) == []


def test_the_api_carries_the_chosen_task_and_lists_the_tasks():
    pytest.importorskip("mujoco")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from simlab.dashboard.build_router import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        lib = c.get("/api/build/library").json()
        names = {t["name"] for t in lib["tasks"]["ground"]}
        assert {"goto_open", "sewer_star", "search_targets", "pipe_star"} <= names and "waypoints_easy" not in names        # only tasks a ground vehicle can do
        assert "waypoints_easy" in {t["name"] for t in lib["tasks"]["air"]} and "goto_open" not in {t["name"] for t in lib["tasks"]["air"]}
        r = c.post("/api/build/evaluate", json={"build": ROVER, "task": "sewer_star"}).json()
        assert r["task_ok"] and r["fit"]["task"] == "sewer_star"
        assert c.post("/api/build/evaluate", json={"build": ROVER}).json()["fit"] is None


def test_a_fix_that_leaves_the_build_completely_clean_is_still_offered():
    bare = rover(power={"regulator": ""}, sensors=[], lamp=None)                      # nothing else to warn about: the boost converter is the only fix needed
    cands = next(f for f in evaluate(bare)["findings"] if f["id"] == "E05" and f["severity"] == "error")["fix"]["candidates"]
    assert [c["part"] for c in cands] == ["mt3608_boost_module"]
    assert evaluate(apply_patch(bare, cands[0]["patch"]), suggest=False)["findings"] == []
