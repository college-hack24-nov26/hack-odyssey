import math

import numpy as np
import pytest

from sewerbot.brain.detectors.base import Detection, iou, nms, score
from sewerbot.brain.navigation import PathFollower, plan_inspect_all, plan_route
from sewerbot.brain.tracker import CameraModel, DefectTracker, PipeFrame, evaluate_against_truth, locate
from sewerbot.core.geometry import Section
from sewerbot.core.imaging import auto_expose
from sewerbot.core.mapspec import load_map
from sewerbot.core.profiles import PRESETS, get_profile


# ------------------------------------------------------------------ detectors / scoring
def test_iou_and_nms():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0
    d = [Detection("crack", 0.9, (0, 0, 10, 10)), Detection("crack", 0.6, (1, 1, 10, 10)), Detection("leak", 0.5, (1, 1, 10, 10))]
    assert len(nms(d)) == 2                                   # same-class duplicate removed, other class kept


def test_score_counts_tp_fp_fn():
    gts = [[{"cls": "crack", "box": [0, 0, 20, 20]}, {"cls": "leak", "box": [50, 50, 20, 20]}]]
    preds = [[Detection("crack", 0.9, (1, 1, 20, 20)), Detection("crack", 0.8, (200, 200, 20, 20))]]
    r = score(preds, gts)
    assert (r["crack"]["tp"], r["crack"]["fp"], r["leak"]["fn"]) == (1, 1, 1)


def test_auto_expose_brightens_dark_frames_and_keeps_bright_ones():
    dark = np.full((60, 80, 3), 12, np.uint8)
    bright = np.full((60, 80, 3), 200, np.uint8)
    assert auto_expose(dark).mean() > 2 * dark.mean()          # brightened, but the gain is capped (10x linear)
    assert auto_expose(dark).mean() < 100                      # ...so pure darkness is never blown up to mid-grey
    assert abs(float(auto_expose(bright).mean()) - 200) < 80


# ------------------------------------------------------------------ ray casting / tracker
def _pipe():
    return PipeFrame("e1", np.array([0.0, 0.0]), np.array([1.0, 0.0]), Section.make("circular", 0.9), 0.9, 17.1)


def test_pixel_rays_land_on_the_right_walls():
    cam = CameraModel.from_profile(get_profile("mid_4wd"))
    cam.tilt_deg = 0
    left = locate(_pipe(), cam, 3.0, 0.0, 0.0, (0, 240, 10, 10))
    right = locate(_pipe(), cam, 3.0, 0.0, 0.0, (630, 240, 10, 10))
    assert 150 < left["angle"] < 230 and (right["angle"] > 310 or right["angle"] < 30)
    up = locate(_pipe(), cam, 3.0, 0.0, 0.0, (320, 5, 10, 10))
    assert up is None or 40 < up["angle"] < 140 or up["range"] > 1.0


def test_known_geometry_roundtrip():
    """Project a wall point into the image, then recover it with locate()."""
    cam = CameraModel.from_profile(get_profile("mid_4wd"))
    sec = Section.make("circular", 0.9)
    pipe = _pipe()
    x, y, yaw = 4.0, 0.05, 0.0
    target = np.array([6.0, -sec.radius(math.radians(150)) * math.cos(math.radians(150)), 0.0])   # on the left wall, s = 6
    r, z = sec.boundary_point(150)
    world = np.array([6.0, -r, sec.fo + z])                      # pipe frame: right = -y
    o = np.array([x + cam.x_off, y, cam.height_m])
    d = world - o
    t = math.radians(cam.tilt_deg)
    xr, yr, zr = d[0], d[1], d[2]
    xc, zc = math.cos(t) * xr - math.sin(t) * zr, math.sin(t) * xr + math.cos(t) * zr    # inverse of the pitch
    px = cam.width / 2 - cam.focal_px * yr / xc
    py = cam.height / 2 - cam.focal_px * zc / xc
    loc = locate(pipe, cam, x, y, yaw, (int(px) - 1, int(py) - 1, 2, 2))
    assert abs(loc["s"] - 6.0) < 0.1 and abs(loc["angle"] - 150) < 3


def test_tracker_merges_repeat_sightings_and_confirms():
    cam = CameraModel.from_profile(get_profile("mid_4wd"))
    tr = DefectTracker(cam, confirm_hits=3)
    det = Detection("crack", 0.7, (100, 150, 40, 40))
    for i in range(5):
        tr.update(i * 0.1, _pipe(), (3.0 + 0.01 * i, 0.0, 0.0), [det])
    assert len(tr.defects) == 1 and tr.defects[0].confirmed and tr.defects[0].hits == 5


def test_tracker_merges_duplicate_tracks_of_one_defect():
    from sewerbot.brain.tracker import TrackedDefect
    tr = DefectTracker(CameraModel.from_profile(get_profile("mid_4wd")))
    tr.defects = [TrackedDefect(1, "crack", "e1", 6.0, 150.0, hits=2, conf=0.5),
                  TrackedDefect(2, "crack", "e1", 6.7, 165.0, hits=5, conf=0.8, confirmed=True),
                  TrackedDefect(3, "leak", "e1", 6.2, 150.0, hits=3),          # other class: must survive
                  TrackedDefect(4, "crack", "e2", 6.1, 150.0, hits=3)]         # other pipe: must survive
    tr._merge()
    assert sorted((d.cls, d.edge) for d in tr.defects) == [("crack", "e1"), ("crack", "e2"), ("leak", "e1")]
    merged = next(d for d in tr.defects if d.cls == "crack" and d.edge == "e1")
    assert merged.hits == 7 and merged.confirmed and 6.0 < merged.s < 6.7


def test_evaluation_matches_by_edge_class_position():
    cam = CameraModel.from_profile(get_profile("mid_4wd"))
    tr = DefectTracker(cam)
    tr.defects = []
    from sewerbot.brain.tracker import TrackedDefect
    tr.defects.append(TrackedDefect(1, "crack", "e1", 6.1, 160.0, confirmed=True))
    tr.defects.append(TrackedDefect(2, "leak", "e1", 11.0, 20.0, confirmed=True))      # false alarm
    truth = [{"edge": "e1", "s": 6.0, "angle": 155.0, "type": "crack"}, {"edge": "e1", "s": 12.5, "angle": 20.0, "type": "leak"}]
    r = evaluate_against_truth(tr.defects, truth)
    assert (r["true_positive"], r["missed"], r["false_positive"]) == (1, 1, 1)


# ------------------------------------------------------------------ navigation (closed loop, kinematic stand-in)
def drive_kinematic(m, p, route, max_steps=40000, dt=0.05):
    f = PathFollower(m, p, route)
    x, y = m.nodes[route[0]].pos
    yaw = math.atan2(m.nodes[route[1]].pos[1] - y, m.nodes[route[1]].pos[0] - x)
    v = w = 0.0
    worst = 0.0
    for _ in range(max_steps):
        cv, cw, st = f.step({"x": x, "y": y, "yaw": yaw})
        if st.state == "arrived":
            return True, worst
        v += max(-p.max_accel * dt, min(p.max_accel * dt, cv - v))
        w += max(-3 * dt, min(3 * dt, cw * p.yaw_gain - w))             # the body only achieves yaw_gain of the command
        x += v * math.cos(yaw) * dt
        y += v * math.sin(yaw) * dt
        yaw += w * dt
        loc = m.locate(x, y)
        if "edge" in loc:      # inside a pipe: how far does the body overhang the drivable floor? (> 0 = wall contact)
            e = m.edges[loc["edge"]]
            half_swept = abs(loc["lateral"]) + 0.5 * (abs(p.width * math.cos(yaw - math.atan2(e.direction[1], e.direction[0])))
                                                      + abs(p.length * math.sin(yaw - math.atan2(e.direction[1], e.direction[0]))))
            worst = max(worst, half_swept - e.section.floor_width / 2)
    return False, worst


@pytest.mark.parametrize("name", ["straight_t", "star_five_goals", "large_loop"])
@pytest.mark.parametrize("rover", sorted(PRESETS))
def test_every_rover_reaches_every_reachable_goal(name, rover):
    m = load_map(f"maps/{name}.yaml")
    p = get_profile(rover)
    for g in m.goals.values():
        try:
            route = plan_route(m, p, m.start_node, g.node)
        except ValueError:
            assert rover == "large_6wd"                                  # only the big rover may be refused (narrow pipes)
            continue
        ok, worst = drive_kinematic(m, p, route)
        assert ok, f"{rover} did not reach {g.id} on {name}"
        assert worst < 0.03, f"{rover} overhung the pipe floor by {worst:.2f} m (wall contact) on the way to {g.id}"


def test_inspect_all_skips_unreachable_goals_for_big_rovers():
    m = load_map("maps/star_five_goals.yaml")
    _, visited, skipped = plan_inspect_all(m, get_profile("large_6wd"), m.start_node)
    assert "GOAL_W" in skipped and len(visited) == 4
    _, visited, skipped = plan_inspect_all(m, get_profile("mid_4wd"), m.start_node)
    assert not skipped and len(visited) == 5


def test_follower_stops_for_obstacles():
    m = load_map("maps/straight_t.yaml")
    f = PathFollower(m, get_profile("mid_4wd"), ["A", "J", "E"])
    v, w, st = f.step({"x": 3.0, "y": 0.0, "yaw": 0.0}, {"front": 0.1})
    assert v == 0 and st.state == "blocked"
