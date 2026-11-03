"""LabRunner: the live-run thread behind the dashboard's Lab tab."""
import time

import pytest

pytest.importorskip("mujoco")

from simlab import get_task_spec, get_vehicle  # noqa: E402
from simlab.runtime import LabRunner  # noqa: E402


def wait_for(cond, timeout=10.0, what="condition"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def runner():
    r = LabRunner()
    yield r
    r.stop()


def start(r, **kw):
    r.start(get_vehicle("mid_4wd"), get_task_spec("goto_open"), speed=kw.pop("speed", 0), **kw)
    wait_for(lambda: r.state().get("status") == "running" and r.state().get("poses"), what="the run to start")


def test_runner_publishes_state_and_scene(runner):
    start(runner)
    s = runner.state()
    assert s["running"] and s["vehicle"] == "mid_4wd" and s["policy"] == "baseline"
    assert len(s["poses"]) == len(runner.scene()["bodies"]) and runner.scene()["id"] == s["scene_id"]
    t0 = s["t"]
    wait_for(lambda: runner.state()["t"] > t0 + 1.0, what="simulated time to advance")
    runner.stop()
    assert runner.state() == {"running": False, "status": "idle"}


def test_a_human_command_takes_over_and_stops_when_quiet(runner):
    start(runner, speed=1.0)
    assert runner.command({"v": 0.3, "w": 0.0}) == "manual"
    for _ in range(8):                                               # a held key re-sends every 100 ms
        runner.command({"v": 0.3, "w": 0.0})
        time.sleep(0.1)
    wait_for(lambda: runner.state()["policy"] == "manual", what="manual takeover")
    assert runner.state()["cmd"]["v"] == pytest.approx(0.3)
    time.sleep(0.9)                                                  # keys released: the failsafe stops the vehicle
    wait_for(lambda: runner.state()["speed"] < 0.05, what="the vehicle to stop")


def test_episodes_loop_and_stats_accumulate(runner):
    start(runner, speed=0)                                           # as fast as possible
    wait_for(lambda: runner.state()["stats"]["episodes"] >= 2, timeout=30, what="two episodes")
    st = runner.state()["stats"]
    assert st["successes"] >= 1 and st["mean_time"] is not None
    assert runner.state()["result"] is None or runner.state()["result"]["t"] > 0


def test_camera_frames_are_rendered_only_while_someone_watches(runner):
    start(runner, speed=1.0)
    time.sleep(0.5)
    assert runner.jpeg() is None                                     # nobody is watching: no rendering cost
    runner.video_watch(+1)
    wait_for(lambda: runner.jpeg() is not None, what="a camera frame")
    assert runner.jpeg()[:2] == b"\xff\xd8"
    runner.video_watch(-1)


def test_slowing_down_after_a_fast_run_stays_responsive(runner):
    """Going from 'max' to real time must not leave the loop sleeping off the simulated time it already ran."""
    start(runner, speed=0)
    wait_for(lambda: runner.state()["t"] > 15 or runner.state()["stats"]["episodes"] >= 1, what="a fast run")
    runner.set_speed(1.0)
    time.sleep(0.2)
    t0 = time.time()
    runner.command({"v": 0.3, "w": 0.0})
    wait_for(lambda: runner.state()["policy"] == "manual", timeout=2.0, what="takeover within 2 s of slowing down")
    assert time.time() - t0 < 2.0
    runner.reset()
    wait_for(lambda: runner.state()["t"] < 1.0, timeout=2.0, what="a reset within 2 s")


def test_speed_and_reset_requests(runner):
    start(runner, speed=1.0)
    runner.set_speed(0)
    t0 = runner.state()["t"]
    wait_for(lambda: runner.state()["t"] > t0 + 3, what="fast forward")
    runner.reset()
    wait_for(lambda: runner.state()["t"] < 1.0, what="a new episode")


def test_a_bad_backend_reports_an_error_instead_of_crashing(runner):
    runner.start(get_vehicle("mid_4wd"), get_task_spec("goto_open"), backend="no_such_backend")
    wait_for(lambda: runner.state().get("status") == "error", what="the error state")
    assert "no_such_backend" in runner.state()["error"]
