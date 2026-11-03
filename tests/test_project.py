"""simlab/project: the store, the sensor ladder, the equipment list and its comparison, the named environment, run records and the stage tracker.
Offline: a temp folder, the real parts library, no hardware and no simulator."""
import json
import threading

import pytest

from simlab.parts.templates import example_builds
from simlab.project import ProjectError, ProjectService, ProjectStore, STAGES, environment, equipment, sensors, stages
from simlab.project.store import ID_RE

KIT = {"esp32_devkit": 1, "esp32cam_gc2145_board": 1, "tt_motor_6v": 2, "wheel_65mm_tt": 2, "drv8833_module_pw": 1, "aa_nimh_2000": 4}
ROVER = example_builds()["esp32_pipe_rover"]
ENV = {"name": "tile floor, tape line, two bags, one box", "surface": "tile floor", "objects": [{"name": "bag", "count": 2}, "box"], "notes": "tape line down the middle"}


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMLAB_INVENTORY", str(tmp_path / "inventory.yaml"))          # never read or write the user's real inventory
    return ProjectService(ProjectStore(tmp_path / "projects"))


def make(svc, **kw) -> str:
    return svc.create("Bag rover", **kw)["id"]


def with_blueprint(svc, pid, **extra):
    return svc.update(pid, {"blueprint": {"build": ROVER, "goal": "sewer_inspect"}, **extra})


# ------------------------------------------------------------------ store
def test_ids_are_twelve_hex_and_can_never_name_a_path_outside_the_folder(tmp_path):
    st = ProjectStore(tmp_path)
    assert ID_RE.match(st.new_id())
    for bad in ("..", "../x", "a" * 11, "A" * 12, "g" * 12, "", None, 5, "a" * 12 + "/.."):
        assert not st.valid_id(bad)
        with pytest.raises(KeyError):
            st.folder(bad)


def test_a_project_round_trips_and_leaves_no_temp_files_behind(svc):
    pid = make(svc, text="inspect a 30 cm pipe")
    again = svc.get(pid)
    assert again["name"] == "Bag rover" and again["requirements"]["text"] == "inspect a 30 cm pipe"
    names = {f.name for f in svc.store.folder(pid).iterdir()}
    assert names == {"project.json"}
    json.loads((svc.store.folder(pid) / "project.json").read_text(encoding="utf-8"))


def test_a_stray_temp_file_is_ignored_and_a_missing_project_is_a_keyerror(svc):
    pid = make(svc)
    (svc.store.folder(pid) / "project.json.tmp-1-2-abc").write_text("{broken", encoding="utf-8")
    assert svc.get(pid)["name"] == "Bag rover" and [p["id"] for p in svc.list()] == [pid]
    with pytest.raises(KeyError):
        svc.get("0" * 12)


def test_a_damaged_project_is_reported_not_replaced_and_still_listed_for_deleting(svc):
    pid = make(svc)
    (svc.store.folder(pid) / "project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="damaged"):
        svc.get(pid)
    listed = svc.list()
    assert listed[0]["id"] == pid and listed[0]["damaged"] is True
    svc.delete(pid)
    assert svc.list() == []


def test_the_list_is_newest_first_and_a_cut_run_line_ends_the_log(svc):
    a, b = make(svc), make(svc)
    svc.update(a, {"name": "touched last"})
    assert [p["id"] for p in svc.list()] == [a, b]
    svc.update(a, {"environment": ENV})
    svc.record_run(a, "sim", "first")
    with open(svc.store.folder(a) / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write('{"id":"x","kind":"sim","sum')                                         # a crash in the middle of a line
    assert [r["summary"] for r in svc.runs(a)] == ["first"]


# ------------------------------------------------------------------ create
def test_a_new_project_starts_from_the_saved_inventory_and_at_the_first_stage(svc, tmp_path):
    (tmp_path / "inventory.yaml").write_text("esp32_devkit: 2\nhc_sr04: 1\n", encoding="utf-8")
    v = svc.create("", moving=True)
    assert v["name"] == "Untitled project" and v["owned"] == {"esp32_devkit": 2, "hc_sr04": 1}
    assert v["stages"]["current"] == "requirements" and [s["id"] for s in v["stages"]["stages"]] == list(STAGES)
    assert v["sensors"] == {n: {**v["sensors"][n], "level": "unknown"} for n in sensors.SENSORS}


def test_owned_given_at_creation_is_checked_against_the_library(svc):
    assert svc.create("x", owned={"esp32_devkit": 1})["owned"] == {"esp32_devkit": 1}
    with pytest.raises(ProjectError, match="unknown part"):
        svc.create("x", owned={"flux_capacitor": 1})


# ------------------------------------------------------------------ the sensor ladder
def test_a_sensor_is_never_verified_without_the_explicit_confirm(svc):
    pid = make(svc)
    with pytest.raises(ProjectError, match="confirm"):
        svc.update(pid, {"sensors": {"hcsr04": "verified"}})
    with pytest.raises(ProjectError, match="confirm"):
        svc.update(pid, {"sensors": {"hcsr04": {"level": "verified", "note": "trust me"}}})
    assert svc.view(pid)["sensors"]["hcsr04"]["level"] == "unknown"
    with pytest.raises(ProjectError, match="fitted first"):                           # not wired in yet: nothing to verify
        svc.confirm_sensor(pid, "hcsr04", confirm=True)
    svc.update(pid, {"sensors": {"hcsr04": {"level": "fitted", "note": "TRIG on GPIO18"}}})
    for not_true in (False, None, "yes", 1):                                           # only the real boolean True counts
        with pytest.raises(ProjectError, match="confirm must be true"):
            svc.confirm_sensor(pid, "hcsr04", confirm=not_true)
    assert svc.view(pid)["sensors"]["hcsr04"]["level"] == "fitted"
    v = svc.confirm_sensor(pid, "hcsr04", confirm=True, note="saw 12 to 200 cm")
    assert v["sensors"]["hcsr04"]["level"] == "verified" and v["sensors"]["hcsr04"]["usable_for_safety"] is True
    assert v["sensors"]["camera"]["usable_for_safety"] is False


def test_only_fitted_sensors_are_unknown_to_the_safety_rule_and_lowering_always_works(svc):
    pid = make(svc)
    svc.update(pid, {"sensors": {"mpu6050": "fitted"}})
    assert svc.view(pid)["sensors"]["mpu6050"]["usable_for_safety"] is False
    svc.confirm_sensor(pid, "mpu6050", confirm=True)
    v = svc.update(pid, {"sensors": {"mpu6050": {"level": "fitted", "note": "moved to GPIO21/22, check again"}}})        # wiring changed: verification is lost
    assert v["sensors"]["mpu6050"]["level"] == "fitted" and not v["sensors"]["mpu6050"]["usable_for_safety"]
    assert svc.update(pid, {"sensors": {"mpu6050": "unknown"}})["sensors"]["mpu6050"]["level"] == "unknown"


def test_unknown_sensors_and_levels_are_refused(svc):
    pid = make(svc)
    for bad in ({"ir": "fitted"}, {"camera": "great"}, {"camera": 3}, "camera", ["camera"]):
        with pytest.raises(ProjectError):
            svc.update(pid, {"sensors": bad})
    with pytest.raises(ProjectError, match="unknown sensor"):
        svc.confirm_sensor(pid, "ir", confirm=True)


def test_nothing_but_the_confirm_call_can_raise_a_sensor(svc):
    pid = make(svc)
    svc.update(pid, {"sensors": {n: "fitted" for n in sensors.SENSORS}})
    with_blueprint(svc, pid)                                                           # the blueprint carries the sensors...
    svc.update(pid, {"environment": ENV})
    svc.record_run(pid, "sim", "ran", ok=True)                                         # ...and a run went fine
    svc.record_run(pid, "real", "ran", ok=True, link="usb")
    v = svc.view(pid)
    assert {n: s["level"] for n, s in v["sensors"].items()} == {n: "fitted" for n in sensors.SENSORS}
    assert v["sensors"]["hcsr04"]["in_blueprint"] and v["sensors"]["camera"]["in_blueprint"] and not v["sensors"]["mpu6050"]["in_blueprint"]


def test_a_stored_sensor_block_is_made_safe_on_load(svc):
    pid = make(svc)
    path = svc.store.folder(pid) / "project.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    d["sensors"] = {"camera": {"level": "verified", "note": "x"}, "hcsr04": {"level": "bogus"}, "extra": {"level": "fitted"}}
    path.write_text(json.dumps(d), encoding="utf-8")
    s = svc.get(pid)["sensors"]
    assert set(s) == set(sensors.SENSORS) and s["camera"]["level"] == "verified" and s["hcsr04"]["level"] == "unknown"


# ------------------------------------------------------------------ blueprint
def test_a_blueprint_the_builder_cannot_read_is_refused_and_changes_nothing(svc):
    pid = make(svc)
    bad = {"name": "x y", "platform": "ground"}
    with pytest.raises(ProjectError, match="not a valid build"):
        svc.update(pid, {"blueprint": {"build": bad}})
    with pytest.raises(ProjectError, match="needs a build"):
        svc.update(pid, {"blueprint": {"goal": "sewer_inspect"}})
    assert svc.get(pid)["blueprint"] is None


def test_a_good_blueprint_stores_the_normalised_build_the_goal_and_the_builders_check(svc):
    pid = make(svc)
    v = with_blueprint(svc, pid)
    bp = v["blueprint"]
    assert bp["goal"] == "sewer_inspect" and bp["build"]["name"] == "esp32_pipe_rover" and bp["check"]["ok"] is True and bp["check"]["errors"] == 0
    st = {s["id"]: s["state"] for s in v["stages"]["stages"]}
    assert st["blueprint"] == "done"


def test_a_blueprint_with_a_builder_error_does_not_complete_the_stage(svc):
    pid = make(svc)
    weak = json.loads(json.dumps(ROVER))
    weak["power"].update(battery="aa_nimh_2000", series=1, parallel=1, regulator="")            # one cell straight into a 3.3 V board's supply
    v = svc.update(pid, {"blueprint": {"build": weak}})
    assert v["blueprint"]["check"]["ok"] is False and v["blueprint"]["check"]["errors"] >= 1
    assert {s["id"]: s["state"] for s in v["stages"]["stages"]}["blueprint"] != "done"
    with pytest.raises(ProjectError, match="Builder accepts"):
        svc.confirm_parts(pid)


# ------------------------------------------------------------------ equipment
def test_each_part_is_have_when_owned_enough_else_buy(svc):
    pid = make(svc, owned=KIT)
    rows = {r["name"]: r for r in with_blueprint(svc, pid)["equipment"]["rows"]}
    assert rows["esp32_devkit"]["state"] == "have" and rows["esp32_devkit"]["buy_qty"] == 0
    assert rows["tt_motor_6v"]["state"] == "have" and rows["tt_motor_6v"]["qty"] == 2
    assert rows["hc_sr04"]["state"] == "buy" and rows["hc_sr04"]["buy_qty"] == 1
    assert rows["drv8833_module_pwp"]["state"] == "buy" and rows["drv8833_module_pwp"]["owned"] == 0     # the PW module you own is another part


def test_owning_part_of_the_quantity_still_means_buy_the_rest(svc):
    pid = make(svc, owned={"aa_nimh_2000": 3})
    r = {r["name"]: r for r in with_blueprint(svc, pid)["equipment"]["rows"]}["aa_nimh_2000"]
    assert r["qty"] == 4 and r["state"] == "buy" and r["buy_qty"] == 1


def test_the_cost_counts_only_what_is_left_to_buy_and_names_the_unpriced_parts(svc):
    pid = make(svc, owned=KIT)
    v = with_blueprint(svc, pid, prices={"hc_sr04": 2.5, "drv8833_module_pwp": 4, "esp32_devkit": 9, "tt_motor_6v": 3})
    t = v["equipment"]["totals"]
    assert t["cost"] == 6.5 and t["complete"] is False                                 # devkit and motors are owned: not counted
    assert set(t["unpriced"]) == {"mt3608_boost_module", "esp32cam_stream_board", "led_headlamp_small"}      # only parts still to buy need a price
    prices = {r["name"]: 1.0 for r in v["equipment"]["rows"]}
    t = svc.update(pid, {"prices": prices})["equipment"]["totals"]
    assert t["complete"] is True and t["unpriced"] == [] and t["cost"] == t["to_buy"] * 1.0


def test_prices_are_checked_and_null_clears_one(svc):
    pid = make(svc)
    for bad in ({"hc_sr04": -1}, {"hc_sr04": "cheap"}, {"hc_sr04": True}, {"nope": 1}, [1]):
        with pytest.raises(ProjectError):
            svc.update(pid, {"prices": bad})
    assert svc.update(pid, {"prices": {"hc_sr04": 2, "esp32_devkit": None}})["prices"] == {"hc_sr04": 2.0}


def test_a_have_decision_is_the_users_word_and_is_marked_as_claimed(svc):
    pid = make(svc, owned={})
    with_blueprint(svc, pid)
    v = svc.update(pid, {"decisions": {"hc_sr04": "have"}})
    row = {r["name"]: r for r in v["equipment"]["rows"]}["hc_sr04"]
    assert row["state"] == "have" and row["buy_qty"] == 0 and row["claimed"] is True and row["auto_state"] == "buy"
    assert svc.update(pid, {"decisions": {"hc_sr04": None}})["decisions"] == {}         # null: back to automatic


def test_a_swap_names_another_library_part_of_the_same_kind_and_prices_that_part(svc):
    pid = make(svc, owned={"drv8833_module_pw": 1})
    with_blueprint(svc, pid, prices={"drv8833_module_pwp": 4, "drv8833_module_pw": 5})
    for bad in ({"drv8833_module_pwp": {"state": "swap"}},                                # no target
                {"drv8833_module_pwp": {"state": "swap", "swap_to": "nope"}},
                {"drv8833_module_pwp": {"state": "swap", "swap_to": "hc_sr04"}},         # another kind
                {"drv8833_module_pwp": {"state": "swap", "swap_to": "drv8833_module_pwp"}},
                {"drv8833_module_pwp": {"state": "lend"}},
                {"not_in_this_build": {"state": "have"}}):
        with pytest.raises(ProjectError):
            svc.update(pid, {"decisions": bad})
    v = svc.update(pid, {"decisions": {"drv8833_module_pwp": {"state": "swap", "swap_to": "drv8833_module_pw"}}})
    row = {r["name"]: r for r in v["equipment"]["rows"]}["drv8833_module_pwp"]
    assert row["state"] == "swap" and row["swap_to"] == "drv8833_module_pw" and row["buy_qty"] == 0 and row["line_cost"] == 0.0     # you own the replacement


def test_decisions_before_a_blueprint_are_refused_and_a_bad_field_saves_nothing(svc):
    pid = make(svc)
    with pytest.raises(ProjectError, match="blueprint"):
        svc.update(pid, {"decisions": {"hc_sr04": "have"}})
    with pytest.raises(ProjectError):                                                  # a good name next to a bad environment: neither is saved
        svc.update(pid, {"name": "renamed", "environment": {"surface": "floor"}})
    assert svc.get(pid)["name"] == "Bag rover" and svc.get(pid)["environment"] is None
    with pytest.raises(ProjectError, match="unknown field"):
        svc.update(pid, {"stage": "deploy"})


def test_a_decision_about_a_part_that_left_the_build_goes_with_it_and_parts_must_be_confirmed_again(svc):
    pid = make(svc, owned=KIT)
    with_blueprint(svc, pid)
    svc.update(pid, {"decisions": {"hc_sr04": "have"}})
    assert svc.confirm_parts(pid)["parts_confirmed"] is True
    svc.update(pid, {"decisions": {"hc_sr04": "buy"}})
    assert svc.get(pid)["parts_confirmed"] is False                                   # a changed decision needs a fresh confirm
    svc.confirm_parts(pid)
    no_sonar = json.loads(json.dumps(ROVER))
    no_sonar["sensors"] = [s for s in no_sonar["sensors"] if s["part"] != "hc_sr04"]
    v = svc.update(pid, {"blueprint": {"build": no_sonar}})
    assert "hc_sr04" not in v["decisions"] and v["parts_confirmed"] is False


def test_owned_counts_are_checked(svc):
    pid = make(svc)
    for bad in ({"hc_sr04": -1}, {"hc_sr04": 1.5}, {"hc_sr04": True}, {"nope": 1}, {"hc_sr04": 10000}, 3):
        with pytest.raises(ProjectError):
            svc.update(pid, {"owned": bad})
    assert svc.update(pid, {"owned": {"hc_sr04": 2, "esp32_devkit": 0}})["owned"] == {"hc_sr04": 2}


# ------------------------------------------------------------------ "using what I have" vs "best"
def all_priced(price=5.0):
    from simlab.goals.assembly import bill
    return {r["name"]: price for r in bill(ROVER, {})} | {"drv8833_module_pw": price, "esp32cam_gc2145_board": price}


def test_using_what_i_have_swaps_in_owned_parts_the_builder_accepts_and_says_what_it_costs():
    c = equipment.compare(ROVER, KIT, all_priced())
    swaps = {s["part"]: s for s in c["swaps"]}
    assert swaps["drv8833_module_pwp"]["swap_to"] == "drv8833_module_pw"
    assert swaps["esp32cam_stream_board"]["swap_to"] == "esp32cam_gc2145_board"
    assert "E03" in swaps["drv8833_module_pwp"]["new_warnings"]                       # the cheaper path costs a new warning, and it is shown
    assert c["best"]["totals"]["cost"] == 25.0 and c["using_what_i_have"]["totals"]["cost"] == 15.0 and c["saved"] == 10.0
    assert c["still_to_buy"] == ["mt3608_boost_module", "hc_sr04", "led_headlamp_small"]
    assert c["best"]["check"]["errors"] == 0 and c["using_what_i_have"]["check"]["errors"] == 0
    assert c["using_what_i_have"]["check"]["warnings"] >= c["best"]["check"]["warnings"]
    assert c["note"] == ""


def test_the_saving_is_not_computed_while_a_price_is_missing():
    c = equipment.compare(ROVER, KIT, {"hc_sr04": 2.0})
    assert c["saved"] is None and "have no price" in c["note"] and "mt3608_boost_module" in c["note"]
    assert c["swaps"]                                                                  # the swaps themselves do not need prices


def test_nothing_owned_means_nothing_to_swap_and_the_two_sides_are_the_same_machine():
    c = equipment.compare(ROVER, {}, all_priced())
    assert c["swaps"] == [] and c["saved"] == 0.0
    assert c["best"]["totals"] == c["using_what_i_have"]["totals"]


def test_a_swap_that_would_add_an_error_is_not_offered():
    owned = {"drv8833_module_pwp": 1, "aa_nimh_2000": 1, "lipo_cell_1000_25c": 2}      # one AA cell cannot replace four; a LiPo pair is another kind of pack
    c = equipment.compare(ROVER, owned, all_priced())
    for s in c["swaps"]:
        assert s["part"] != "aa_nimh_2000" or c["using_what_i_have"]["check"]["errors"] == 0
    assert c["using_what_i_have"]["check"]["errors"] <= c["best"]["check"]["errors"]


def test_the_compare_route_needs_a_blueprint_and_works_through_the_service(svc):
    pid = make(svc, owned=KIT)
    with pytest.raises(ProjectError, match="blueprint"):
        svc.compare(pid)
    with_blueprint(svc, pid, prices=all_priced())
    assert svc.compare(pid)["saved"] == 10.0


# ------------------------------------------------------------------ environment
def test_an_environment_is_normalised_and_every_change_bumps_its_version(svc):
    pid = make(svc)
    v = svc.update(pid, {"environment": ENV})
    env = v["environment"]
    assert env["name"] == ENV["name"] and env["surface"] == "tile floor" and env["objects"] == [
        {"name": "bag", "count": 2, "note": ""}, {"name": "box", "count": 1, "note": ""}]
    assert svc.get(pid)["env_version"] == 1
    svc.update(pid, {"environment": {**ENV, "surface": "carpet"}})
    assert svc.get(pid)["env_version"] == 2
    assert v["environment_steps"][0] == "Use this surface: tile floor."
    assert any("Place 2 x bag" in s for s in v["environment_steps"]) and any("tape line" in s for s in v["environment_steps"])
    assert svc.update(pid, {"environment": None})["environment"] is None and svc.update(pid, {"environment": None})["environment_steps"] == []


@pytest.mark.parametrize("bad", [None, "floor", {}, {"name": "  "}, {"name": "x", "objects": "bag"}, {"name": "x", "objects": [{"count": 2}]},
                                 {"name": "x", "objects": [{"name": "bag", "count": 0}]}, {"name": "x", "objects": [{"name": "bag", "count": "two"}]},
                                 {"name": "x", "objects": [{"name": "bag", "count": 100}]}, {"name": "x", "objects": [f"o{i}" for i in range(31)]}])
def test_an_unusable_environment_is_refused_with_a_sentence(bad):
    with pytest.raises(environment.EnvError):
        environment.normalise(bad)


def test_environment_text_is_trimmed_and_collapsed():
    env = environment.normalise({"name": "  tile   floor\n", "surface": "x" * 200, "notes": "a" * 5000})
    assert env["name"] == "tile floor" and len(env["surface"]) == 60 and len(env["notes"]) == environment.MAX_TEXT


# ------------------------------------------------------------------ run records
def test_a_run_needs_a_named_environment_and_stores_its_name(svc):
    pid = make(svc)
    with pytest.raises(ProjectError, match="environment"):
        svc.record_run(pid, "sim", "no scene")
    svc.update(pid, {"environment": ENV})
    r = svc.record_run(pid, "sim", "three laps", ok=True, data={"laps": 3})
    assert r["environment"] == ENV["name"] and r["env_version"] == 1 and r["kind"] == "sim" and r["link"] is None and r["data"] == {"laps": 3}
    svc.update(pid, {"environment": {**ENV, "name": "carpet, one bag"}})
    r2 = svc.record_run(pid, "real", "drove straight", ok=False, link="wifi")
    assert r2["environment"] == "carpet, one bag" and r2["env_version"] == 2 and r2["ok"] is False
    assert [x["environment"] for x in svc.runs(pid)] == [ENV["name"], "carpet, one bag"]            # the first run keeps the name it was measured in
    v = svc.view(pid)
    assert v["run_count"] == 2 and [x["id"] for x in v["runs"]] == [r["id"], r2["id"]]


def test_run_records_are_checked(svc):
    pid = make(svc)
    svc.update(pid, {"environment": ENV})
    for kwargs in ({"kind": "dream"}, {"kind": "real"}, {"kind": "real", "link": "bluetooth"}, {"kind": "sim", "link": "usb"},
                   {"kind": "sim", "ok": "yes"}, {"kind": "sim", "data": {"x": "y" * 30000}}, {"kind": "sim", "data": {"x": object()}}):
        with pytest.raises(ProjectError):
            svc.record_run(pid, **kwargs)
    assert svc.runs(pid) == []


def test_many_runs_recorded_at_once_all_arrive_whole(svc):
    pid = make(svc)
    svc.update(pid, {"environment": ENV})
    threads = [threading.Thread(target=lambda i=i: svc.record_run(pid, "sim", f"run {i}", data={"i": i})) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r["data"]["i"] for r in svc.runs(pid)) == list(range(12))


# ------------------------------------------------------------------ stages
def states(v):
    return {s["id"]: s["state"] for s in v["stages"]["stages"]}


def test_the_stages_follow_the_data_in_order(svc):
    pid = make(svc, owned=KIT)
    assert svc.view(pid)["stages"]["current"] == "requirements"
    v = svc.update(pid, {"requirements": {"text": "follow a bag", "items": ["indoors", "", "under 0.3 m/s"]}})
    assert v["requirements"]["items"] == ["indoors", "under 0.3 m/s"] and v["stages"]["current"] == "blueprint"
    v = with_blueprint(svc, pid)
    assert v["stages"]["current"] == "parts"
    v = svc.confirm_parts(pid)
    assert v["stages"]["current"] == "environment"
    v = svc.update(pid, {"environment": ENV})
    assert v["stages"]["current"] == "simulate"
    svc.record_run(pid, "sim", "ok", ok=True)
    v = svc.view(pid)
    assert v["stages"]["current"] == "connect" and states(v)["simulate"] == "done"
    assert svc.list()[0]["stage"] == "connect"                                         # the list shows it too


def test_a_real_run_on_the_synthetic_rover_is_not_a_test_of_the_hardware(svc):
    pid = make(svc, owned=KIT)
    svc.update(pid, {"requirements": "x", "environment": ENV})
    svc.record_run(pid, "real", "synthetic", link="synthetic")
    assert states(svc.view(pid))["test"] != "done"
    svc.record_run(pid, "real", "usb", link="usb")
    assert states(svc.view(pid))["test"] == "done"


def test_a_stage_without_a_data_trail_can_only_be_marked_in_order(svc):
    pid = make(svc, owned=KIT)
    for stage in ("requirements", "test", "simulate", "bogus", ""):
        with pytest.raises(ProjectError, match="cannot be marked by hand"):
            svc.mark_stage(pid, stage)
    with pytest.raises(ProjectError, match="finish requirements"):
        svc.mark_stage(pid, "connect")
    svc.update(pid, {"requirements": "x", "environment": ENV})
    with_blueprint(svc, pid)
    svc.confirm_parts(pid)
    with pytest.raises(ProjectError, match="finish simulate"):
        svc.mark_stage(pid, "connect")
    svc.record_run(pid, "sim", "ok")
    svc.mark_stage(pid, "connect", "COM5 answers")
    with pytest.raises(ProjectError, match="finish test"):                             # connected is not tested
        svc.mark_stage(pid, "gaps")
    svc.record_run(pid, "real", "drift", link="usb")
    svc.mark_stage(pid, "gaps")
    with pytest.raises(ProjectError, match="finish fix"):
        svc.mark_stage(pid, "deploy")
    svc.mark_stage(pid, "fix", "trim 0.92")
    v = svc.mark_stage(pid, "deploy")
    assert v["stages"]["current"] is None and all(s == "done" for s in states(v).values())
    assert v["marks"]["fix"]["note"] == "trim 0.92" and svc.list()[0]["stage"] == "done"


def test_a_device_that_does_not_move_skips_the_rover_stages(svc):
    pid = make(svc, moving=False)
    svc.update(pid, {"requirements": "x", "environment": ENV})
    with_blueprint(svc, pid)
    svc.confirm_parts(pid)
    svc.record_run(pid, "sim", "bench scene")
    v = svc.view(pid)
    assert states(v)["connect"] == states(v)["test"] == states(v)["gaps"] == states(v)["fix"] == "skipped" and v["stages"]["current"] == "deploy"
    with pytest.raises(ProjectError, match="does not move"):
        svc.mark_stage(pid, "gaps")
    assert svc.mark_stage(pid, "deploy")["stages"]["current"] is None
    assert svc.update(pid, {"moving": True})["stages"]["current"] == "connect"
    with pytest.raises(ProjectError, match="true or false"):
        svc.update(pid, {"moving": "no"})
