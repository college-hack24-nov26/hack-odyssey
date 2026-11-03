"""Missions as data: a closed vocabulary, validated; and the drive commands (left duty, right duty, ms) the runner emits."""
import json

import pytest

from simlab.missions import Drive, DriveModel, Mission, MissionError, STEP_OPS, TargetRegistry, step
from simlab.missions.runner import STOP

BAG_TO_BOX = {"name": "bag to bag to box", "steps": [
    {"op": "go_to", "target": "bag_a", "stop_at_cm": 30},
    {"op": "go_to", "target": "bag_b", "stop_at_cm": 30},
    {"op": "stop_at", "target": "box", "within_cm": 35}]}


# ---------------------------------------------------------------- the vocabulary
def test_the_vocabulary_is_closed_and_each_step_has_limits():
    assert set(STEP_OPS) == {"go_to", "follow", "orbit", "stop_at"}
    for op, (doc, params) in STEP_OPS.items():
        names = {p.name for p in params}
        assert doc and {"target", "max_time_s", "max_distance_cm"} <= names, op


def test_a_mission_is_json_and_round_trips_with_defaults_filled_in():
    m = Mission.from_dict(BAG_TO_BOX)
    d = json.loads(json.dumps(m.to_dict()))
    assert Mission.from_dict(d).to_dict() == m.to_dict()
    assert d["steps"][0]["max_time_s"] == 0.0 and d["steps"][0]["stop_at_cm"] == 30.0           # every parameter is explicit in the data
    assert m.targets() == ["bag_a", "bag_b", "box"]
    assert m.describe()[0] == "go to bag_a and stop 30 cm from it" and m.describe()[-1].startswith("stop at box")


def test_laps_and_orbit_data():
    m = Mission.from_dict({"name": "laps", "steps": [{"op": "orbit", "target": "bag_a", "radius_cm": 40, "laps": 3}, {"op": "stop_at", "target": "box"}]})
    assert m.steps[0].laps == 3 and m.steps[0].direction == "ccw" and m.steps[1].within_cm == 35.0


@pytest.mark.parametrize("bad, field, text", [
    ({"steps": [{"op": "fly", "target": "bag_a"}]}, "steps[0].op", "unknown step"),
    ({"steps": [{"op": "go_to", "target": "bag_a", "speed": 9}]}, "steps[0].speed", "unknown parameter"),
    ({"steps": [{"op": "go_to"}]}, "steps[0].target", "needs the id"),
    ({"steps": [{"op": "go_to", "target": "bag_a", "stop_at_cm": 2}]}, "steps[0].stop_at_cm", "below the minimum"),
    ({"steps": [{"op": "orbit", "target": "bag_a", "direction": "up"}]}, "steps[0].direction", "must be one of"),
    ({"steps": [{"op": "orbit", "target": "bag_a", "laps": 99}]}, "steps[0].laps", "above the maximum"),
    ({"steps": [{"op": "stop_at", "target": "box"}, {"op": "go_to", "target": "bag_a"}]}, "steps[0]", "must be the last step"),
    ({"steps": []}, "steps", "at least one step"),
    ({"steps": [{"op": "go_to", "target": "bag_a"}], "shell": "rm -rf /"}, "shell", "unknown top-level key"),
    ({"steps": [{"op": "go_to", "target": "bag_a"}] * 13}, "steps", "at most"),
])
def test_invalid_missions_are_refused_with_the_field_and_a_plain_reason(bad, field, text):
    m, issues = Mission.parse(bad)
    assert m is None
    hit = [i for i in issues if i.field == field and text in i.msg]
    assert hit, [str(i) for i in issues]
    with pytest.raises(MissionError, match=text):
        Mission.from_dict(bad)


def test_non_mappings_do_not_crash_the_parser():
    for junk in (None, "go_to bag", 3, [], {"steps": "go"}, {"steps": [None, 3]}):
        m, issues = Mission.parse(junk)
        assert m is None and issues


def test_targets_are_checked_against_the_registry_and_roles_warn_not_fail():
    reg = TargetRegistry(standins=True)
    m, issues = Mission.parse({"steps": [{"op": "go_to", "target": "dragon"}]}, reg)
    assert m is None and "no target 'dragon'" in issues[0].msg
    m, issues = Mission.parse({"steps": [{"op": "stop_at", "target": "bag_a"}]}, reg)         # a bag is not a goal: unusual, not forbidden
    assert m is not None and [i.level for i in issues] == ["warn"]
    m, issues = Mission.parse(BAG_TO_BOX, reg)
    assert m is not None and not issues


def test_step_helper_validates():
    s = step("orbit", target="bag_a", radius_cm=50, laps=2)
    assert s.op == "orbit" and s.radius_cm == 50 and s.to_dict()["laps"] == 2
    with pytest.raises(MissionError):
        step("orbit", target="bag_a", radius_cm=5)


def test_every_step_has_a_time_and_distance_limit_even_when_none_is_given():
    for raw in BAG_TO_BOX["steps"] + [{"op": "orbit", "target": "bag_a", "laps": 2}, {"op": "follow", "target": "bag_a", "duration_s": 30}]:
        m = Mission.from_dict({"steps": [raw]})
        t, d = m.steps[0].limits()
        assert t > 0 and d > 0
    assert step("go_to", target="bag_a", max_time_s=10, max_distance_cm=150).limits() == (10.0, 1.5)
    assert step("orbit", target="bag_a", laps=2).limits()[0] > step("orbit", target="bag_a", laps=1).limits()[0]
    assert step("follow", target="bag_a", duration_s=30).limits()[0] > 30


# ---------------------------------------------------------------- drive commands
def test_a_drive_command_is_the_bounded_serial_line():
    d = Drive(120, -120, 250)
    assert d.line() == "D 120 -120 250" and not d.stopped and STOP.stopped and Drive(0, 0, 100).stopped


def test_drive_commands_are_always_bounded_and_respect_the_minimum_duty():
    model = DriveModel()
    for v in (-0.3, -0.1, -0.02, 0.0, 0.004, 0.02, 0.08, 0.15, 0.4):
        for w in (-3.0, -1.0, -0.2, 0.0, 0.05, 0.5, 1.5, 4.0):
            d = model.drive(v, w, 0.5)
            assert -255 <= d.left <= 255 and -255 <= d.right <= 255 and 0 <= d.ms <= model.max_ms
            for duty in (d.left, d.right):
                assert duty == 0 or abs(duty) >= model.min_duty                              # the firmware would raise anything lower anyway
            assert (d.ms == 0) == (d.left == 0 and d.right == 0)


def test_slow_motion_is_a_shorter_pulse_at_minimum_duty():
    model = DriveModel()
    slow, fast = model.drive(0.02, 0.0, 0.5), model.drive(0.12, 0.0, 0.5)
    assert slow.left == slow.right == model.min_duty and slow.ms < fast.ms
    assert fast.left == fast.right > model.min_duty and fast.ms == model.max_ms
    # same average speed either way: speed x pulse / tick
    for d, want in ((slow, 0.02), (fast, 0.12)):
        assert model.speed(d.left) * d.ms / 500.0 == pytest.approx(want, rel=0.12)


def test_turning_and_arcs_have_the_right_signs():
    model = DriveModel()
    left = model.drive(0.0, 1.0, 0.5)                      # spin counter-clockwise: the right wheel forward, the left back
    assert left.right > 0 > left.left
    arc = model.drive(0.1, 0.3, 0.5)                       # veering left: the right wheel runs faster
    assert arc.right > arc.left > 0
    assert model.drive(-0.1, 0.0, 0.5).left < 0
    assert model.distance(model.drive(0.1, 0.0, 0.5)) == pytest.approx(0.05, rel=0.15)
    assert model.distance(model.drive(0.0, 1.0, 0.5)) == 0.0                                 # a pivot goes nowhere


def test_speed_scale_is_a_calibration():
    nominal, measured = DriveModel(), DriveModel(scale=0.8)                  # the real rover covers 80 % of the nominal distance
    d = nominal.drive(0.1, 0.0, 0.5)
    assert measured.distance(d) == pytest.approx(0.8 * nominal.distance(d))
    assert measured.drive(0.1, 0.0, 0.5).left >= d.left                      # and so is asked for more duty for the same speed
