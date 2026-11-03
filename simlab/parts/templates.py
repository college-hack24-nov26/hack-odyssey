"""Starting points for the Builder: complete builds made only of library parts, so a new user begins with something that works."""
from __future__ import annotations

import copy

EXAMPLES: dict[str, dict] = {
    "esp32_pipe_rover": {
        "name": "esp32_pipe_rover", "platform": "ground",
        "description": "The rover in docs/HARDWARE.md: ESP32 + ESP32-CAM + DRV8833 + 2 TT motors + 4xAA NiMH behind a boost converter.",
        "chassis": {"length": 0.16, "width": 0.12, "height": 0.055, "frame_mass_kg": 0.12, "drive": "diff2wd", "caster_count": 1},
        "drive": {"motor": "tt_motor_6v", "motor_count": 2, "wheel": "wheel_65mm_tt", "driver": "drv8833_module_pwp", "speed_limit_mps": 0.0},
        "power": {"battery": "aa_nimh_2000", "series": 4, "parallel": 1, "regulator": "mt3608_boost_module"},
        "controller": "esp32_devkit",
        "sensors": [{"part": "esp32cam_stream_board", "name": "front", "mount": {"forward": 0.07, "height": 0.075, "tilt_deg": -10}},
                    {"part": "hc_sr04", "name": "sonar", "mount": {"forward": 0.08, "height": 0.03, "tilt_deg": 0}}],
        "lamp": {"part": "led_headlamp_small", "count": 1},
        "extras": [{"name": "wiring and board", "mass_kg": 0.02, "current_a": 0.0}],
        "calibration": {},
    },
    "my_zk2wd_rover": {
        "name": "my_zk2wd_rover", "platform": "ground",
        "description": "Your bench rover as far as it is known: ZK-2WD kit (two TT-class motors), ESP32 DevKit, DRV8833, a separate ESP32-CAM (GC2145, 96x96), IR and ultrasonic sensors planned. ASSUMED, check each against your parts: the driver's chip package (PW assumed, the safer 0.5 A one), the wheel size, the battery (4xAA NiMH) and the boost converter. Then calibrate.",
        "chassis": {"length": 0.16, "width": 0.12, "height": 0.06, "frame_mass_kg": 0.14, "drive": "diff2wd", "caster_count": 1},
        "drive": {"motor": "tt_motor_6v", "motor_count": 2, "wheel": "wheel_65mm_tt", "driver": "drv8833_module_pw", "speed_limit_mps": 0.0},
        "power": {"battery": "aa_nimh_2000", "series": 4, "parallel": 1, "regulator": "mt3608_boost_module"},
        "controller": "esp32_devkit",
        "sensors": [{"part": "esp32cam_gc2145_board", "name": "front", "mount": {"forward": 0.07, "height": 0.075, "tilt_deg": -10}},
                    {"part": "hc_sr04", "name": "sonar", "mount": {"forward": 0.08, "height": 0.03, "tilt_deg": 0}},
                    {"part": "ir_obstacle_module", "name": "ir", "mount": {"forward": 0.08, "height": 0.02, "tilt_deg": 0}}],
        "lamp": {"part": "led_headlamp_small", "count": 1},
        "extras": [{"name": "wiring and board", "mass_kg": 0.02, "current_a": 0.0}],
        "calibration": {},
    },
    "romi_like_rover": {
        "name": "romi_like_rover", "platform": "ground",
        "description": "A Pololu-Romi-style 2-wheel robot: 120:1 motors, 70 mm wheels, an Arduino Nano and a TB6612FNG, on 4xAA behind a boost converter.",
        "chassis": {"length": 0.165, "width": 0.165, "height": 0.07, "frame_mass_kg": 0.18, "drive": "diff2wd", "caster_count": 1},
        "drive": {"motor": "pololu_120_1_hp_4v5", "motor_count": 2, "wheel": "wheel_70mm_pololu", "driver": "tb6612fng_module", "speed_limit_mps": 0.0},
        "power": {"battery": "aa_nimh_2000", "series": 4, "parallel": 1, "regulator": "mt3608_boost_module"},
        "controller": "arduino_nano",
        "sensors": [{"part": "hc_sr04", "name": "sonar", "mount": {"forward": 0.08, "height": 0.04, "tilt_deg": 0}}],
        "extras": [],
        "calibration": {},
    },
    "quad_5in_racer": {
        "name": "quad_5in_racer", "platform": "air",
        "description": "A 5-inch quadcopter: 2306 motors, 4S 1500 mAh, a buck regulator for the controller, an IMU and a camera.",
        "chassis": {"frame": "frame_5in_x", "extra_mass_kg": 0.0},
        "drive": {"propulsor": "motorprop_2306_2400kv_5x4_4s", "esc": "esc_30a"},
        "power": {"battery": "lipo_cell_1500_75c", "series": 4, "parallel": 1, "regulator": "lm2596_buck_module"},
        "controller": "esp32_devkit",
        "sensors": [{"part": "mpu6050", "name": "imu", "mount": {"forward": 0.0, "height": 0.03, "tilt_deg": 0}},
                    {"part": "esp32cam_stream_board", "name": "front", "mount": {"forward": 0.07, "height": 0.04, "tilt_deg": 25}}],
        "extras": [{"name": "wiring and radio", "mass_kg": 0.015, "current_a": 0.0}],
        "calibration": {},
    },
    "small_boat": {
        "name": "small_boat", "platform": "water",
        "description": "A 1.2 m boat: one 12 V thruster, two lead-acid batteries in parallel, a buck regulator for the electronics.",
        "chassis": {"hull": "hull_box_1m2", "ballast_kg": 4.0},
        "drive": {"thruster": "thruster_small_12v", "count": 1},
        "power": {"battery": "lead_acid_12v_7ah", "series": 1, "parallel": 2, "regulator": "lm2596_buck_module"},
        "controller": "esp32_devkit",
        "sensors": [{"part": "neo6m_gps", "name": "gps", "mount": {"forward": 0.0, "height": 0.35, "tilt_deg": 0}}],
        "extras": [],
        "calibration": {},
    },
}


def example_builds() -> dict[str, dict]:
    return copy.deepcopy(EXAMPLES)
