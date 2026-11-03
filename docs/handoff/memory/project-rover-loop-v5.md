---
name: project-rover-loop-v5
description: "Rover plan v5 (approved 2026-11-04): real-rover error-correction loop + bag/box mission + new UI; hardware facts, pin conflicts, what is built"
metadata:
  node_type: memory
  type: project
  originSessionId: b8925161-60f4-4746-95b3-44c7726075c9
  modified: 2026-11-03T19:35:17.967Z
---

Plan file `C:\Users\Parth\.claude\plans\lazy-wondering-floyd.md` (approved). Order: A (rover loop) -> M (bag/box mission) -> B (project backend) -> U (Claude-desktop-style UI) -> C (flow cards) -> D (judges' demo hardening).

**Hardware facts from the user:** ESP32 DevKit + DRV8833 (left GPIO26/27, right 32/33), ESP32-CAM, MPU6050 fitted, HC-SR04 being set up (pending until the user says it works), 2S Li-ion -> LM2596, no capacitors, **no battery/rail dividers fitted**, encoder and IR dropped. Demo task: follow a bag, orbit it, bag to bag, end at a goal box (goal object must stay flexible; user will send photos of bag and box).

**Pin conflicts found:** the user wired MPU SDA/SCL to GPIO32/33 (= right motor pins) and HC-SR04 TRIG to GPIO34 (input-only). Recommended: MPU on 21/22, TRIG on 18, ECHO on 35 via 10k/20k divider. Pin map lives in `config/hardware.json` "pins" (`sewerbot/hardware/pins.py`).

**Built (A0/A1 slice, 2026-11-04):** `firmware/esp32_rover_v2/` ('!' protocol, soft start, gyro heading-hold, runtime sensor guard, optional Wi-Fi TCP), `sewerbot/hardware/{bringup,flash,pins,mock_v2}.py`, `tools/rover_bringup.py`, `tools/flash.py`, `tests/test_rover_v2.py`. Not yet flashed to the real board (gate G0/G1 are the user's).

**Why:** the loop is only proven once the user runs bring-up on the real rover. **How to apply:** never claim hardware results before reading their `runs/rover/<time>/log.txt`; flashing only after the user confirms.

Tests: running pytest while the live dashboard holds Webots port 8770 fails `test_simlab_webots`; set `SIMLAB_WEBOTS_PORT=87xx` (parallel sessions can collide too).
