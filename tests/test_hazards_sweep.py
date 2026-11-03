"""The stress-test engine: catalogues, level logic, a real ground sweep, cancellation and the report."""
import json
import threading

import pytest

pytest.importorskip("mujoco")
from simlab import get_vehicle  # noqa: E402
from simlab.hazards import load_catalogue, render, run_sweep, write_report  # noqa: E402
from simlab.hazards.sweep import _hazard, _weakest, conditions  # noqa: E402

KNOBS = {"grip_mu", "drag_ms2", "side_gain", "cmd_delay_s", "cmd_dropout", "runtime_s", "energy_frac", "wind_ms", "rotor_eff", "cold",
         "wave_hs", "wave_tp", "current_ms", "thruster_eff", "rudder_stuck"}


@pytest.mark.parametrize("name", ["sewer", "water", "air"])
def test_catalogues_are_valid_and_every_knob_is_one_the_plants_understand(name):
    cat = load_catalogue(name)
    assert cat["platform"] in ("ground", "water", "air") and cat["not_tested"] and 0.5 <= cat["threshold"] <= 0.95
    for h in cat["hazards"]:
        assert ("knob" in h) != ("task_param" in h)
        assert h.get("knob", "grip_mu") in KNOBS and len(h["levels"]) >= 3
        assert h["plain"] and h["source"] and h["fix"]
    for f in cat["faults"]:
        assert set(f["hazards"]) <= KNOBS and f["plain"] and f["fix"]
    assert load_catalogue.__name__ and pytest.raises(KeyError, load_catalogue, "nope")


def test_conditions_scale_and_resolve_the_thruster_fault():
    cat = load_catalogue("water")
    one = {c["id"]: c for c in conditions(cat, {"thrusters": 1}) if c["kind"] == "fault"}
    two = {c["id"]: c for c in conditions(cat, {"thrusters": 2}) if c["kind"] == "fault"}
    assert one["thruster_out"]["hazards"]["thruster_eff"] == 0.0 and two["thruster_out"]["hazards"]["thruster_eff"] == 0.5
    mud = [c for c in conditions(load_catalogue("sewer"), {"max_accel": 0.5}) if c["id"] == "resistance"]
    assert mud[-1]["hazards"]["drag_ms2"] == pytest.approx(0.97 * 0.5)
    debris = [c for c in conditions(cat, {}) if c["id"] == "debris"]
    assert [c["task"]["debris"] for c in debris] == [0, 6, 15, 30] and not debris[0]["hazards"]


def lv(value, success, n=5):
    return {"value": value, "success": success, "ci": [0, 1], "n": n, "crashes": {}, "timeouts": 0, "mean_time": 1.0}


def test_breaking_point_asked_level_and_a_reference_that_already_fails():
    h = {"id": "w", "title": "Waves", "plain": "p", "unit": "m", "ask": "wave_hs", "levels": [0, 0.3, 0.6, 0.9], "fix": "f"}
    res = {("hazard", "w", v): r for v, r in ((0, lv(0, 1.0)), (0.3, lv(0.3, 1.0)), (0.6, lv(0.6, 0.4)), (0.9, lv(0.9, 0.0)))}
    out = _hazard(h, res, 0.8, {"wave_hs": 0.3})
    assert out["breaks_at"] == 0.6 and out["last_ok"] == 0.3 and out["meets_ask"] is True and "covered" in out["summary"]
    out = _hazard(h, res, 0.8, {"wave_hs": 0.5})                   # 0.5 sits between levels: the next level up (0.6) must pass
    assert out["meets_ask"] is False and "NOT covered" in out["summary"]
    assert _weakest([out], [])["id"] == "w"
    flat = {("hazard", "w", v): lv(v, 1.0) for v in h["levels"]}
    assert _hazard(h, flat, 0.8, {})["breaks_at"] is None and "every level" in _hazard(h, flat, 0.8, {})["summary"]
    broken = {("hazard", "w", v): lv(v, 0.2) for v in h["levels"]}
    assert _hazard(h, broken, 0.8, {})["reference_fails"] is True


def test_a_real_ground_sweep_is_repeatable_and_reports_what_it_did_not_test():
    cat = load_catalogue("sewer")
    cat["hazards"] = [h for h in cat["hazards"] if h["id"] in ("grip", "resistance")]
    cat["hazards"][0]["levels"] = [0.8, 0.05]
    cat["hazards"][1]["levels"] = [0.0, 0.97]
    cat["faults"] = cat["faults"][:1]
    spec = get_vehicle("mid_4wd")
    seen = []
    a = run_sweep(spec, cat, {"max_accel": spec.params["max_accel"], "thrusters": 1}, {}, episodes=2, progress=lambda d, t, l: seen.append((d, t)))
    b = run_sweep(spec, cat, {"max_accel": spec.params["max_accel"], "thrusters": 1}, {}, episodes=2)
    assert seen[-1] == (len(seen), len(seen)) and seen[0][1] == 5
    strip = lambda r: [[(lv["value"], lv["success"]) for lv in h["levels"]] for h in r["hazards"]]          # noqa: E731
    assert strip(a) == strip(b)                                                                           # same seeds: same answers
    grip, mud = a["hazards"]
    assert grip["levels"][0]["success"] == 1.0 and mud["levels"][0]["success"] == 1.0                      # calm works
    assert mud["levels"][1]["success"] < 1.0 and mud["breaks_at"] == 0.97 and "starts to fail" in mud["summary"]
    assert a["not_tested"] and "not a guarantee" in a["statement"] and a["weakest"] and not a["partial"]
    json.dumps(a)


def test_cancel_stops_early_and_says_so():
    cat = load_catalogue("sewer")
    stop = threading.Event()
    stop.set()
    r = run_sweep(get_vehicle("mid_4wd"), cat, {"max_accel": 0.5}, {}, episodes=1, cancel=stop)
    assert r["partial"] and all(not h["levels"] for h in r["hazards"])


def test_the_report_lists_what_was_not_tested_never_claims_safety_and_escapes_text(tmp_path):
    cat = load_catalogue("air")
    cat["hazards"][0]["levels"] = [0, 12]
    cat["faults"] = []
    r = run_sweep(get_vehicle("quad_250"), cat, {}, {"wind_ms": 8}, episodes=2)
    page = render(r, "<script>alert(1)</script>", {"name": "x", "parts": ["a&b"]})
    low = page.lower()
    assert "not tested" in low and "&lt;script&gt;" in page and "<script>" not in page and "a&amp;b" in page
    assert " safe" not in low and "safety" not in low and "not a guarantee" in low
    name = write_report(r, "air_search", "Watch from the air", {"name": "x", "parts": []}, root=tmp_path)
    assert (tmp_path / name / "report.html").is_file() and json.loads((tmp_path / name / "report.json").read_text())["goal"] == "air_search"
