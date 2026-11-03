"""simlab spec layer: parameters, command spaces, vehicle kinds, presets, protocol, compatibility with sewerbot."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

import simlab
from simlab import VehicleSpec, get_vehicle, save_vehicle, vehicle_names
from simlab.protocol import cmd_header, hello_caps, parse_cmd
from simlab.registry import Registry
from simlab.spec import WEBOTS_CAPS, Channel, CommandSpace, Param, SpecError, TaskSpec, apply_schema
from simlab.vehicles.multirotor import allocation_matrix, mix, rotor_layout, rotors

FIXTURES = Path(__file__).parent / "fixtures"
GROUND = ("micro_2wd", "mid_4wd", "tracked_crawler", "large_6wd")
AIR = ("quad_micro", "quad_250", "hex_450")


# ---------------------------------------------------------------- parameters
def test_param_coercion_and_ranges():
    p = Param("speed", "float", 1.0, 0.1, 5.0, "m/s")
    assert p.coerce("2.5") == 2.5
    for bad in (0.01, 9.0, float("nan"), float("inf"), "fast", None, True):
        with pytest.raises(ValueError):
            p.coerce(bad)
    n = Param("n", "int", 4, 1, 8)
    assert n.coerce(6.0) == 6
    with pytest.raises(ValueError):
        n.coerce(4.5)
    e = Param("mode", "enum", "a", choices=("a", "b"))
    assert e.coerce("b") == "b"
    with pytest.raises(ValueError):
        e.coerce("c")
    b = Param("flag", "bool", False)
    assert b.coerce("yes") is True and b.coerce("0") is False
    with pytest.raises(ValueError):
        b.coerce("maybe")


def test_schema_fills_defaults_and_rejects_unknown_keys():
    schema = (Param("a", "float", 1.0, 0.0, 2.0), Param("b", "int", 3))
    out, issues = apply_schema(schema, {"a": 1.5}, "params")
    assert out == {"a": 1.5, "b": 3} and not issues
    out, issues = apply_schema(schema, {"a": 5.0, "typo": 1}, "params")
    fields = {i.field: i for i in issues}
    assert "params.a" in fields and "params.typo" in fields
    assert "known" in fields["params.typo"].msg


# ---------------------------------------------------------------- command spaces
def test_command_space_clip_dict_and_normalise():
    cs = CommandSpace("u", (Channel("v", -0.5, 0.5, "m/s"), Channel("w", -2.0, 2.0, "rad/s")))
    assert cs.dim == 2 and cs.names == ("v", "w")
    assert np.allclose(cs.vector({"v": 9.0}), [0.5, 0.0])               # clipped, missing channel = 0
    assert np.allclose(cs.vector([0.1, float("nan")]), [0.1, 0.0])       # NaN never reaches an actuator
    with pytest.raises(KeyError):
        cs.vector({"speed": 1.0})
    with pytest.raises(ValueError):
        cs.vector([1.0, 2.0, 3.0])
    v = np.array([0.25, -1.0])
    assert np.allclose(cs.denormalise(cs.normalise(v)), v)
    assert np.allclose(cs.normalise([0.5, -2.0]), [1.0, -1.0])
    assert np.allclose(cs.denormalise([5.0, -5.0]), [0.5, -2.0])         # policy outputs outside [-1, 1] are clipped


def test_one_sided_channel_normalises_to_full_range():
    cs = CommandSpace("m", (Channel("m0", 0.0, 1.0),))
    assert np.allclose(cs.normalise([0.0]), [-1.0]) and np.allclose(cs.denormalise([1.0]), [1.0])


# ---------------------------------------------------------------- registry
def test_registry_errors_are_helpful():
    reg = Registry("thing")

    class A:
        name = "a"

    reg.add(A())
    with pytest.raises(ValueError):
        reg.add(A())
    with pytest.raises(KeyError, match="available"):
        reg.get("zzz")
    assert reg.names() == ["a"] and "a" in reg


# ---------------------------------------------------------------- presets and specs
def test_all_presets_load_and_pass_checks():
    names = vehicle_names()
    assert set(GROUND + AIR) <= set(names)
    for n in names:
        spec = get_vehicle(n)
        errors = [i for i in spec.validate() if i.level == "error"]
        assert not errors, (n, errors)
        assert spec.space().dim >= 2 and spec.derived()


def test_ground_presets_match_the_original_python_literals():
    """The YAML port must reproduce the numbers the Webots pipeline was calibrated with."""
    from sewerbot.core.profiles import PRESETS

    old = json.loads((FIXTURES / "presets_v1.json").read_text())
    assert set(PRESETS) == set(old) == set(GROUND)
    for name, d in old.items():
        assert PRESETS[name].to_dict() == d, name


def test_spec_round_trips_through_yaml_and_profile(tmp_path):
    from sewerbot.core.profiles import PRESETS, profile_from_spec, spec_from_profile

    for n in GROUND:
        spec = get_vehicle(n)
        spec.dump(tmp_path / f"{n}.yaml")
        assert VehicleSpec.load(tmp_path / f"{n}.yaml") == spec
        back = spec_from_profile(profile_from_spec(spec)).to_dict()
        assert back == {**spec.to_dict(), "description": ""}
        assert profile_from_spec(spec) == PRESETS[n]
    with pytest.raises(ValueError, match="multirotor"):
        profile_from_spec(get_vehicle("quad_250"))


def test_override_by_bare_name_and_camera_prefix():
    s = get_vehicle("mid_4wd", mass=5.0, max_speed=0.8, cam_fov_deg=90)
    assert s.body["mass"] == 5.0 and s.params["max_speed"] == 0.8 and s.sensor("camera").options["fov_deg"] == 90
    with pytest.raises(ValueError, match="unknown override"):
        get_vehicle("mid_4wd", warp_drive=9)
    with pytest.raises(SpecError) as e:
        get_vehicle("mid_4wd", wheel_radius=-1)
    assert any(i.field == "params.wheel_radius" for i in e.value.issues)


def test_parse_reports_every_problem_without_raising():
    spec, issues = VehicleSpec.parse({"name": "bad name!", "kind": "ground_wheeled",
                                      "body": {"mass": -1}, "params": {"drive": "hover", "wat": 1},
                                      "sensors": [{"type": "lidar", "name": "x"}, {"type": "camera", "name": "c", "fov_deg": 500}]})
    assert spec is None
    fields = {i.field for i in issues}
    assert {"name", "body.mass", "params.drive", "params.wat", "sensors[0].type", "sensors[1].fov_deg"} <= fields
    spec, issues = VehicleSpec.parse({"name": "x", "kind": "warpship"})
    assert spec is None and "known" in issues[0].msg


def test_user_vehicles_dir_shadows_presets(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMLAB_VEHICLES", str(tmp_path))
    custom = get_vehicle("mid_4wd", mass=9.0).override(name="my_rover")
    path = save_vehicle(custom)
    assert path.parent == tmp_path and "my_rover" in vehicle_names("ground_wheeled")
    assert get_vehicle("my_rover").body["mass"] == 9.0
    with pytest.raises(SpecError):
        save_vehicle(get_vehicle("mid_4wd").override(name="broken", max_speed=2.0).override(wheel_radius=0.4))


# ---------------------------------------------------------------- ground kind
def test_ground_mixer_matches_the_original_formula():
    from sewerbot.core.profiles import PRESETS, unicycle_to_sides

    p = PRESETS["mid_4wd"]
    left, right = unicycle_to_sides(p, 0.3, 0.9)
    half = p.track_width / 2
    assert left == pytest.approx(0.3 - 0.9 / p.yaw_gain * half) and right == pytest.approx(0.3 + 0.9 / p.yaw_gain * half)
    left, right = unicycle_to_sides(p, 5.0, 0.0)                           # clamped to max_speed
    assert left == right == p.max_speed
    left, right = unicycle_to_sides(p, 0.5, 1.5)                           # saturated side scales both, keeps the ratio
    assert max(abs(left), abs(right)) == pytest.approx(p.max_speed)
    assert (right - left) / (right + left) > 0


def test_ground_command_spaces_follow_the_vehicle():
    s = get_vehicle("large_6wd")
    uni = s.space("unicycle")
    assert uni.hi[0] == s.params["max_speed"] and uni.hi[1] == s.params["max_yaw_rate"]
    assert s.default_space == "unicycle" and set(s.spaces()) == {"unicycle", "wheels"}
    with pytest.raises(KeyError):
        s.space("thrust")


def test_backend_caps_limit_ground_speed_and_kind():
    fast = get_vehicle("mid_4wd", max_speed=3.0)
    assert fast.validate() == [] or all(i.level != "error" for i in fast.validate())      # fine in general
    errs = [i for i in fast.validate(WEBOTS_CAPS) if i.level == "error"]
    assert errs and errs[0].field == "params.max_speed"
    drone = [i for i in get_vehicle("quad_250").validate(WEBOTS_CAPS) if i.level == "error"]
    assert drone and drone[0].field == "kind"


def test_wheels_taller_than_body_is_rejected():
    s = get_vehicle("mid_4wd").to_dict()
    s["params"]["wheel_radius"] = 0.2
    spec, issues = VehicleSpec.parse(s)
    assert spec is not None                                               # schema-valid ...
    assert any(i.level == "error" and "wheels taller" in i.msg for i in spec.validate())    # ... but physically wrong


# ---------------------------------------------------------------- multirotor kind
@pytest.mark.parametrize("layout,n", [("quad_x", 4), ("quad_plus", 4), ("hex", 6), ("octo", 8)])
def test_rotor_layouts_are_balanced(layout, n):
    r = rotor_layout(layout, 0.2)
    assert len(r) == n
    assert sum(x for x, _, _ in r) == pytest.approx(0, abs=1e-12) and sum(y for _, y, _ in r) == pytest.approx(0, abs=1e-12)
    assert sum(s for _, _, s in r) == 0                                   # reaction torques cancel in hover
    assert all(math.hypot(x, y) == pytest.approx(0.2) for x, y, _ in r)
    with pytest.raises(ValueError):
        rotor_layout("pentacopter", 0.2)


@pytest.mark.parametrize("layout", ["quad_x", "quad_plus", "hex", "octo"])
def test_allocation_is_full_rank_and_hover_splits_thrust_equally(layout):
    spec = get_vehicle("quad_250", layout=layout, thrust_max=6.0)
    A = allocation_matrix(spec)
    assert np.linalg.matrix_rank(A) == 4
    n = A.shape[1]
    weight = spec.body["mass"] * 9.81
    t = mix(spec, [weight, 0, 0, 0])
    assert np.allclose(t, weight / n)
    # a requested roll torque is realised by the mix without disturbing thrust or yaw
    got = A @ mix(spec, [weight, 0.02, 0, 0])
    assert got == pytest.approx([weight, 0.02, 0, 0], abs=1e-9)
    got = A @ mix(spec, [weight, 0, -0.015, 0.003])
    assert got == pytest.approx([weight, 0, -0.015, 0.003], abs=1e-9)


def test_mix_never_exceeds_the_motors():
    spec = get_vehicle("quad_250")
    t = mix(spec, [1e3, 5.0, -5.0, 1.0])
    assert t.min() >= 0 and t.max() <= spec.params["thrust_max"]


def test_multirotor_designer_checks():
    ok = get_vehicle("quad_250")
    facts = {f.key: f.value for f in ok.derived()}
    assert facts["twr"] == pytest.approx(4 * 4.0 / (0.65 * 9.81))
    assert facts["hover_throttle"] == pytest.approx(100 * 0.65 * 9.81 / 4 / 4.0)
    assert facts["rotors"] == 4 and facts["horizontal_accel"] > 5
    weak = get_vehicle("quad_250", thrust_max=0.8)                        # twr ~0.5: cannot leave the ground
    assert any(i.level == "error" and "hover" in i.msg for i in weak.validate())
    sluggish = get_vehicle("quad_250", thrust_max=2.2)                    # twr ~1.4
    issues = sluggish.validate()
    assert not any(i.level == "error" for i in issues) and any("low" in i.msg for i in issues)
    assert any("hold altitude" in i.msg for i in get_vehicle("quad_250", thrust_max=1.9, max_tilt_deg=60).validate())


def test_multirotor_command_spaces():
    s = get_vehicle("hex_450")
    assert set(s.spaces()) == {"velocity", "position", "attitude_thrust", "motors"} and s.default_space == "velocity"
    assert s.space("motors").dim == 6 == len(rotors(s))
    vel = s.space("velocity")
    assert vel.lo[2] == -s.params["max_descent"] and vel.hi[2] == s.params["max_climb"]
    assert s.space("attitude_thrust").hi[0] == pytest.approx(math.radians(s.params["max_tilt_deg"]))


def test_vehicle_size_range_is_wide():
    mass = sorted(get_vehicle(n).body["mass"] for n in GROUND + AIR)
    assert mass[0] < 0.1 and mass[-1] >= 25.0                              # 45 g micro quad ... 25 kg rover


# ---------------------------------------------------------------- tasks
def test_task_spec_parsing():
    t = TaskSpec.from_dict({"name": "goto_open", "task": "goto", "world": {"size": [20, 20]},
                            "reward": {"progress": 1.0, "reached": 10}, "randomize": {"mass": 0.2},
                            "labels": ["pad"]})
    assert t.episode["policy_hz"] == 20.0 and t.reward["reached"] == 10.0
    assert TaskSpec.from_dict(t.to_dict()) == t
    spec, issues = TaskSpec.parse({"name": "x", "task": "goto", "randomize": {"gravity": 3}, "labels": ["a", "a"],
                                   "reward": {"progress": "lots"}})
    assert spec is None
    assert {"randomize.gravity", "labels", "reward.progress"} <= {i.field for i in issues}


# ---------------------------------------------------------------- protocol
def test_cmd_header_keeps_the_protocol_1_fields_for_unicycle():
    h = cmd_header("unicycle", {"v": 0.2, "w": -0.5})
    assert h["v"] == 0.2 and h["w"] == -0.5 and h["u"] == {"v": 0.2, "w": -0.5} and h["space"] == "unicycle"
    assert parse_cmd(h) == ("unicycle", {"v": 0.2, "w": -0.5})
    assert parse_cmd({"t": "cmd", "v": 0.1, "w": 0.2}) == ("unicycle", {"v": 0.1, "w": 0.2})   # old peer -> new reader
    assert parse_cmd({"t": "cmd"}) == ("unicycle", {"v": 0.0, "w": 0.0})
    assert "v" not in cmd_header("velocity", {"vx": 1.0, "vy": 0, "vz": 0, "yaw_rate": 0})
    assert parse_cmd(cmd_header("velocity", {"vx": 1.0}))[0] == "velocity"
    nan = cmd_header("unicycle", {"v": float("nan"), "w": float("inf")})
    assert nan["v"] == 0.0 and nan["w"] == 0.0                              # JSON must stay valid and actuators sane


def test_hello_caps_and_framing_round_trip():
    from simlab import protocol

    caps = hello_caps(["unicycle"], ["camera"], ["pose"], fps=12)
    msg = protocol.pack({"t": "hello", "proto": protocol.PROTOCOL_VERSION, "caps": caps}, b"x")
    (h, payload), = protocol.Reader().feed(msg)
    assert h["caps"]["spaces"] == ["unicycle"] and h["proto"] == 2 and payload == b"x"


def test_the_webots_controller_import_path_never_loads_mujoco_or_opengl():
    """Regression: an eagerly loaded plug-in pulled MuJoCo + GLFW into the Webots controller process, which then never connected."""
    import subprocess
    import sys
    from pathlib import Path

    code = ("import sys\nimport sewerbot.core.profiles, sewerbot.core.protocol\n"
            "bad = [m for m in ('mujoco', 'glfw', 'torch', 'stable_baselines3') if m in sys.modules]\n"
            "print('loaded:', bad)\nsys.exit(1 if bad else 0)\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr[-400:]


def test_old_import_path_still_works():
    from sewerbot.core import protocol as old

    assert old.pack is simlab.protocol.pack and old.PORT == 8770
