"""run_sweep streams one result per finished condition (the chat shows them as they arrive), and the chat's watch resolves conditions on the server."""
import importlib
import threading

import pytest

pytest.importorskip("mujoco")
from simlab.chat import words  # noqa: E402
from simlab.goals import propose  # noqa: E402
from simlab.hazards import load_catalogue, run_sweep, write_report  # noqa: E402
from simlab.hazards import sweep as sweep_mod  # noqa: E402
from simlab.hazards.sweep import conditions  # noqa: E402
from simlab.llm import menu  # noqa: E402
from simlab.parts import Build, build_to_vehicle  # noqa: E402
from tests.chat_helpers import FakeRunner, PIPE, SweepFake, make_engine, say  # noqa: E402

lab_router = importlib.import_module("simlab.dashboard.router")       # `from simlab.dashboard import router` is the APIRouter object, not this module
CTX = {"max_accel": 0.5, "thrusters": 2}


def cat():
    c = load_catalogue("water")
    c = {**c, "hazards": [dict(h) for h in c["hazards"]][:2], "faults": c["faults"][:1]}
    return c


def spec():
    r = propose("water_rescue", owned={}, params={"range_m": 60, "minutes": 10, "wave_hs": 0.5, "wind_ms": 4, "payload_kg": 0})
    s, _ = build_to_vehicle(Build.from_dict(r["proposals"][0]["build"]))
    return s


# ------------------------------------------------------------------ the stream
def test_default_call_gives_exactly_todays_dict(monkeypatch):
    SweepFake(monkeypatch)
    out = run_sweep(None, cat(), CTX, {"ask_waves": 0.3}, 2)
    assert set(out) == {"platform", "task", "threshold", "episodes", "baseline", "hazards", "faults", "weakest", "not_tested", "partial", "seconds", "statement"}
    assert all("per_seed" not in lv for h in out["hazards"] for lv in h["levels"]) and all("per_seed" not in f for f in out["faults"])
    assert out["partial"] is False and out["episodes"] == 2
    n = len(conditions(cat(), CTX))
    assert sum(len(h["levels"]) for h in out["hazards"]) + len(out["faults"]) == n


def test_on_result_fires_once_per_condition_in_order_with_per_seed_outcomes(monkeypatch):
    SweepFake(monkeypatch)
    seen, order = [], []
    out = run_sweep(None, cat(), CTX, {}, 3, progress=lambda d, t, label: order.append(("progress", d)),
                    on_result=lambda c, r, d, t: (seen.append((c, r, d, t)), order.append(("result", d))), per_seed=True)
    conds = conditions(cat(), CTX)
    assert [(c["kind"], c["id"], c["value"]) for c, *_ in seen] == [(c["kind"], c["id"], c["value"]) for c in conds]
    assert [d for *_, d, _ in seen] == list(range(1, len(conds) + 1)) and {t for *_, t in seen} == {len(conds)}
    for c, r, d, t in seen:
        assert [s["seed"] for s in r["per_seed"]] == [0, 1, 2] and all(set(s) == {"seed", "success", "t", "crash", "timeout"} for s in r["per_seed"])
    assert order[:4] == [("progress", 1), ("result", 1), ("progress", 2), ("result", 2)]       # progress first, then the new callback
    assert all("per_seed" in lv for h in out["hazards"] for lv in h["levels"])                  # per_seed=True keeps them in the final result too


def test_stopping_is_honoured_between_conditions(monkeypatch):
    SweepFake(monkeypatch)
    stop, seen = threading.Event(), []

    def on_result(c, r, d, t):
        seen.append(d)
        if d == 2:
            stop.set()
    out = run_sweep(None, cat(), CTX, {}, 2, cancel=stop, on_result=on_result)
    assert seen == [1, 2] and out["partial"] is True
    assert sum(len(h["levels"]) for h in out["hazards"]) == 2                       # the finished conditions are kept, the rest are simply absent


def test_a_subset_runs_only_what_it_lists_and_a_wildcard_means_every_level(monkeypatch):
    fake = SweepFake(monkeypatch)
    c = cat()
    hid = c["hazards"][0]["id"]
    out = run_sweep(None, c, CTX, {}, 1, subset=[("hazard", hid, c["hazards"][0]["levels"][1])])
    assert [(k, i, v) for k, i, v, _ in fake.calls] == [("hazard", hid, c["hazards"][0]["levels"][1])] and len(out["hazards"][0]["levels"]) == 1
    fake.calls.clear()
    run_sweep(None, c, CTX, {}, 1, subset=[("hazard", hid, None)])
    assert len(fake.calls) == len(c["hazards"][0]["levels"]) and {i for _, i, _, _ in fake.calls} == {hid}
    fake.calls.clear()
    run_sweep(None, c, CTX, {}, 1, subset=[("fault", c["faults"][0]["id"], None)])
    assert [(k, i) for k, i, _, _ in fake.calls] == [("fault", c["faults"][0]["id"])]
    fake.calls.clear()
    run_sweep(None, c, CTX, {}, 1, subset=[])
    assert fake.calls == []


def test_a_level_typed_as_a_float_matches_the_catalogue_level(monkeypatch):
    fake = SweepFake(monkeypatch)
    c = cat()
    hid, lv = c["hazards"][0]["id"], c["hazards"][0]["levels"][2]
    run_sweep(None, c, CTX, {}, 1, subset=[("hazard", hid, float(f"{lv:.9f}"))])
    assert len(fake.calls) == 1


# ------------------------------------------------------------------ reports
def test_two_reports_in_the_same_second_do_not_share_a_folder(monkeypatch, tmp_path):
    SweepFake(monkeypatch)
    out = run_sweep(None, cat(), CTX, {}, 1)
    names = {write_report(out, "water_rescue", "Rescue", {"name": "x", "parts": []}, root=tmp_path) for _ in range(4)}
    assert len(names) == 4 and all((tmp_path / n / "report.html").is_file() for n in names)


# ------------------------------------------------------------------ the real simulator: a watched try versus a sweep try
def test_a_lab_try_and_a_sweep_try_with_the_same_seed_and_conditions_agree():
    """The chat labels a watched run 'a similar run, not the exact failed try'. This pins what is known: the same seed and conditions give the same outcome in the
    Lab's way of building the run (hazards at construction) and the sweep's (set_hazards)."""
    from simlab.control import make_policy
    from simlab.envs import Sim
    from simlab.hazards.sweep import _task
    from simlab.train.evaluate import run_episode
    s = spec()
    c = load_catalogue("water")
    task = _task(c, {})
    hz = {"wave_hs": 0.6}
    a = Sim(s, task)
    a.set_hazards(hz)
    ra = run_episode(a, make_policy("baseline", a), 0)
    a.close()
    b = Sim(s, task, "mujoco", seed=0, hazards=hz)
    rb = run_episode(b, make_policy("baseline", b), 0)
    b.close()
    assert (ra["success"], round(ra["t"], 2), ra["crash"]) == (rb["success"], round(rb["t"], 2), rb["crash"])


def test_the_limited_wording_is_what_the_chat_says_about_a_watched_run():
    assert "similar run" in words.WATCH_SIMILAR.lower() and "not the exact failed try" in words.WATCH_SIMILAR
    assert "exact" not in words.watch_started("waves 0.9 m", "user").replace("not the exact failed try", "")


# ------------------------------------------------------------------ what the Lab accepts, and the chat's conversion
def test_the_lab_drops_faults_debris_and_unknown_knobs_so_the_chat_calls_them_not_watchable():
    cleaned = lab_router._clean_hazards({"wave_hs": 9.0, "side_gain": 0.5, "rotor_eff": 0.5, "debris": 30, "thruster_eff": 0.5, "rudder_stuck": 1, "wind_ms": 0.0, "nonsense": 3})
    assert cleaned == {"wave_hs": menu.RANGES["wave_hs"][1]}
    for k in ("side_gain", "rotor_eff", "thruster_eff", "rudder_stuck", "debris"):
        assert k not in lab_router.LIVE_HAZARDS


def test_a_watch_converts_mud_from_a_share_to_the_labs_absolute_unit_on_the_server(tmp_path, monkeypatch):
    runner = FakeRunner()
    eng = make_engine(tmp_path, monkeypatch, runner=runner)
    try:
        chat = eng.new_chat()
        say(eng, chat, PIPE)
        chat.wait_idle(30)
        ok = [e for e in chat.view.cards["watch"]["runs"]]
        assert ok and ok[0]["source"] == "auto"
        say(eng, chat, "watch the worst mud")
        kw = runner.starts[-1]
        assert "vehicle" in kw and kw["loop"] is False and kw["policy"] == "baseline" and kw["seed"] == 0       # try 1 is seed 0, as in a sweep
        top = chat.book.for_hazard("resistance")["sweep"]["levels"][-1]
        max_accel = float(chat.s["proposals"][0]["vehicle"]["params"].get("max_accel", 0.5))
        assert kw["hazards"]["drag_ms2"] == pytest.approx(min(20.0, top * max_accel), rel=1e-3)
        assert chat.view.cards["watch"]["runs"][-1]["conditions"]["drag_ms2"] == pytest.approx(top * max_accel, rel=1e-3)
    finally:
        eng.close()
