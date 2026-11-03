# Plan (v5): the rover flow end to end — think, blueprint, parts, environment, simulate, test on the real rover, fix, flash — then the bag/box mission, a Claude-desktop-style app, and a judges' demo that works

## 0. Two wiring problems to fix BEFORE the next power-on (found while planning)
Your pins collide with the motor pins already in the repo (`firmware/esp32_rover_serial`, `docs/HARDWARE.md`: left motor GPIO26/27, **right motor GPIO32/33**):
1. **MPU6050 on GPIO32 (SDA) / GPIO33 (SCL) is the right motor's DRV8833 inputs.** Driving I2C and PWM on the same two pins will garble the gyro and can make the right wheel jerk or run on its own. Unless you moved the motor wires, **move the MPU to GPIO21 (SDA) / GPIO22 (SCL)** (the ESP32's normal I2C pins; free now that the IR sensor is dropped).
2. **HC-SR04 TRIG on GPIO34 cannot work:** GPIO34/35/36/39 are *input-only* on the ESP32. **ECHO on GPIO35 is fine** (input, through your 10k/20k divider). **Move TRIG to GPIO18** (the firmware's existing default; GPIO19 is free as a spare).

**Recommended pin map** (stored as data in `config/hardware.json`, turned into a generated `pins.h` at flash time, so a different map is a config change, not a code edit):
| Function | GPIO |
|---|---|
| Left motor (DRV8833 IN1/IN2) | 26 / 27 |
| Right motor (IN3/IN4) | 32 / 33 |
| MPU6050 SDA / SCL (3V3 supply, AD0→GND) | 21 / 22 |
| HC-SR04 TRIG / ECHO (ECHO via 10k/20k divider; VCC 5 V) | 18 / 35 |
| Battery / rail dividers | not fitted (34 stays free for later) |
The first-contact tool (A0) also *probes* the real wiring (I2C scan on 21/22, 32/33, 16/17; echo test) so a wrong map is reported in plain words before any motor moves.

## 1. Context
**Goal:** a user types a job; the app works out the requirements, makes a blueprint, lists the equipment, asks what the user already owns (cheapest build first), names a test environment, simulates the machine in it, asks the user to connect the built device, tests it physically, finds where reality differs from the simulation, fixes it, re-verifies, then writes/flashes the code (and tunes the small model/controller). **The rover is the judges' demo and must work with one click, with fallbacks.**

**The demo task (new):** the rover **follows a bag on the ground, runs laps around it, or goes from one bag to another, and ends at a goal box**; the goal object is **flexible** (you will send photos of the bag and the box; adding any other object later must be a few photos, not a code change).

**Builds on** the chat app (committed) and supersedes the rover plan v4. Older plans (planner, Webots, portfolio/website ones) are background only.

**Hardware now (your latest word):**
| Part | Status in the app |
|---|---|
| ESP32 DevKit + DRV8833 + 2 TT motors; ESP32-CAM (GC2145 96×96, Wi-Fi) | in use |
| 2S Li-ion → LM2596 → one 5.x V rail, **no capacitors** | in use; **no battery/rail dividers** (confirmed) |
| **MPU6050** | fitted; to be moved/verified (§0); used for heading and lap counting |
| **HC-SR04** | being set up; **pending until you say it works** |
| Rotary encoder, IR proximity | **dropped** (not fitted, not used) |

**Design rule: the firmware tells the app what is really connected.** At boot it reports a `hello` line (firmware version, reset reason, which sensors answered). A sensor is "verified" only after sane readings **and** your confirmation; until then it is never used for safety decisions. With no dividers the app has no battery reading: power health comes from the boot reset reason and a multimeter value you type in.

## 2. How the MPU6050 is used
- **Feed-forward trim + feedback heading-hold:** a stored left/right duty ratio cancels the weak motor; the firmware also integrates gyro-Z at ~100 Hz and nudges the two duties to hold heading (bounded ±15 %, self-disabling if the gyro stops answering or reads implausible values).
- **Measure before correcting:** hold would hide a weak motor, so the reality-gap test runs **open-loop first**; then hold is switched on and the correction it needs must agree with the trim (that agreement is the verification).
- **Laps:** orbit progress is counted from integrated gyro heading (360° = one lap), with the camera and HC-SR04 keeping the radius.
- **Honest limits:** relative heading only, small bias (zeroed for 2 s at every start), no distance or speed. Speed/distance come from the tape/ruler, the camera, and (once verified) the HC-SR04. Camera-only fallback remains (tape measurement, phase correlation) if the gyro is absent or distrusted.

## 3. The user flow (every step is a chat card)
1. **Requirements** (what, where, limits; questions only for what matters) → 2. **Blueprint** (Builder checks, planner verified by code) → 3. **Equipment list** (*I have it / buy it / swap*, cost, "using what I have" vs "best"; inventory saved and updated from what the rover reports) → 4. **Named environment** (e.g. "tile floor, tape line, two bags, one box": becomes the simulated scene *and* the physical set-up instructions, stored with every run) → 5. **Simulate** → 6. **Connect** (port auto-found, health check, checklist, Stop) → 7. **Physical tests** (bounded) → 8. **Gaps found** (evidence + confidence) → 9. **Fix + verify** (before/after, "verified on hardware for these tests", never "safe") → 10. **Write, tune, flash** (config/firmware from template, confirm click; exact manual steps if arduino-cli cannot).
Non-moving devices: steps 1–5 plus a bench-check route (blueprint, parts, wiring check, flash), no motion tests.

**"Train a model", honestly:** the on-device "model" is the controller (trim + heading-hold gains), identified from real runs and tuned on the twin with `simlab/control/search.py` / `simlab/train`. Object detection runs on the laptop from the camera stream (96×96 is too small for on-device). Real frames are captured for fine-tuning (`HardwareManager.capture`, `simlab/perception/train.py`).

## 4. The bag / box mission (new capability)
- **Target registry** (`simlab/missions/targets.py`, `config/targets/<id>/`): name, role (*follow / waypoint / goal*), 3–10 photos, real size in cm (you tell me), detector. First detector: a **colour + shape model learned from your photos** (fast, works at 96×96, tested on the same photos with a hit-rate shown); a fine-tuned detector (existing RF-DETR/YOLO paths) only if that proves too weak. Until your photos arrive, stand-in coloured objects in the simulator.
- **Missions as steps** (`simlab/missions/`): `go_to(target, stop_at)`, `follow(target, gap)`, `orbit(target, radius, laps)`, `stop_at(goal)`; a mission is a list of steps, so "bag A → bag B → box" and "3 laps around the bag, then the box" are data. Chat requests map onto this closed vocabulary (planner output validated like today). Lost target → stop; time and distance limits per step.
- **Driving:** laptop closed loop (camera bearing/apparent size → commands). Firmware v2 gains a bounded `D <left> <right> <ms>` command (independent duties, arcs possible: an orbit at 40 cm needs an inner/outer ratio ≈ 0.74, above the 90 minimum duty) so the rover is not limited to spin-or-straight.
- **Wi-Fi link is required for laps and bag-to-bag:** a USB cable would wrap around the bag and limit range. Firmware v2 serves the **same line protocol over Wi-Fi (TCP)** with the same heartbeat and 1 s watchdog; USB stays for flashing, bring-up and tethered tests. Credentials live in a git-ignored `config/wifi.json` (never printed or committed). Wi-Fi bursts on the shared rail raise the brownout risk: tested in A0/A1, soft start kept.
- **Simulation:** new task `simlab/tasks/bag_mission.py` (pattern of `goto.py`/`sandbox.py`) with bag/box objects in a named flat-floor environment; same mission runner runs in the simulator and on the rover.
- **Honest limits:** detection range at 96×96 is limited (a 30 cm bag is ≈12 px wide at 2 m; ≈6 px at 4 m: reliable range is measured, not assumed); one camera, 9–11 fps, ~0.2–0.4 s end-to-end latency, so slow speeds.

## 5. Milestones (each ships alone; `pytest -q` green at every gate (727 now); commit per milestone through the clone `hack-odyssey-target`, since `.git` is missing here)
**Phase A: the real rover loop (hardware first)**
- **A0 First contact (`tools/rover_bringup.py` + health check):** port found; firmware answers; pin-map probe (§0); `hello` parsed (reset reason: brownout flagged); MPU WHO_AM_I, rest bias/noise, gravity/axes check, a by-hand 90° turn confirms gyro sign/scale; HC-SR04 probe (output pin works, echo sane); per-wheel check wheels-off; camera check (reachable, fps, brightness, blur). Log to `runs/rover/<time>/`. **Gate G0:** you run it, we read the log.
- **A1 Firmware v2 + safe flashing (`firmware/esp32_rover_v2/`, `esp32_rover_serial` kept as recovery build):** `hello`; soft start (~200 ms), staggered starts, coast before reversing; brownout reset-reason log (rail-sag back-off exists but stays off until dividers are fitted); MPU read, bias, bounded heading-hold; runtime sensor config (HC-SR04 guard off until verified, replacing today's "no reading = blocked forever" compile flag); NVS trim/invert/min duty; firmware-enforced timed moves streaming `yaw/rate/correction`; `D l r ms` command; Wi-Fi TCP transport; pre-drive checklist updated from "4 AA NiMH ≥ 4.4 V" to "2S pack ≥ 7.2 V on a multimeter, buck output 5.0–5.7 V". `tools/flash.py` wraps arduino-cli (installed) with a confirm token and checks. **Gate G1:** flash, health check passes, no brownout on starts, Wi-Fi link holds.
- **A2 Synthetic rover (parallel):** extend `MockSerialPort` (`sewerbot/hardware/mock.py`) with the v2 protocol, gyro and Wi-Fi-like latency/drop; faults via the existing `side_gain`, inverted wheel, humming motor, gyro bias, camera tilt; camera frames from the simulator. For development, CI, and a clearly labelled "simulated rover" fallback.
- **A3 Reality-gap engine (`simlab/twin/`):** test protocol (timed straight run, spins, start threshold, repeats for noise); deterministic diagnosis (motor imbalance + side, asymmetric turns, start threshold, power resets, dead/inverted motor, gyro scale, camera tilt, link latency) with evidence and confidence; twin calibration (`side_gain`, yaw gain, speed scale, extending `simlab/parts/calibrate.fit`); trim computation. Tested with every injected fault. **Gate G3:** real drift measured and diagnosed.
- **A4 Correct, verify, persist, deploy:** apply trim + hold gains (NVS + `config/hardware.json`); tune on the calibrated twin and check stability in simulation first; re-run on the rover; before/after report; generate config from template; flash (confirm first). **Gate G4:** measurably straighter than before.

**Phase M: the bag/box mission**
- **M0 Targets + detector** (photos in, hit-rate out, stand-ins until yours arrive). **M1 `bag_mission` sim task + mission runner**, all four behaviours working in the simulator with tests. **M2 Mission on the real rover** (Wi-Fi, closed loop, HC-SR04 guard once verified): **Gate G5:** follows a bag, completes a lap, goes bag→bag, ends at the box. **M3 Gaps again:** the reality-gap loop covers steering response, latency and camera tilt for this task, and fixes them.

**Phase B: project flow backend:** `simlab/project/` (requirements, blueprint, equipment/inventory + sensor status, named environment, run records, stage tracker; `runs/projects/<id>/`, patterns from `simlab/chat/store.py`); matcher fix (2+ keyword hits, so the plant monitor stops matching the boat); non-moving device route.

**Phase U: the new look (your earlier screenshot is not in view now; re-attach it when we get here if the description below is off).** Claude-desktop-style shell: left **sidebar** (projects → chats, search, new project), centre **conversation**, right **panes** (Plan, Simulation, Tests, Parts, Rover, Background tasks). Built on the existing chat files (`simlab/dashboard/static/chat*.js/css`, `Viewer3D`; the details sheet becomes a docked pane). Keeps themes, a11y, Limits strip, honesty tests; `/workbench` untouched.

**Phase C: flow cards in the new shell:** Requirements, Blueprint, Equipment ("I have…"), Environment, Targets, Mission, Rover (connect / health / checklist / Stop / flash), Reality, Fix-and-verify, Deploy; stage tracker. The AI is used only for planning (≤3 calls per plan); the rover loop and missions use none.

**Phase D: make the demo flawless**
- **D1 "Run the demo":** preflight (port, firmware version, sensors, camera, Wi-Fi, multimeter value) → go/no-go card → story: plan → simulate → real test → gap found → fix → verify → mission; automatic fallback to the labelled synthetic rover, offline mode if OpenRouter is down, a one-page set-up checklist (pack charged, tape line, bags, Stop reachable).
- **D2 Failure drills:** USB unplugged, Wi-Fi drop, camera drop, brownout, low pack, wrong port, lost target, double click, reload: each ends safely with a plain message. Docs/brief updated (`docs/PROJECT_BRIEF.*` via `tools/build_brief.py`, `docs/CHAT_PROTOCOL.md`, new `docs/ROVER.md`).
- **D3 Two full rehearsals on the real rover**, logs and a screen recording kept as a backup.

## 6. Safety (enforced in code)
No movement without the server-side checklist; every test bounded by the firmware itself; 1 s watchdog and boot-coast on every transport (USB and Wi-Fi); Stop always on screen; first test wheels off the ground; no free driving or global key handler on the chat page; heading-hold bounded and self-disabling; missions stop on lost target, timeout, or link loss; flashing, trim and sensor guards change only after a confirm click; HC-SR04 not relied on until verified; every real run logged. Li-ion needs a protection board, a 2S charger and a fuse; without dividers the app asks for a multimeter reading before each real session (warns at 7.2 V) and treats any brownout reset in the log as "stop and check the pack".

## 7. Verification
- `pytest -q` at every gate. New tests: `hello`/pin-probe parsing, soft-start and stagger on the emulator, heading-hold stability and self-disable, every injected fault diagnosed with the right side/size, low-confidence ruler fallback, flash refuses without the confirm token, firmware compile (skipped without arduino-cli), Wi-Fi watchdog on the emulator, target detector hit-rate on stand-in images, each mission step in simulation (follow, orbit laps counted from heading, bag→bag, stop at goal, lost target stops), project store round-trip, matcher sentence, honesty banned-word test on all new cards, route order.
- Browser pane at 1440×900 and 375×812: the full story on the **synthetic** rover; sidebar/panes; reload mid-run.
- **Real-rover gates (you run it, I read the logs):** G0 first contact · G1 flash + Wi-Fi · G3 real drift diagnosed · G4 correction verified · G5 mission on the floor · D3 rehearsals.

## 8. Honest limits
Until G0 nothing is proven on your hardware; the synthetic rover proves only the software. No capacitors means brownouts are reduced (soft start, staggered starts) and only detected after the fact (reset reason), since there are no dividers. The gyro cannot measure distance. Detection is limited by the 96×96 camera and Wi-Fi latency. "Verified on hardware" covers only the tests run, in the named environment. Machines other than the rover (drone, boat) stay blueprint + simulation until their hardware path exists.
