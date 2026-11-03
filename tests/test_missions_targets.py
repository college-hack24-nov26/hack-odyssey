"""Targets: learned from a few photos (stand-ins until the real ones arrive), a hit-rate on those photos, a registry that needs no code to extend."""
import json

import cv2
import numpy as np
import pytest

from simlab.missions.targets import (STANDINS, CameraModel, ColourShapeDetector, Target, TargetError, TargetRegistry, background_photos,
                                     false_alarm_rate, hit_rate, photo_files, read_image, standin_photos, to_frame, _draw_standin, StandIn)


# ---------------------------------------------------------------- the detector, on stand-in photos
@pytest.mark.parametrize("tid", sorted(STANDINS))
def test_detector_finds_each_standin_in_its_own_photos_and_at_a_distance(tid):
    photos = standin_photos(tid, 6, seed=0)
    det = ColourShapeDetector.fit(photos)
    near = hit_rate(det, photos)
    far = hit_rate(det, photos, scales=(0.5, 0.33))                     # the same pictures with the object half and a third as big
    assert near.rate >= 0.99, near.to_dict()
    assert far.rate >= 0.9, far.to_dict()
    fresh = hit_rate(det, standin_photos(tid, 20, seed=11))               # photos it never saw
    assert fresh.rate >= 0.9, fresh.to_dict()


@pytest.mark.parametrize("tid", sorted(STANDINS))
def test_detector_does_not_report_the_object_in_empty_scenes_or_other_objects(tid):
    det = ColourShapeDetector.fit(standin_photos(tid, 6, seed=0))
    assert false_alarm_rate(det, background_photos(16, seed=3)) <= 0.15
    others = [p for o in STANDINS if o != tid for p in standin_photos(o, 8, seed=4)]
    assert false_alarm_rate(det, others) <= 0.2                         # a different colour is a different target (clutter dots may match by chance)


def test_detector_works_on_96x96_frames_and_is_fast():
    import time
    photos = standin_photos("bag_a", 6)
    det = ColourShapeDetector.fit(photos)
    frame = to_frame(photos[0])
    assert max(frame.shape[:2]) == 96
    t0 = time.perf_counter()
    for _ in range(50):
        det.detect(frame)
    assert (time.perf_counter() - t0) / 50 < 0.02                       # well under one 11 fps frame period


def test_a_thin_strip_of_the_right_colour_is_not_the_object():
    det = ColourShapeDetector.fit(standin_photos("bag_a", 6))
    frame = np.full((96, 96, 3), (110, 112, 118), np.uint8)
    frame[60:64, :] = STANDINS["bag_a"].rgb                             # e.g. the rover's own body edge across the bottom of the picture
    assert det.detect(frame) is None
    frame[:] = (110, 112, 118)
    frame[30:70, 30:60] = STANDINS["bag_a"].rgb
    assert det.detect(frame) is not None


def test_a_close_up_that_fills_the_frame_still_counts():
    det = ColourShapeDetector.fit(standin_photos("box", 6))
    frame = np.full((96, 96, 3), STANDINS["box"].rgb, np.uint8)
    b = det.detect(frame)
    assert b is not None and b.clipped


def test_learning_needs_photos_and_a_colour_that_stands_out():
    with pytest.raises(TargetError, match="at least 3 photos"):
        ColourShapeDetector.fit(standin_photos("box", 2))
    grey = [np.full((100, 100, 3), 120, np.uint8) + np.random.default_rng(i).integers(0, 6, (100, 100, 3), dtype=np.uint8) for i in range(3)]
    with pytest.raises(TargetError, match="no colour stands out"):
        ColourShapeDetector.fit(grey)


def test_detector_round_trips_through_json():
    det = ColourShapeDetector.fit(standin_photos("bag_b", 5))
    again = ColourShapeDetector.from_dict(json.loads(json.dumps(det.to_dict())))
    f = to_frame(standin_photos("bag_b", 1, seed=9)[0])
    a, b = det.detect(f), again.detect(f)
    assert a is not None and b is not None and (a.x, a.y, a.w, a.h) == (b.x, b.y, b.w, b.h)


def test_a_new_object_is_just_photos_and_a_size(tmp_path):
    """A yellow cone nobody wrote code for: photos + size are all a target needs."""
    cone = StandIn("Yellow cone", "waypoint", "cylinder", (240, 215, 30), 18.0, 30.0)
    rng = np.random.default_rng(5)
    photos = [_draw_standin(cone, rng) for _ in range(5)]
    reg = TargetRegistry(tmp_path)
    t = reg.add("cone", "Yellow cone", "waypoint", 18, 30, photos)
    assert t.hits["rate"] >= 0.9
    assert "cone" in reg.ids() and set(STANDINS) <= set(reg.ids())
    assert len(photo_files(tmp_path / "cone")) == 5 and (tmp_path / "cone" / "target.json").is_file()
    frame = to_frame(_draw_standin(cone, np.random.default_rng(77)))
    s = reg.get("cone").locate(frame, CameraModel())
    assert s is not None and abs(s.bearing) < 0.25


# ---------------------------------------------------------------- the registry and the target record
def test_registry_round_trip_and_precedence(tmp_path):
    reg = TargetRegistry(tmp_path)
    assert reg.ids() == sorted(STANDINS) and reg.get("bag_a").source == "standin"
    mine = reg.add("bag_a", "My real bag", "follow", 30, 25, standin_photos("bag_b", 4))          # a folder of the same id wins over the stand-in
    assert reg.get("bag_a").name == "My real bag" and reg.get("bag_a").source == "user"
    again = TargetRegistry(tmp_path).get("bag_a")
    assert (again.width_cm, again.height_cm, again.role) == (30, 25, "follow") and again.detector.bins == mine.detector.bins
    reg.remove("bag_a")
    assert reg.get("bag_a").source == "standin"
    with pytest.raises(KeyError, match="unknown target"):
        reg.get("nope")
    with pytest.raises(KeyError):
        reg.get("../etc")


def test_registry_refuses_bad_records_and_writes_nothing(tmp_path):
    reg = TargetRegistry(tmp_path, standins=False)
    for kw, msg in [({"role": "dragon"}, "role"), ({"width_cm": 0.5}, "size_cm"), ({"target_id": "bad id!"}, "id")]:
        args = {"target_id": "x", "name": "x", "role": "goal", "width_cm": 20, "height_cm": 20, "photos": standin_photos("box", 4), **kw}
        with pytest.raises(TargetError, match=msg):
            reg.add(**args)
    with pytest.raises(TargetError, match="at least 3"):
        reg.add("x", "x", "goal", 20, 20, standin_photos("box", 2))
    assert reg.ids() == [] and not list(tmp_path.iterdir())


def test_a_hand_made_folder_of_photos_learns_on_first_use(tmp_path):
    folder = tmp_path / "crate"
    folder.mkdir()
    for i, p in enumerate(standin_photos("box", 4, seed=2)):
        cv2.imwrite(str(folder / f"IMG_{i}.jpg"), cv2.cvtColor(p, cv2.COLOR_RGB2BGR))
    (folder / "target.json").write_text(json.dumps({"id": "crate", "role": "goal", "size_cm": {"width": 30, "height": 25}, "shape": "box"}))
    t = TargetRegistry(tmp_path).get("crate")
    assert t.detector is None
    assert t.ensure_detector().detect(to_frame(read_image(photo_files(folder)[0]))) is not None


def test_targets_directory_can_be_redirected(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMLAB_TARGETS", str(tmp_path))
    assert TargetRegistry().root == tmp_path


def test_distance_and_bearing_come_from_real_size_and_the_camera_model():
    cam = CameraModel(96, 96, 62.0)
    assert cam.focal_px == pytest.approx(79.9, abs=0.1)
    assert cam.distance(0.22, 11.0) == pytest.approx(1.6, abs=0.02)
    assert cam.bearing(48.0) == pytest.approx(0.0) and cam.bearing(20.0) > 0 > cam.bearing(80.0)      # left of centre = positive (counter-clockwise)
    t = TargetRegistry(standins=True).get("bag_a")
    assert t.detection_range_m(cam) == pytest.approx(cam.focal_px * 0.22 / 6, rel=1e-6) and t.footprint_m == pytest.approx(0.13)
    shape, rgba, size = t.look()
    assert shape == "cylinder" and size == pytest.approx((0.13, 0.11)) and rgba[2] > rgba[0]            # a blue bag


def test_target_to_dict_is_json_and_validates():
    t = TargetRegistry().get("box")
    d = json.loads(json.dumps(t.to_dict()))
    assert d["role"] == "goal" and d["size_cm"] == {"width": 28.0, "height": 20.0} and d["hit_rate"]["rate"] >= 0.99
    d["role"] = "boss"
    with pytest.raises(TargetError, match="role"):
        Target.from_dict(d)
