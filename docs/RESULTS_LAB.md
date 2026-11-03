# simlab results (measured on this machine, simulator only)

Machine: Intel Core 7 240H (10 cores), RTX 5050 Laptop 8 GB, Windows 11, Python 3.11, MuJoCo 3.17, PyTorch 2.11 + CUDA 12.8.
Everything below is simulator data. None of it has been checked against real hardware.

## Simulation speed and fit to the specification

| Check | Result |
|---|---|
| Bare MuJoCo step, tiny model | 240k steps/s on one core |
| Rover (planar model + sensors + task), one core | ≈ 5.4k policy steps/s (10 physics steps each) |
| Drone (autopilot in Python), one core | ≈ 2.4k policy steps/s |
| RL training, 8 worker processes, rover | ≈ 3.9k policy steps/s steady (≈ 2.6k including evaluations); 300k steps ≈ 2 min |
| Onboard camera render | 13 ms at 320×240, 21 ms at 640×480 |
| Rover top speed / acceleration / spin vs the vehicle spec | 0.500 m/s (0.5), peak 0.599 m/s² (0.6), 1.500 rad/s (1.5), no sideways drift |
| Drone hover | holds 1.5 m within 3 cm, level within 1°, on a 45 g quad, a 650 g quad and a 1.8 kg hex |
| Drone velocity step | 10-22 % overshoot, tilt limits respected, yaw rate 0.999 for 1.0 commanded, steady 6 m/s wind rejected to < 0.15 m/s |
| Boat | floats at its predicted draft (±1 cm), reaches 5.04 m/s vs 4.9 estimated, symmetric turns |

## Classical baselines (these are what a trained policy has to beat)

* **Rover `goto`**, 4 rovers × 20 episodes × 2 task variants: 154 / 160 goals reached, zero contact. The misses are time-outs where the reactive controller wedges itself in a dead end.
* **Drones**, 3 airframes × 5 tasks (waypoints, takeoff, hover-and-hold, gusty wind with random mass and motor strength, landing) × 10 episodes: **150 / 150**. The baseline has perfect state.
* **`search`** (find 3 targets among 6 distractors): rover and both quads find 18 / 18 targets over 6 episodes each; the slow micro rover finds 13 / 18.

## Reinforcement learning (PPO, 8 simulators, evaluated on 50-100 identical unseen scenarios)

| Machine / task | Steps | Trained policy | Classical baseline |
|---|---|---|---|
| `mid_4wd` / `goto_open`, collision penalty -0.5 | 300k | 98 % reached (95 % CI 90-100), 13.5 s, **21 contact steps per episode** | 98 % (90-100), 11.8 s, 0 contact |
| `mid_4wd` / `goto_open`, collision penalty -3 | 600k | 93 % (86-97), 12.2 s, **62 contact steps per episode** | 98 % (93-99), 11.7 s, 0 contact |
| `quad_250` / `waypoints_easy` | 600k | 100 % (96-100), 6.7 s, 0 contact | 100 % (96-100), 5.9 s, 0 contact |

What this says, plainly: the learned policies reach the goal about as often as the classical controller but are not better. The rover policy learned to get there while scraping obstacles, and a larger collision penalty plus twice the steps did not fix it (it got worse; a 20-episode in-training evaluation had reported 100 %, the 100-episode comparison is what exposed it). The drone policy matches the baseline and is slightly slower. Obstacle-aware reward shaping, longer training, SAC, or observing the distance to the nearest obstacle are the obvious next experiments; they were not run.

## Camera detector (RF-DETR Nano, quad_250 camera, task `search_targets`)

800 captured images (644 train / 78 valid / 78 test, whole scenes per split), 15 epochs, 7.3 min total on the RTX 5050. On the held-out test scenes (116 labelled objects):

| Class | Precision | Recall | F1 |
|---|---|---|---|
| crate | 97.6 % | 100 % | 0.988 |
| barrel | 94.7 % | 100 % | 0.973 |
| ball | 95.2 % | 100 % | 0.975 |

18 ms per image (22-25 ms inside the dashboard). The objects are distinct solid colours and shapes, so this is an easy detection problem; the unlabelled lookalike distractors (other colours, same shapes) are not confused with targets. Real imagery would be much harder.

## Webots sewer regression

The sewer pipeline runs behind the same `Backend` contract as MuJoCo (`WebotsBackend`, task `sewer_drive`, presets `sewer_straight` and `sewer_star`).

**Live backend check** (`python -m tools.webots_smoke`): `Sim(mid_4wd, "sewer_straight", backend="webots")` connects to Webots in 4-10 s, runs 300 policy steps (19.5 s of simulated time), moves 5.8 m at 0.30 m/s and returns real 640x480 camera frames; the observation vector has no NaNs.

**Whole-mission regression** (star map, `mid_4wd`, inspect-all, RF-DETR nano, same script in `fast` and `realtime` mode):

| | planted defects found | false alarms | precision |
|---|---|---|---|
| before the refactor (`docs/RESULTS.md`) | 16 / 16 | 5 | 76 % |
| after, `fast` mode | 16 / 16 | 9 | 64 % |
| after, `realtime` mode | 16 / 16 | 9 | 64 % |

Recall is unchanged. **False alarms went up from 5 to 9, and I did not find why.** Both modes agree, so it is not timing. What was checked and is identical to the pre-refactor snapshot: the four rover presets (value by value), `iou` and `nms`, the tracker, the navigation code and the command values the rover receives. What was not checked: running the old snapshot again today (the 5 may come from a slightly different model, defect layout or tracker setting at the time) or the effect of the Webots world generator changes. Treat 9 as the current number.

**A bug found on the way:** on Windows, two programs that listen on the same port with `SO_REUSEADDR` can both bind it, and an incoming connection then goes to only one of them. A leftover dashboard and a regression script both listened on port 8770, so the rover talked to the wrong one and the mission never started. Both listeners now bind exclusively and fail with a clear message instead. Run one dashboard (or one Webots run) at a time.

**Limits of the Webots backend:** one simulation at a time, `reset()` starts Webots again (4-10 s here, up to tens of seconds on a cold start), no segmentation render and no 3D scene export (use the Webots window), ground vehicles only, so it is not suited to RL training. Use MuJoCo for that.

## Builder: parts model checked against published data

The Builder's physics ([BUILDER.md](BUILDER.md)) was compared with numbers that manufacturers or other people published. None of this is a measurement of the rover on this desk; no such measurement exists yet.

| Check | Published | Model | Error |
|---|---|---|---|
| Pololu HP 50:1 / 75:1 / 100:1 6 V, maximum output power | 1.3 / 1.4 / 1.3 W | torque × no-load speed / 4 | +0.2 / −2.3 / +4.1 % |
| Pololu motor, maximum-efficiency point | 490 rpm, 0.32 A | 487 rpm, 0.36 A | −0.6 % speed, +12.5 % current |
| Pololu Zumo, two gear ratios (wheel radius fitted on the third ratio) | measured top speeds | predicted | −7.5 % and −1.2 % |
| BrotherHobby R5 2306 2400 kV, 5×4 prop, 4S: current at 36 / 144 / 438 / 821 / 1258 g thrust | 1.3 / 2.7 / 8.0 / 17.2 / 31.1 A | `idle + (Imax − idle)·(T/Tmax)^1.5` | +11.1 / −9.1 / −7.2 / −1.1 / 0 % |
| Adafruit TT motor, datasheet speed vs a measured speed | 200 rpm datasheet | 250 rpm measured | datasheet is 20 % **low** here |

The last row is a warning in the other direction: hobby-motor data sheets are not reliably optimistic or pessimistic, which is why every part carries a `confidence` badge and why calibration exists. The thrust curve is fitted at its top point, so the 0 % error there is by construction; the other four points are the test.

**Calibration closed loop** (test `test_the_calibrated_vehicle_moves_and_turns_like_the_real_one_in_mujoco`): entering the measurements 520 g, 0.90 m in 3 s, 540° in 3 s, 1.2 s to 90 % speed, then running the saved vehicle in MuJoCo gives a top speed within 4 % of 0.30 m/s, a spin within 12 % of 540° and a time to 90 % speed within about a third of 1.2 s (the simulator ramps the command and the motor lags, so it is not a straight line to 90 %). The measurements here are example numbers typed into the test, not readings from a real rover.

## Builder: drones and boats from parts

| Check | Result |
|---|---|
| `quad_5in_racer` (built from parts), `hover_hold`, 10 seeds | 10 / 10 |
| 6-rotor F550-class build, `waypoints_easy`, 10 seeds, thrust-to-weight 1.33 | 10 / 10 |
| `small_boat` top speed: simulated at full throttle vs the build's own estimate | 6.16 m/s vs 5.91 m/s (+4 %) |

The boat comparison is the model against the same plug-in's simulator, not against a real boat. The drone builds hover and fly because the derived autopilot limits are conservative (see BUILDER.md); they say nothing about what the airframe could do in the air.

## Pipe environment in MuJoCo (fast sewer inspection)

`mid_4wd`, inspect-every-goal missions, `python -m simlab.tasks.pipe_eval`, detector = **oracle** (the exact boxes from the segmentation render, i.e. a perfect detector), so the numbers below measure the mission, tracking and pose pipeline rather than a neural network:

| Map | Planted defects | Found | Missed | False alarms | Sim time | Wall time |
|---|---|---|---|---|---|---|
| `pipe_short` (A–J–E and back) | 4 | 4 | 0 | 0 | 91 s | 55 s |
| `pipe_straight` (straight_t) | 10 | 10 | 0 | 0 | 285 s | n/a |
| `pipe_star` (5 goals, star_five_goals) | 16 | 15 | 1 (a crack) | **4** | 618 s | 436 s |

Even with a perfect detector the star map ends with one miss and four false alarms, so part of the false-alarm count in any run on this map comes from how detections are tracked and projected into the map, not from the detector. I did not investigate why (candidates: detections of a defect seen from two sides of a chamber counted twice, the match radius, camera tilt in curved pipes). The Webots false-alarm figure above (9 vs 5) may have the same cause; that is a guess, not a finding.

**Speed:** about 1,790 policy steps/s on one core without the camera (each step is 20 physics substeps), 22 steps/s with a 640×480 frame every step; a frame takes 17–24 ms. The scenes are 10–21 k triangles. Several copies run in parallel for training, which Webots cannot do.

**Other checks (tests):** the collision boxes equal the Webots world's `boundingObject` boxes in count and size; 500 steps of full-speed pushing against the walls keep the vehicle inside the pipe; a planted defect is visible in the camera frame and in the segmentation frame with the right class; the scene is identical for the same seed.

**Detector transfer (measured):** the RF-DETR nano trained on Webots frames (`models/rfdetr_nano_v1`), run on the MuJoCo `pipe_star` mission with the same route and tracker:

| Detector | Found | Missed | False alarms | Precision |
|---|---|---|---|---|
| oracle (perfect boxes) | 15 / 16 | 1 | 4 | 79 % |
| RF-DETR trained on Webots frames | 16 / 16 | 0 | **28** | **36 %** |

Recall holds, precision collapses: the detector finds every defect but also fires on 24 more things the oracle does not, i.e. it does not transfer cleanly from Webots renders to MuJoCo renders. This is the expected domain gap, now with a number. To train in the MuJoCo pipes, capture a dataset there (`simlab.perception`, task `pipe_star`) and train on it; I have not done that run. Both worlds draw their defects with the same generator, so even a good score would say little about real pipes. An earlier run on a smaller map that I had noted as 71 % precision is not reproduced here; use the table.

## Builder: scenario sweep (ground, air, water)

A hand-made matrix of 30+ builds, each checked statically (does the Builder say the right thing?) and, when it should work, run in MuJoCo with the classical baseline:

| Platform | Builds that should work | Result | Builds that should be refused or warned | Result |
|---|---|---|---|---|
| Ground | ESP32 rover, Romi-like, 4WD N20, tracked crawler | all run `goto_open` 3/3 with peak speed equal to the prediction (0.50 / 0.56 / 0.75 / 0.30 m/s); the ESP32 rover also drives `pipe_short` 2/2 | no boost converter (E05 error), small-package driver (E03 warning), 4WD on an L298N (E03 error), wheels taller than the body (M06), camera-less rover on a pipe task (T01), garbage input (rejected, no crash) | all as expected |
| Air | 5-inch racer, F550 hexacopter (TWR 1.33), micro 1S | racer 5/5 `hover_hold`, 5/5 `waypoints_easy`, 3/3 `takeoff_waypoints`; hex 5/5 and 3/3 | 2.5 kg payload (A01 error), ESC too weak (A04 error), 1S pack on 4S motors (errors) | as expected |
| Water | 1.2 m boat, 3 m hull with two thrusters | 6.16 m/s vs 5.91 estimated; 5.03 vs 5.02 m/s; both turn with the rudder | ballast beyond displacement (W01), ground motor as thruster (rejected) | as expected |

Three of my first expectations were wrong and the Builder was right (a 0.5 A driver with a 1.2 A stall is a warning, not an error; four 1.8 A motors on two L298N channels is an error; four 12 V batteries in series give 48 V, above the only regulator's limit). The drone runs succeed because the autopilot limits are conservative; the Builder labels a TWR of 1.33 "sluggish" and still lets it fly. Nothing here was checked on real hardware.

## Goal tab: designs, stress tests and the real-rover link

Everything here is simulator or emulator data. **Nothing was run on the physical rover**: its motors currently hum without turning (a hardware problem under repair), so the USB link was tested against a software emulator of its firmware.

**Designer** (`simlab/goals/design.py`, default answers, no parts owned): sewer rover 210 candidates in 1.8-2.4 s, drone 288 in 2.5-3.5 s, boat 12 in 0.1-0.4 s. What it returns: the pipe rover for 30 cm pipes (feasible); a 1.2 m boat for calm to choppy water (feasible); for rough water (0.6 m waves, 12 m/s wind) **no design in the library meets the goal** (the small hull is too low, the 3 m hull cannot push against the wind) and it says so; for a 20-minute drone flight the best library design gives 20 min against the 24 needed, so it also says so.

**Stress tests** (`simlab/hazards/`, same scenes at every level; a level passes at 75-80 % success):

| Machine (default answers) | Hazard | Result |
|---|---|---|
| pipe rover, 4 runs per level, 16 s in all | slippery floor (grip 0.8 to 0.05) | all levels pass: the model limits grip only on a flat floor, so this is **not** evidence that slime is harmless |
| | mud and debris (share of pulling force used up) | fine up to 0.3, fails from 0.6 |
| | slow commands up to 1 s | passes |
| | a drive motor fails / 30 % of commands lost | **fails every run** (2-wheel rover circles) / passes |
| 1.2 m boat, 4 runs per level, about 2.5 min for 23 conditions | waves up to 0.9 m | passes: the model only heaves and tilts the boat (no breaking waves, no slamming), so this is the easy half |
| | wind | fine up to 12 m/s, fails at 16 m/s |
| | current up to 2.5 m/s | passes (75 % at 1.5 and 2.5 m/s) |
| | fixed obstacles (about 60 cm, not steered around) | 6 or 15 in the way: 50 %; 30: 0 % |
| | the only thruster fails / rudder jams / a storm (0.45 m, 10 m/s, 0.5 m/s current) | 0 % / 25 % / passes |
| drone, 20 min design | wind | fine up to 8 m/s, 50 % at 12, fails at 16 and 20 |
| | a rotor fails / all motors at 70 % | **flips every run** (the simple autopilot has no fault-tolerant control) / passes |

**What the stress test found about the software itself:** (1) with the first controller the 79 kg boat failed even in calm water (it approached too fast to stop); (2) the 3 m "best match" boat was blown away by 8 m/s wind, which the designer did not know, so a wind-versus-thrust requirement was added; (3) the mud hazard first changed almost nothing because the traction model is stiff, so it now cuts top speed by the share of force used up (a linear motor curve).

**USB rover link** (emulator, 14 tests): forward, back and spin map to the right keys; a drive command lapses after 0.4 s; the firmware watchdog stops a rover that hears nothing for 1 s; a distance reading under 15 cm, no valid reading, a silent sensor or an IR hit each block forward motion while spinning stays allowed; closing the link leaves the rover stopped; drive controls stay locked until the four-item checklist is complete (checked on the server). The firmware compiles for the ESP32 DevKit at 278,839 bytes (21 % of flash) without sensors and 282,431 bytes with both sensors enabled.
