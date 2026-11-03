import math

import numpy as np
import pytest

from sewerbot.core import protocol
from sewerbot.core.geometry import Section, fit_check, layout_chamber, ring_panel
from sewerbot.core.mapspec import build_map, load_map
from sewerbot.core.profiles import PRESETS, get_profile, unicycle_to_sides, wheel_layout

MAPS = ["straight_t", "star_five_goals", "large_loop"]


# ------------------------------------------------------------------ geometry
@pytest.mark.parametrize("shape,w,h", [("circular", 0.9, 0.9), ("oval", 0.9, 1.2), ("box", 1.8, 1.5)])
def test_section_is_convex_ccw_and_floor_is_flat(shape, w, h):
    s = Section.make(shape, w, h)
    p = s.poly
    for i in range(len(p)):
        a, b, c = p[i], p[(i + 1) % len(p)], p[(i + 2) % len(p)]
        assert (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) >= -1e-9      # left turns only
    assert s.floor_width > 0.5 * w
    assert np.isclose(p[:, 1].min(), -s.fo)


def test_circular_floor_proportions():
    s = Section.make("circular", 1.0)
    # 1.6 * r with the floor at 0.6 r below the axis; the 28-segment arc sits ~0.3 % inside the ideal circle
    assert math.isclose(s.floor_width, 0.8, rel_tol=1e-6)
    assert math.isclose(s.clear_height, 0.8, abs_tol=0.004)


def test_rays_hit_the_expected_walls():
    s = Section.make("circular", 1.0)
    assert math.isclose(s.radius(math.pi / 2), 0.5, abs_tol=0.004)    # crown (polygonal arc: slightly inside)
    assert math.isclose(s.radius(0.0), 0.5, abs_tol=0.004)            # right wall
    assert math.isclose(s.radius(3 * math.pi / 2), 0.3, rel_tol=1e-6)  # floor is 0.6 r below the axis


def test_boundary_param_roundtrip():
    s = Section.make("oval", 0.9, 1.2)
    for ang in (10, 45, 90, 135, 170, 250, 290):
        pt, nrm = s.point_at(s.boundary_param(ang))
        assert abs(math.degrees(math.atan2(pt[1], pt[0])) % 360 - ang) < 0.5
        assert s.contains(*(pt + nrm * 0.01))                      # inward normal points into the pipe


def test_fit_check_known_cases():
    narrow = Section.make("circular", 0.65)
    assert fit_check(0.12, 0.16, 0.08, narrow)["fits"]
    assert not get_profile("large_6wd").fits(narrow)["fits"]
    assert get_profile("mid_4wd").fits(Section.make("circular", 0.9))["can_turn"]


def test_chamber_gaps_never_exceed_90_degrees():
    ch = layout_chamber([("a", 0.0, 0.45)])
    angs = sorted(f.angle for f in ch.faces)
    gaps = [(angs[(i + 1) % len(angs)] - angs[i]) % (2 * math.pi) for i in range(len(angs))]
    assert max(gaps) <= math.pi / 2 + 1e-9
    assert ch.apothem >= 0.9


def poly_area(pts):
    x, y = np.array(pts).T
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


@pytest.mark.parametrize("shape,w,h", [("circular", 0.9, 0.9), ("box", 1.2, 1.0)])
def test_ring_panel_area_equals_rectangle_minus_hole(shape, w, h):
    sec = Section.make(shape, w, h)
    verts, tris = ring_panel(0.0, 3.0, -0.2, 2.2, sec, 1.5, sec.fo)
    area = sum(poly_area([verts[i] for i in t]) for t in tris)
    assert math.isclose(area, 3.0 * 2.4 - poly_area(sec.poly), rel_tol=1e-3)


# ------------------------------------------------------------------ maps
@pytest.mark.parametrize("name", MAPS)
def test_sample_maps_load_and_goals_reachable(name):
    m = load_map(f"maps/{name}.yaml")
    assert all(m.reachable_goals().values())
    for e in m.edges.values():
        assert e.s_max - e.s_min >= 1.0


def _spec(**over):
    s = {"name": "t", "nodes": {"A": [0, 0], "B": [20, 0], "C": [20, 20]},
         "edges": [{"id": "e1", "from": "A", "to": "B", "diameter": 0.9}, {"id": "e2", "from": "B", "to": "C", "diameter": 0.9}],
         "goals": [{"id": "G", "node": "C"}], "start": {"node": "A"}}
    s.update(over)
    return s


def test_valid_spec_builds():
    assert build_map(_spec()).route("A", "C") == ["A", "B", "C"]


@pytest.mark.parametrize("mut,msg", [
    ({"nodes": {"A": [0, 0], "B": [2, 0], "C": [20, 20]}}, "only"),                       # pipe too short
    ({"goals": [{"id": "G", "node": "Z"}]}, "unknown node"),
    ({"edges": [{"id": "e1", "from": "A", "to": "B", "diameter": 0.9}]}, "no pipes|not reachable"),
    ({"defects": [{"edge": "e1", "s": 500, "type": "crack"}]}, "outside pipe"),
    ({"defects": [{"edge": "e1", "s": 8, "type": "rust"}]}, "defect type"),
])
def test_invalid_specs_are_rejected(mut, msg):
    with pytest.raises(ValueError, match=msg):
        build_map(_spec(**mut))


def test_pipes_leaving_a_chamber_too_close_together_are_rejected():
    spec = _spec(nodes={"A": [0, 0], "B": [20, 0], "C": [20, 4]},
                 edges=[{"id": "e1", "from": "A", "to": "B", "diameter": 0.9}, {"id": "e2", "from": "A", "to": "C", "diameter": 0.9}])
    with pytest.raises(ValueError, match="within"):
        build_map(spec)


def test_route_respects_edge_filter():
    m = load_map("maps/star_five_goals.yaml")
    big = get_profile("large_6wd")
    with pytest.raises(ValueError):
        m.route("H", "GW", lambda e: big.fits(e.section)["fits"])
    assert m.route("H", "GW") == ["H", "GW"]


def test_random_defects_are_deterministic_and_inside_pipes():
    a = load_map("maps/star_five_goals.yaml", defect_seed=3, defect_count=9).defects
    b = load_map("maps/star_five_goals.yaml", defect_seed=3, defect_count=9).defects
    assert [(d.edge, d.s, d.angle) for d in a] == [(d.edge, d.s, d.angle) for d in b] and len(a) == 9


# ------------------------------------------------------------------ profiles
def test_presets_validate_and_have_wheels():
    for name, p in PRESETS.items():
        p.validate()
        assert len(wheel_layout(p)) >= 2
        assert p.camera.height < 0.5


def test_speed_limit_is_enforced():
    with pytest.raises(ValueError):
        get_profile("mid_4wd", max_speed=3.0)


def test_unicycle_mapping_is_symmetric_and_clamped():
    p = get_profile("mid_4wd")
    l, r = unicycle_to_sides(p, 0.3, 0.0)
    assert l == r == 0.3
    l, r = unicycle_to_sides(p, 0.0, 0.5)
    assert math.isclose(l, -r)
    l, r = unicycle_to_sides(p, 5.0, 5.0)
    assert max(abs(l), abs(r)) <= p.max_speed + 1e-9
    # a lower yaw gain asks the wheels for more differential
    q = p.override(yaw_gain=0.5)
    assert abs(unicycle_to_sides(q, 0, 0.3)[1]) > abs(unicycle_to_sides(p.override(yaw_gain=1.0), 0, 0.3)[1])


def test_profile_roundtrip_through_json():
    p = get_profile("tracked_crawler", max_speed=0.3)
    assert type(p).from_dict(p.to_dict()) == p


# ------------------------------------------------------------------ protocol
def test_protocol_reassembles_split_messages():
    msg = protocol.pack({"t": "obs", "x": 1}, b"\xff\xd8jpegbytes\xff\xd9") + protocol.pack({"t": "cmd", "v": 0.1})
    r = protocol.Reader()
    out = []
    for i in range(0, len(msg), 7):                       # deliver in awkward 7-byte fragments
        out += r.feed(msg[i:i + 7])
    assert [h["t"] for h, _ in out] == ["obs", "cmd"]
    assert out[0][1].startswith(b"\xff\xd8")


def test_protocol_rejects_garbage_length():
    with pytest.raises(ValueError):
        protocol.Reader().feed(b"\xff\xff\xff\xff" + b"x" * 10)
