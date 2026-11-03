# Sewer Inspection Rover — Simulation + Real Hardware (Webots-based)

## Context
Build a simulator where rovers of different **sizes, types and speeds** drive through a **user-defined sewer network** (several tunnels, junctions, goal posts in different directions) containing **cracks and leaks**. A simulated camera feeds a **detector that finds and classifies those defects**. The same software must also drive **real hardware (ESP32 + ESP32-CAM)**, with the hardware living in its **own tab**. The project folder `C:\Users\Parth\Desktop\phaltan4_similator` is empty (greenfield).

## What I found on your machine (checked, not assumed)
| Item | Result | Consequence |
|---|---|---|
| GPU | RTX 5050 Laptop, **8 GB VRAM**, driver 617.42, plus Intel iGPU (hybrid laptop) | Needs PyTorch **CUDA 12.8+** builds (Blackwell). Webots must be forced onto the NVIDIA GPU. |
| CPU / RAM | Core 7 240H (10C/16T), 23.6 GB | Plenty. Not a bottleneck. |
| **Disk** | **only ~51 GB free** (of ~675 GB used+free) | Rules out a heavy ROS 2/Gazebo/WSL stack; cap generated datasets (~10 GB). |
| Python | default **3.14.3**; also 3.10 and 3.11.9 (uv) | `torch 2.11.0+cu128` exists for 3.14 (verified via pip index). Webots R2025a predates 3.13/3.14, so its adapter will use **3.11**. |
| Tools present | git, node, docker, WSL (Ubuntu), winget, **arduino-cli 1.5.1 with esp32 core 3.3.12 + AVR core already installed** | I can compile ESP32/ESP32-CAM firmware here without the Arduino IDE. |
| Missing | Webots, ROS 2 | Webots installer is ~250 MB (needs your OK to download). |

## Decision: Webots, not ROS 2 / Gazebo
- **Native Windows**, uses the RTX directly. ROS's own docs say WSL has *no hardware acceleration* for Webots; Gazebo-on-WSL2 GPU rendering has multiple reports of black screens/CPU fallback. ROS 2 would also cost 10–20 GB of your 51 GB.
- Webots gives us exactly what we need built in: Python controllers, `Camera` **Recognition** (automatic ground-truth boxes → free training labels), `Track` nodes and **parametric PROTO templates** (one rover model → many sizes/types), noise/motion-blur/fisheye camera options.
- Webots is open source (Apache-2.0) and we build our own software on top of it. Use stable **R2025a** (R2025b is unreleased; only nightlies exist).
- Our protocol stays ROS-agnostic, so a `webots_ros2` bridge can be added later if you want ROS.

## Architecture (one brain, two bodies)
```
        ┌───────────── sewerbot (our software, Python, runs on laptop GPU) ─────────────┐
        │  detectors · defect tracker/map · route planner · reports · FastAPI dashboard │
        └───────────────▲──────────────────────────────────────▲────────────────────────┘
          frames + pose │ drive cmd (v, ω)        frames + telemetry │ drive cmd (v, ω)
        ┌───────────────┴───────────┐                ┌────────────────┴───────────────┐
        │ Webots adapter (thin)     │                │ Hardware adapter (thin)        │
        │ sewer world + rover PROTO │                │ ESP32-CAM MJPEG + ESP32 WebSocket│
        └───────────────────────────┘                └────────────────────────────────┘
```
Every rover (simulated or real) speaks one command: unicycle `(v, ω)`. Per-type mapping (differential, skid-steer, tracked, Ackermann) lives in the adapter. So detection, navigation and the dashboard are written once and tested in simulation first.

## Phased build (each phase has a "done when")
**P0 – Environment** (you: set Windows → Graphics → Webots → High performance; I download Webots after your OK). uv venv for the brain (torch cu128, rfdetr, opencv, fastapi); 3.11 venv for the Webots adapter. *Done when:* `torch.cuda.is_available()` on RTX 5050, a Webots Python controller runs, GPU is the NVIDIA one.

**P1 – Sewer map system.** YAML map spec → generator → `.wbt` world. Nodes (manhole chambers: 3-way/4-way/dead-end/entry shaft), edges (circular / egg / box-culvert, diameter, length, slope, material, optional water level), goal posts (coloured post + beacon at branch ends in N/E/S/W/diagonal directions). 3 sample maps: *straight+T*, *4-branch star with goals N/E/S/W*, *large loop tunnel with mixed pipe sizes*. *Done when:* generated worlds load, pipes are drivable, goals are reachable (graph test).

**P2 – Parametric rover PROTO + presets.** Fields: type, length/width/height, wheel radius, mass, max speed/accel, camera (height, tilt, FOV, resolution, fps), lights. Presets: **Micro 2WD** (~16 cm, 0.25 m/s, matches ESP32 hobby chassis), **Mid 4WD skid** (~35 cm, 0.5 m/s), **Tracked crawler** (~55 cm, 0.4 m/s), **Large 6WD** (~80 cm, 1.0 m/s); custom allowed up to ~1.5 m/s. A *fit check* warns when a rover won't fit a pipe. *Done when:* every preset drives every sample map; fit check blocks impossible combos.

**P3 – Defects + auto-labelled data.** Procedural crack generator (hairline, longitudinal, circumferential, branching, spalling) and leak generator (joint seep, wet streak, drip + puddle, flow), rendered as PBR decals on curved wall patches. Recognition node → exact boxes (masks if tight boxes needed) exported as COCO + YOLO. **Domain randomisation** (textures, lighting, exposure, noise, motion blur, fisheye) and **hard negatives** (pipe joints, seams, stains, cables, shadows) so the detector doesn't call every line a crack. *Done when:* 5–10 k labelled images are generated headless (fast mode), within the disk budget.

**P4 – Detection + defect map.** `Detector` interface with 3 backends: **classical OpenCV** (ridge/Frangi for cracks; dark-wet + specular for leaks — works on day one, no training, also the sanity baseline), **RF-DETR Nano/Small (Apache-2.0, default)** fine-tuned on P3 data (8 GB recipe: batch 1–2, grad-accum, gradient checkpointing), and **optional Ultralytics YOLO** (AGPL-3.0 — only if you accept open-sourcing everything or buying a licence). Tracker dedupes a defect across frames, converts to map coordinates, estimates crack length/leak area → severity. Scoring against ground truth: precision/recall per class, localisation error, **recall vs rover speed** sweep. *Done when:* held-out sim set scored; report (HTML/CSV/JSON + annotated frames) generated from a full run.

**P5 – Autonomy.** Route planner over the map graph (goal selection, junction turns), centreline following with range sensors + IMU/odometry, modes: *go-to-goal*, *inspect-all*, *manual*. *Done when:* each preset reaches goals in all three maps.

**P6 – Dashboard (FastAPI + plain HTML/JS, no build step).** Two tabs: **Simulation** (map picker/preview, rover type/size/speed controls, run/pause, live camera with boxes, 2-D map with rover + defects, report export) and **Hardware** (see P7). *Done when:* screenshot-verified in the in-app browser at desktop and phone width.

**P7 – Hardware tab + firmware.**
- **ESP32-CAM (AI-Thinker):** camera node only — MJPEG stream (≈12 fps at 800×600, ≈25 fps at CIF in one published benchmark; I'll default to VGA/SVGA), settings endpoint, optional LED. Needs a dedicated ≥5 V/1 A supply (+ bulk capacitor) — brownout resets are the #1 documented failure.
- **ESP32 DevKit:** rover brain — drives your **DRV8833** with 4 LEDC-PWM pins (AIN1/AIN2/BIN1/BIN2; the DRV8833 has no enable pins, so PWM goes straight onto the IN pins), `nSLEEP` on a GPIO, `nFAULT` read as an input and shown in the tab. WebSocket server for `(v, ω)` @ ~20 Hz, **300 ms failsafe stop**, speed ramps, PWM-duty cap, telemetry (battery V, fault flag, optional encoders/IMU/ranges). Same command as simulation, so the autonomy code is reused unchanged.
- **Power design for 4×AA NiMH (≈4.8 V nominal, ~5.6 V fresh, ~4.0 V when spent).** The ESP32 DevKit and ESP32-CAM both use an AMS1117 linear regulator (dropout 1.1–1.3 V → needs **≥ ~4.6 V in**), so powering them straight from 4 NiMH cells will brown out as the cells discharge or when the motors pull the pack down. Plan: motors from the pack straight into the DRV8833 `VM` (it runs on 2.7–10.8 V; add a ≥ 470 µF bulk cap + the 10 µF ceramic TI recommends), and **one 5 V boost module (e.g. MT3608-class, set to ~5.1 V) feeding both ESP32 boards** with a bulk cap at each 5 V pin. Firmware reads pack voltage through a resistor divider on an ADC1 pin and applies a **latching low-battery cut-off (~1.0 V/cell ≈ 4.0 V)** — latching because NiMH voltage "bounces back" once the load drops. Check your DRV8833 module: PWP/RTY-package modules are rated 1.5 A RMS / 2 A peak per bridge, the small PW package only 500 mA RMS (TI datasheet), so cap duty to your motors' stall current.
- **Position on hardware:** plain gear motors have no encoders, so odometry is open-loop (PWM→speed calibration) unless you add encoders or an MPU6050 IMU. The tab supports both; defect positions on the map are approximate without them.
- **Arduino:** (1) **USB-serial programmer for the ESP32-CAM** (RESET→GND, RX↔RX/TX↔TX, IO0→GND while flashing — confirmed by several sources; a helper script wraps `arduino-cli`); (2) optional **5 V sensor hub** (MQ gas, ultrasonic) over UART, because the ESP32's ADC2 is unusable while Wi-Fi is on.
- **Tab features:** device connect/status (RSSI, fps, latency, battery), live stream + detection overlay, keyboard/gamepad drive, calibration wizard (camera scale from known pipe diameter; motor speed from a 1 m run), **capture-and-label** export to fine-tune on *real* ESP32-CAM frames, **offline replay** (run the detector on recorded video/images with no rover).
- Firmware lives in `firmware/` with one `config.h` for pins/geometry; compiled via `arduino-cli`. *Done when:* both sketches compile; the dashboard works end-to-end against a **mock ESP32 emulator** I write; I flash real boards if you plug them in.

**P8 – Docs & tests.** README with one-command start, wiring diagrams, calibration and troubleshooting.

## Proposed layout
```
phaltan4_similator/
  sewerbot/{core,brain,dashboard,hardware}/   # protocol, rover profiles, detectors, planner, UI, device drivers
  sim/webots/{worlds,protos,controllers,textures}/
  tools/        # map→world generator, crack/leak texture generator, dataset builder, scoring
  maps/         # YAML sewer maps
  firmware/{esp32_rover,esp32cam_stream,arduino_sensor_hub}/
  docs/  tests/
```

## Hardware inventory
**Confirmed by you:** Arduino, ESP32, ESP32-CAM, **DRV8833** motor driver, **4×AA NiMH** battery pack, laptop with RTX 5050.
**Still assumed (correct me if wrong):** small DC gear motors rated for ~3–6 V on a 2WD/4WD chassis (no encoders), an Arduino Uno/Nano, no FTDI adapter (hence the Arduino-as-programmer route), no IMU/ultrasonic yet. Pins/geometry sit in one `config.h`, so adding encoders, an IMU or sensors is a small edit.

## Risks & honest limits
1. **Sim-to-real gap.** A detector trained only on synthetic cracks will *not* be reliably accurate on real concrete. Mitigations: randomisation, hard negatives, mixing real public images (Roboflow Pipe Defects, Istanbul ISWDS, Co-UDlabs; **check each licence first** — Sewer-ML is classification-only and needs a request), and fine-tuning on your own ESP32-CAM captures. I will not claim real-world accuracy until tested on real images.
2. **Webots R2025a × Python 3.14** is unverified → adapter pinned to 3.11 (already installed); verified as the P0 gate.
3. **Junction geometry** (round pipes through chamber walls) is the fiddliest mesh work → fallback: simple box chambers with square-framed openings.
4. **8 GB VRAM:** never train while Webots renders; use Nano/Small; RF-DETR on Python 3.14 may need a 3.12 venv (YOLO backend as fallback).
5. **Wi-Fi does not propagate well inside real underground sewers.** The hardware tab targets bench/test-pipe/demo use; field use would need a tether or on-board recording (future work).
6. **Power is the most likely real-world failure:** 4×AA NiMH is right at the AMS1117 dropout limit, so an unmodified wiring (batteries → ESP32/ESP32-CAM) will reset under motor load. The boost-converter + bulk-capacitor design above is part of the deliverable (wiring diagram), but I can't measure your actual voltage sag.
7. I can compile firmware and test against an emulator, but **cannot verify physical behaviour** (traction, battery life, real Wi-Fi range) without you running it.

## Verification
- `pytest`: map generator (valid geometry, goal reachability), protocol + failsafe against the mock ESP32, defect→map projection maths.
- Headless Webots runs in fast mode: every preset × every map; scripted pass/fail on goal reached, collisions, time.
- Detector scoring on a held-out synthetic set (per-class P/R, mAP, recall-vs-speed); classical baseline for comparison.
- Dashboard checked in the in-app browser (desktop + phone width), console clean.
- `arduino-cli compile` for ESP32-CAM, ESP32 and Arduino sketches.
- Final end-to-end demo: choose a map + rover in the Simulation tab → autonomous run → report; then replay a recorded clip in the Hardware tab through the same detector.

## Sources
Webots releases/docs (github.com/cyberbotics/webots, cyberbotics.com/doc), ROS 2 Jazzy Webots-on-Windows guide (docs.ros.org), Gazebo WSLg threads (discourse.openrobotics.org, gazebosim/gz-sim#2595), PyTorch Blackwell threads (discuss.pytorch.org), RF-DETR docs (rfdetr.roboflow.com), Roboflow model licences (docs.roboflow.com/models/model-licenses), Ultralytics licence (ultralytics.com/license), Sewer-ML (arXiv 2103.10895), Co-UDlabs sewer_defects, ISWDS (mdpi.com/2076-3417/15/20/11096), ESP32-CAM benchmark (aggregate.org) and troubleshooting (randomnerdtutorials.com), Arduino-as-ESP32-CAM-programmer threads (stackoverflow, forum.arduino.cc), TI DRV8833 datasheet (ti.com/lit/ds/symlink/drv8833.pdf), NiMH + AMS1117 power threads (forum.arduino.cc, arduino/electronics.stackexchange.com).
