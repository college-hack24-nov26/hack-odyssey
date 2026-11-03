"""Tests as data: the operations that edit them, clamps, undo, replay onto another base, staleness and the way back to a catalogue run_sweep can run."""
import copy

import pytest

from simlab.chat import cases as C
from simlab.hazards.synth import synthesize
from simlab.llm import menu

WATER = {"range_m": 150, "minutes": 60, "wave_hs": 1.0, "wind_ms": 8, "payload_kg": 0}
GROUND = {"pipe_d": 0.3, "distance_m": 50, "wet": 1}


def water() -> C.CaseBook:
    return C.CaseBook("water", C.draft_plan("water", WATER, "a boat"), "catalogue", machine_sig="m1")


def ground() -> C.CaseBook:
    return C.CaseBook("ground", C.draft_plan("ground", GROUND, "a rover"), "catalogue", machine_sig="m1")


def levels(book, hid="waves"):
    return book.for_hazard(hid)["sweep"]["levels"]


# ------------------------------------------------------------------ the draft
def test_the_draft_has_every_catalogue_test_and_the_asked_level_is_one_of_the_levels():
    b = water()
    assert [c["sweep"]["hazard"] for c in b.cases if c["kind"] == "sweep"] == ["waves", "wind", "current", "debris"][:len([1 for c in b.cases if c["kind"] == "sweep"])]
    assert 1.0 in levels(b)                                       # the job's own 1 m waves are tested, not just the catalogue's ladder
    assert all(c["pass"]["owner"] == "code" and c["pass"]["value"] == pytest.approx(b.m["threshold"]) for c in b.testable())
    assert b.task["params"]["range_m"] == 150                    # the test course is as long as the job says
    for c in b.testable():
        assert c["source"] == "catalogue" and c["tries"] == {"start": 0, "n": C.DEFAULT_TRIES}


def test_with_no_edits_the_cases_make_exactly_the_catalogue_synthesize_makes():
    plan = C.draft_plan("water", WATER, "a boat")
    b = C.CaseBook("water", plan)
    cat, asked = b.to_catalogue()
    want, want_asked = synthesize(plan, "water")
    assert cat == want and asked == want_asked


def test_the_cases_convert_back_to_a_stress_plan():
    b = water()
    sp = b.stress_plan()
    assert {h["id"] for h in sp["hazards"]} == set(menu.load("water")["hazards"]) and set(sp["faults"]) == set(menu.load("water")["faults"])
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0, 0.5, 1.2]}])
    assert next(h for h in b.stress_plan()["hazards"] if h["id"] == "waves")["levels"] == [0, 0.5, 1.2]


# ------------------------------------------------------------------ clamps and ordering
def test_values_are_clamped_to_the_menu_ranges_and_the_clamp_is_reported():
    b = water()
    out = b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0, 0.5, 1.0, 9.0]}])
    assert levels(b)[-1] == 1.5 and any("limited to 1.5 m" in c and "maximum" in c for c in out.clamps)
    out = b.apply([{"op": "set_tries", "case": None, "n": 99}])
    assert all(c["tries"]["n"] == 20 for c in b.testable()) and out.clamps and "1 to 20" in out.clamps[0]
    out = b.apply([{"op": "set_tries", "case": "tc_waves", "n": 0}])
    assert b.get("tc_waves")["tries"]["n"] == 1 and out.clamps
    out = b.apply([{"op": "set_job_number", "name": "range_m", "value": 99999}])
    assert b.params["range_m"] == menu.PARAMS["range_m"][1] and out.clamps
    out = b.apply([{"op": "set_bar", "case": None, "value": 1.7}])
    assert all(c["pass"]["value"] == 1.0 for c in b.testable()) and out.clamps
    for lo_hi in (b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [-3, 0.4, 0.8]}]),):
        assert lo_hi.clamps and levels(b)[0] == 0.0               # the first level stays the catalogue's mild one


def test_levels_stay_mild_to_severe_with_at_least_three():
    b = water()
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [1.2, 0.3, 0.9]}])
    lv = levels(b)
    assert lv == sorted(lv) and lv[0] == 0.0 and len(lv) >= 3 and {0.3, 0.9, 1.2} <= set(lv)
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0.4]}])
    assert len(levels(b)) >= 3                                    # padded from the catalogue's own ladder, never fewer
    g = ground()
    g.apply([{"op": "set_levels", "case": "tc_grip", "levels": [0.1, 0.5, 0.05]}])
    lv = levels(g, "grip")
    assert lv == sorted(lv, reverse=True) and lv[0] == 0.8        # descending test: mild is high, severe is low


def test_adding_a_level_inserts_it_in_severity_order():
    b = water()
    n = len(levels(b))
    out = b.apply([{"op": "add_sweep", "hazard": "waves", "levels": [0.75]}])
    assert out.applied and len(levels(b)) == n + 1 and levels(b) == sorted(levels(b)) and 0.75 in levels(b)
    out = b.apply([{"op": "add_sweep", "hazard": "waves", "levels": [0.75]}])
    assert not out.applied and out.failed and "already" in out.failed[0][1]


def test_only_the_allowed_names_are_accepted():
    b = water()
    assert not b.apply([{"op": "set_task_param", "name": "max_time", "value": 5}]).applied              # not in menu.TASK_PARAMS
    assert b.apply([{"op": "set_task_param", "name": "debris", "value": 12}]).applied and b.task["params"]["debris"] == 12
    assert not b.apply([{"op": "set_job_number", "name": "pipe_d", "value": 0.5}]).applied               # a ground number on a boat
    assert not b.apply([{"op": "add_sweep", "hazard": "grip"}]).applied                                  # not a tough condition of a boat
    assert not b.apply([{"op": "add_fixed", "fault": "left_motor_dead"}]).applied
    assert not b.apply([{"op": "teleport"}]).applied
    out = b.apply([{"op": "set_levels", "case": "tc_nope", "levels": [1, 2, 3]}])
    assert not out.applied and out.failed


def test_a_custom_combination_is_checked_against_the_knobs_the_platform_has():
    b = water()
    out = b.apply([{"op": "add_fixed", "conditions": {"wave_hs": 0.8, "wind_ms": 12}, "title": "Rough and windy"}])
    assert out.applied
    c = next(x for x in b.cases if x["kind"] == "fixed" and x["fixed"]["fault"] is None)
    assert c["fixed"]["conditions"] == {"wave_hs": 0.8, "wind_ms": 12.0} and c["source"] == "user" and c["title"] == "Rough and windy"
    cat, _ = b.to_catalogue()
    extra = [f for f in cat["faults"] if f["id"] == c["id"]]
    assert extra and extra[0]["hazards"] == {"wave_hs": 0.8, "wind_ms": 12.0}
    out = b.apply([{"op": "add_fixed", "conditions": {"grip_mu": 0.2}}])               # grip is not a knob a boat has
    assert not out.applied and "cannot be set" in out.failed[0][1]
    out = b.apply([{"op": "add_fixed", "conditions": {"wave_hs": 9, "wind_ms": 99}}])
    assert out.applied and out.clamps and len(out.clamps) == 2
    c2 = [x for x in b.cases if x["kind"] == "fixed" and x["fixed"]["fault"] is None][-1]
    assert c2["fixed"]["conditions"] == {"wave_hs": 1.5, "wind_ms": 25.0}
    assert not b.apply([{"op": "add_fixed", "conditions": {}}]).applied


def test_the_bar_a_person_sets_is_marked_as_theirs():
    b = water()
    b.apply([{"op": "set_bar", "case": "tc_waves", "value": 0.6}])
    c = b.get("tc_waves")
    assert c["pass"] == {"metric": "success_rate", "op": ">=", "value": 0.6, "owner": "user"} and "bar" in c["touched"]
    assert b.get("tc_wind")["pass"]["owner"] == "code"
    m = C.results_matrix(b)
    row = next(r for r in m["rows"] if r["case_id"] == "tc_waves")
    assert "you set this" in row["bar"]["text"] and m["bar_text"].startswith("set per test")


# ------------------------------------------------------------------ untestable tests
def test_an_untestable_test_never_runs_and_always_counts_as_not_tested():
    b = water()
    n0 = len(b.not_tested_items())
    out = b.apply([{"op": "add_untestable", "title": "Rain", "reason": "Rain is not simulated here.", "key": "rain"}])
    assert out.applied
    c = b.get("tc_n_rain")
    assert c["status"] == "untestable" and c["untestable_reason"] and c["watchable"] is False
    assert len(b.not_tested_items()) == n0 + 1 and "Rain" in b.not_tested_items()[0]
    assert "tc_n_rain" not in [i for g in b.groups() for i in g["cases"]]
    cat, _ = b.to_catalogue()
    assert any("Rain" in t for t in cat["not_tested"])
    assert b.summary()["untestable"] == 1
    cells = b.cells(c)
    assert len(cells) == 1 and cells[0]["state"] == "not_tested"
    assert not b.apply([{"op": "add_untestable", "title": "Rain"}]).applied               # once is enough


def test_a_scenario_the_ai_marked_untestable_arrives_as_an_untestable_test():
    plan = C.draft_plan("water", WATER, "a boat")
    plan["scenarios"] = [{"name": "Night search", "why": "needs a camera model", "hazard": None}, {"name": "Waves", "why": "", "hazard": "waves"}]
    b = C.CaseBook("water", plan, "ai")
    assert [c["title"] for c in b.untestable()] == ["Night search"]


# ------------------------------------------------------------------ undo and redo
def test_undo_and_redo_restore_the_plan_exactly():
    b = water()
    before = copy.deepcopy(b.cases)
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0, 0.5, 1.2]}], "waves")
    b.apply([{"op": "set_tries", "case": None, "n": 9}], "tries")
    after = copy.deepcopy(b.cases)
    assert b.undo() == "tries" and b.get("tc_waves")["tries"]["n"] == C.DEFAULT_TRIES and levels(b)[-1] == 1.2
    assert b.undo() == "waves" and b.cases == before
    assert b.undo() is None and not b.can_undo()
    assert b.redo() == "waves" and b.redo() == "tries" and b.cases == after and b.redo() is None
    b.undo()
    b.apply([{"op": "remove", "case": "tc_wind"}])              # a new edit after an undo drops the redo branch
    assert not b.can_redo() and b.get("tc_wind") is None


def test_undo_is_twenty_deep():
    b = water()
    for i in range(25):
        b.apply([{"op": "set_tries", "case": None, "n": 2 + 3 * (i % 2)}], f"e{i}")                 # 2, 5, 2, 5 ...: every edit changes something
    n = 0
    while b.undo():
        n += 1
    assert n == C.UNDO_DEPTH == 20


def test_one_turn_is_one_undo_step():
    b = water()
    b.apply([{"op": "set_tries", "case": None, "n": 7}, {"op": "set_bar", "case": None, "value": 0.5}], "two things")
    assert b.undo() == "two things" and b.get("tc_waves")["tries"]["n"] == C.DEFAULT_TRIES and b.get("tc_waves")["pass"]["owner"] == "code"


def test_a_job_number_edit_is_undone_with_its_effects():
    b = water()
    b.apply([{"op": "set_job_number", "name": "range_m", "value": 400}])
    assert b.params["range_m"] == 400 and b.task["params"]["range_m"] == 400 and "range_m" in b.touched_params
    b.undo()
    assert b.params["range_m"] == 150 and b.task["params"]["range_m"] == 150 and not b.touched_params


# ------------------------------------------------------------------ switching the base (the AI's plan) and replaying the person's edits
def test_the_persons_edits_are_replayed_on_the_new_base_and_marked():
    b = water()
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0, 0.5, 1.2]}, {"op": "set_bar", "case": "tc_waves", "value": 0.6}], "mine")
    b.apply([{"op": "add_untestable", "title": "Fog", "reason": "no fog here", "key": "fog"}], "fog")
    ai = C.draft_plan("water", {**WATER, "wave_hs": 0.6}, "ai plan")
    ai["stress"]["hazards"] = [{"id": "waves", "levels": [0.0, 0.3, 0.6], "ask": 0.6}]
    ai["stress"]["faults"] = ["thruster_out"]
    failed = b.rebase(ai, "ai", keep_edits=True)
    assert failed == [] and b.source == "ai"
    waves = b.get("tc_waves")
    assert {0.5, 1.2} <= set(waves["sweep"]["levels"]) and {"levels", "bar"} <= set(waves["touched"]) and waves["pass"]["owner"] == "user"
    assert b.get("tc_n_fog") is not None
    assert [c["sweep"]["hazard"] for c in b.cases if c["kind"] == "sweep"] == ["waves"]      # the AI's plan has only waves
    # dropping the edits gives the plain AI plan
    b.rebase(ai, "ai", keep_edits=False)
    assert levels(b) == [0.0, 0.3, 0.6] and b.get("tc_n_fog") is None and not b.history


def test_an_edit_that_no_longer_applies_to_the_new_base_is_listed_not_lost():
    b = water()
    b.apply([{"op": "set_levels", "case": "tc_wind", "levels": [0, 6, 12]}], "wind")
    ai = C.draft_plan("water", WATER, "ai")
    ai["stress"]["hazards"] = [{"id": "waves", "levels": [0.0, 0.3, 0.6], "ask": None}]
    failed = b.rebase(ai, "ai", keep_edits=True)
    assert len(failed) == 1 and failed[0]["op"]["case"] == "tc_wind" and failed[0]["why"]


def test_the_book_survives_being_saved_and_loaded():
    b = water()
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0, 0.5, 1.2]}, {"op": "add_untestable", "title": "Rain", "key": "rain"}], "x")
    b.undo()
    b.redo()
    b2 = C.CaseBook.from_dict(copy.deepcopy(b.to_dict()))
    assert b2.cases == b.cases and b2.cursor == b.cursor and b2.params == b.params


# ------------------------------------------------------------------ results and staleness
def fake_result(book, cid, bar=None):
    c = book.get(cid)
    vals = c["sweep"]["levels"] if c["kind"] == "sweep" else [None]
    per = [{"value": v, "success": 1.0, "ci": [0.4, 1.0], "n": c["tries"]["n"], "crashes": {}, "timeouts": 0, "mean_time": 9.0} for v in vals]
    book.record("r1", cid, per, book.case_sig(c), bar if bar is not None else c["pass"]["value"], len(vals))


def test_a_result_goes_stale_when_what_it_ran_on_changes_and_nothing_re_runs():
    b = water()
    fake_result(b, "tc_waves")
    c = b.get("tc_waves")
    assert c["status"] == "done" and not c["stale"] and c["last_run"]["verdict"] == "met"
    assert not b.any_stale()
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0, 0.5, 1.2]}])
    assert b.get("tc_waves")["stale"] and b.any_stale() and b.get("tc_waves")["last_run"] is not None          # still there, marked
    b.undo()
    assert not b.get("tc_waves")["stale"]                                                                       # undo makes it fresh again
    for op in ({"op": "set_tries", "case": "tc_waves", "n": 9}, {"op": "set_bar", "case": "tc_waves", "value": 0.5}, {"op": "set_task_param", "name": "debris", "value": 5}):
        b.apply([op])
        assert b.get("tc_waves")["stale"], op
        b.undo()
    b.set_machine("m2")                                             # another design: every result is out of date
    assert all(c["stale"] for c in b.testable() if c.get("last_run"))
    assert b.summary()["stale"] == 1


def test_the_input_signature_covers_machine_task_levels_and_bar():
    b = water()
    s0 = b.get("tc_waves")["input_sig"]
    b.set_machine("other")
    assert b.get("tc_waves")["input_sig"] != s0
    b.set_machine("m1")
    assert b.get("tc_waves")["input_sig"] == s0


def test_the_results_matrix_counts_cells_and_leaves_untestable_out_of_the_total():
    b = water()
    b.apply([{"op": "add_untestable", "title": "Rain", "key": "rain"}])
    fake_result(b, "tc_waves")
    m = C.results_matrix(b)
    n_waves = len(levels(b))
    assert m["met"] == n_waves and m["below"] == 0 and m["total"] == b.n_conditions() and m["not_run"] == m["total"] - n_waves
    rain = next(r for r in m["rows"] if r["case_id"] == "tc_n_rain")
    assert rain["untestable"] and rain["cells"][0]["state"] == "not_tested"
    only = C.results_matrix(b, ids=["tc_waves"])
    assert [r["case_id"] for r in only["rows"]] == ["tc_waves"] and only["not_run"] == 0


def test_cells_are_labelled_for_screen_readers_and_show_a_run_in_progress():
    b = water()
    cell = b.cells(b.get("tc_waves"), overlay={"tc_waves": {0.0: "running", 0.15: "queued"}})[:2]
    assert [c["state"] for c in cell] == ["running", "queued"] and "running" in cell[0]["text"] and "Waves" in cell[0]["text"]
    fake_result(b, "tc_waves")
    c = b.cells(b.get("tc_waves"))[0]
    assert c["state"] == "met" and "met bar" in c["text"] and f"{c['wins']} of {c['n']} tries" in c["text"]


# ------------------------------------------------------------------ running: groups, subset
def test_cases_with_the_same_course_tries_and_bar_run_together():
    b = water()
    assert len(b.groups()) == 1
    b.apply([{"op": "set_tries", "case": "tc_waves", "n": 8}])
    groups = b.groups()
    assert len(groups) == 2 and {g["episodes"] for g in groups} == {C.DEFAULT_TRIES, 8}
    assert sum(len(g["cases"]) for g in groups) == len(b.testable())
    only = b.groups(["tc_waves"])
    assert len(only) == 1 and [h["id"] for h in only[0]["cat"]["hazards"]] == ["waves"] and only[0]["cat"]["faults"] == []


def test_the_watchable_flag_follows_the_live_knobs():
    b = water()
    assert b.get("tc_waves")["watchable"] and b.get("tc_wind")["watchable"]
    assert not b.get("tc_debris")["watchable"] and b.get("tc_debris")["watch_reason"]                  # debris is a course setting
    assert not b.get("tc_f_thruster_out")["watchable"]                                                 # a failure is not a live knob
    b.apply([{"op": "add_fixed", "conditions": {"wave_hs": 0.5, "wind_ms": 5}}])
    assert [c for c in b.cases if c["kind"] == "fixed" and c["fixed"]["fault"] is None][0]["watchable"]


def test_a_subset_run_gives_the_same_numbers_as_the_full_run_for_the_same_conditions():
    pytest.importorskip("mujoco")
    from simlab.hazards.sweep import run_sweep
    from simlab.parts import Build, build_to_vehicle
    from simlab.parts.templates import example_builds
    from simlab.goals import propose
    r = propose("water_rescue", owned={}, params={"range_m": 60, "minutes": 10, "wave_hs": 0.5, "wind_ms": 4, "payload_kg": 0})
    spec, _ = build_to_vehicle(Build.from_dict(r["proposals"][0]["build"]))
    b = C.CaseBook("water", C.draft_plan("water", {"range_m": 60, "wave_hs": 0.3}, "x"))
    b.apply([{"op": "set_levels", "case": "tc_waves", "levels": [0, 0.15, 0.3]}, {"op": "set_tries", "case": None, "n": 2}])
    cat, asked = b.to_catalogue([b.get("tc_waves")])
    ctx = {"max_accel": float(spec.params.get("max_accel", 0.5)), "thrusters": 1}
    full = run_sweep(spec, cat, ctx, asked, 2, per_seed=True)
    part = run_sweep(spec, cat, ctx, asked, 2, per_seed=True, subset=[("hazard", "waves", 0.15)])
    assert [lv["value"] for lv in part["hazards"][0]["levels"]] == [0.15]
    a = next(lv for lv in full["hazards"][0]["levels"] if lv["value"] == 0.15)
    p = part["hazards"][0]["levels"][0]
    assert a["success"] == p["success"] and a["per_seed"] == p["per_seed"] and a["mean_time"] == p["mean_time"]
