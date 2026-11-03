# Sewerbot + simlab: a configurable, trainable simulator for any machine

**simlab** ([docs/LAB.md](docs/LAB.md)) is a simulator where a machine is data: rovers of any size and drive, quad/hex/octo-copters, boats, or any MuJoCo/URDF robot you import. Start in the **Goal** tab: say what the machine is for (find cracks in a pipe, rescue on the water, watch from the air), answer a few plain questions, and get a machine made of sourced parts, checked on paper, shown working in the simulator, stress-tested (where does it start to fail, and what was not tested) and with a build sheet ([docs/GOAL.md](docs/GOAL.md)). In the **Builder** you assemble your own hardware from parts (ESP32, motor driver, motors, wheels, battery, sensors; or a drone or boat) and get predicted performance, errors and verified "add this part" suggestions ([docs/BUILDER.md](docs/BUILDER.md)). Pick a machine and a task in the dashboard, watch it in 3D with its onboard camera, **train control policies by reinforcement learning** and **camera detectors** on simulator data, and compare the result with a classical controller. Measured results and limits: [docs/RESULTS_LAB.md](docs/RESULTS_LAB.md).

**Sewerbot** (the original project, below) is built on the same core: one software "brain" drives two bodies, a **Webots** simulation of rovers in a sewer network and a **real ESP32 + ESP32-CAM rover**. Cracks and leaks are found by a detector (RF-DETR trained on simulator data, or a training-free OpenCV fallback), tracked into a defect map and written to a report.

```powershell
.venv\Scripts\python.exe -m sewerbot        # http://127.0.0.1:8000  ->  Goal · Lab · Builder · Train · Sewer (Webots) · Hardware
```

```
          ┌───────────────── sewerbot (Python, runs on the laptop GPU) ──────────────────┐
          │  detectors · defect tracker · route planner · reports · dashboard (2 tabs)   │
          └──────────────▲────────────────────────────────────────▲──────────────────────┘
     frames+pose │ (v,ω) │                                  frames │ (v,ω) via UDP
          ┌──────┴────────┴──────┐                       ┌────────┴───────────────────────┐
          │ Webots adapter       │                       │ ESP32-CAM  (MJPEG, port 81)    │
          │ sewer world + rover  │                       │ ESP32 + DRV8833 (UDP, port 4210)│
          └──────────────────────┘                       └────────────────────────────────┘
```

## Quick start (Windows)

```powershell
# 1. one-time setup, see docs/SETUP.md (Python 3.11 venv, Webots R2025a, optional GPU stack)
# 2. start the dashboard (double-click startup.bat, or:)
.venv\Scripts\python.exe -m sewerbot            # opens http://127.0.0.1:8000
```

`startup.bat` does the same and, if a dashboard is already running, just opens it (two dashboards would fight over port 8770, the Webots link).

* **Lab and Train tabs:** the generalised simulator ([docs/LAB.md](docs/LAB.md)).
* **Goal tab (first):** describe the job, get a checked machine, watch it, stress-test it, get the build sheet; for the pipe rover it connects to the real ESP32 rover over USB ([docs/GOAL.md](docs/GOAL.md), [docs/HARDWARE.md](docs/HARDWARE.md)).
* **Builder tab:** pick parts (or add your own with datasheet numbers), see a 3D preview, predicted speed / acceleration / turn rate / climb / runtime, errors and suggestions, check it against a task, calibrate it with a ruler and a stopwatch, then *Save*: it appears in the Lab like any vehicle ([docs/BUILDER.md](docs/BUILDER.md)). Its predictions are estimates until calibrated; nothing has been compared with your own hardware yet.
* **Sewer (Webots) tab:** pick a map, a rover (type, size, speed), a mission (manual / go to a goal post / inspect everything) and a detector, press *Start*. Webots opens, the rover appears on the map, the camera view shows detections, and a report is written when the mission ends.
* **Hardware tab:** enter the two ESP32 addresses and press *Connect*. No hardware yet? Press **Try with emulator**: it streams simulator frames and mimics the rover firmware so the whole tab works. Firmware, wiring, power and calibration: [docs/HARDWARE.md](docs/HARDWARE.md).

## What is in the box

| Part | Where | Notes |
|---|---|---|
| Sewer maps (YAML) | `maps/` | pipes (round / oval / box), chambers, branches in any direction, goal posts, planted or random cracks and leaks |
| World generator | `tools/gen_world.py` | YAML + rover profile → Webots world, procedural wall textures, baked crack/leak patches, box-only collision |
| Rover model | `sim/webots/protos/SewerRover.proto` | one parametric model: 2-wheel, 4-wheel, 6-wheel, tracked; any size/speed/camera |
| Presets | `sewerbot/core/profiles.py` | `micro_2wd` (ESP32-sized), `mid_4wd`, `tracked_crawler`, `large_6wd`, plus free customisation |
| Detectors | `sewerbot/brain/detectors/` | `rfdetr` (Apache-2.0, default once trained), `classical` (no training), optional `yolo` (AGPL, see licensing) |
| Dataset + training | `tools/make_dataset.py`, `tools/train_rfdetr.py` | auto-labelled, pixel-exact boxes straight from the renderer |
| Firmware | `firmware/` | ESP32 rover (DRV8833), ESP32-CAM stream, Arduino sensor hub |
| simlab (generalised simulator) | `simlab/` | vehicle specs, command spaces, tasks, MuJoCo + Webots backends, RL training, detector pipeline, plug-ins, Lab/Train dashboard tabs |
| Builder (parts → machine) | `simlab/parts/` | 55 sourced parts, derivation of speed / thrust / runtime, rules, verified suggestions, calibration; fast MuJoCo pipe environment in `simlab/world/pipe_geometry.py` + `simlab/tasks/pipe.py` |
| Tests | `tests/` | `pytest -q`: 350+ tests (Webots and hardware are not needed; the Webots backend is tested against a fake controller) |

## Results at a glance (simulator data)
RF-DETR Nano on 450 unseen test images: cracks 92 % precision / 85 % recall, leaks 93 % / 86 %, 28 ms per frame. A full 5-goal inspection mission found 16 of 16 planted defects. Details and caveats: [docs/RESULTS.md](docs/RESULTS.md).

## Honest limits

* A detector trained **only** on simulated cracks is not reliable on real concrete. The sim proves the pipeline and gives a strong start; for real accuracy, capture frames from your own ESP32-CAM (Hardware tab → *Save frame*), label them, and fine-tune ([docs/TRAINING.md](docs/TRAINING.md)).
* The OpenCV baseline is a fallback, not a product: it has low precision on textured walls (it is shown in the dashboard as such).
* Wi-Fi barely penetrates underground sewers. The Hardware tab targets bench, test-pipe and demo use; a real deployment needs a tether or on-board recording.
* Without wheel encoders or an IMU the rover's position along the pipe is dead-reckoned from the commanded speed: approximate. Calibrate it (Hardware tab).

Licensing summary: [docs/LICENSING.md](docs/LICENSING.md).
