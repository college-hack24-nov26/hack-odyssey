# simlab: one simulator for any machine

`simlab` generalises the sewer-rover project into a configurable, trainable simulator. A machine (rover, 4WD, tracked crawler, quadcopter, hexacopter, boat, or any MuJoCo/URDF robot you bring) is **plain data**; a **task** says what to do in what world; a **backend** simulates it; a **policy** drives it. You can watch it in 3D, train control policies by reinforcement learning, and train camera detectors, all from the dashboard.

```
   Dashboard  ──  Goal · Lab · Builder · Train · Sewer (Webots) · Hardware
        │
   Task (world, goal, reward) ── Policy (manual · classical baseline · trained RL model · idle)
        │      command in a named command space
   Backend:  MuJoCo (fast, headless, trainable)   Webots (sewer visuals)   real hardware (ESP32 rover, own tab)
        │
   VehicleSpec (YAML): body + parameters + sensors + limits   ← the machine is data
```

## Run it

```powershell
.venv\Scripts\python.exe -m sewerbot          # dashboard at http://127.0.0.1:8000, opens on the Lab tab
.venv\Scripts\python.exe -m pytest -q         # 350+ tests, no Webots or hardware needed (stop any running dashboard first: one test uses port 8770)
```

* **Lab:** choose a vehicle, a task, a policy and a speed, press *Start*. The 3D view follows the machine (Follow / Orbit / Top; drag to rotate, wheel to zoom), the onboard camera and telemetry update live. Keys: arrows/WASD drive a rover; for a drone also Q/E slide and R/F climb; any other machine gets one slider per actuator. Press a key during a baseline run and you take over.
* **Vehicle designer** (same tab): every parameter of the selected machine as a form, generated from its schema. Checks run as you type (a drone that cannot hover, a boat that sinks, wheels taller than the body are refused with the reason) and derived facts update (thrust-to-weight, top speed, draft ...). *Save as...* writes `config/vehicles/<name>.yaml`; *Import* reads an MJCF/URDF file.
* **Train:** reinforcement-learning runs (PPO or SAC, 1-16 parallel simulators, optional easy-to-hard curriculum) with live learning curves and a final comparison against the task's classical baseline on identical scenarios; *Watch trained policy in Lab* replays it. The same tab trains a **camera detector** (see below).
* **Goal:** the starting point: describe a job and get a machine ([GOAL.md](GOAL.md)).
* **Builder:** assemble a machine from real parts and save it as a vehicle ([BUILDER.md](BUILDER.md)).
* **Sewer (Webots)** and **Hardware**: the original sewer-inspection and ESP32 tabs, unchanged.

## Machines

| Kind | What it is | Command spaces |
|---|---|---|
| `ground_wheeled` | 2WD / 4WD / 6WD skid-steer, tracked | `unicycle` (v, ω) · `wheels` (left, right) |
| `multirotor` | quad (X or +), hex, octo; any arm length, mass, motor size, battery | `velocity` (vx, vy, vz, yaw rate) · `position` · `attitude_thrust` · `motors` |
| `custom` | any MJCF or URDF robot | `raw`: one channel per actuator |
| `boat` | displacement hull, propeller, rudder (a **plug-in**, see below) | `helm` (throttle, rudder) |

Presets live in `simlab/vehicles/presets/` (and the boat's in `simlab/plugins/boat_presets/`); yours in `config/vehicles/`. Example (`simlab/vehicles/presets/quad_250.yaml`):

```yaml
name: quad_250
kind: multirotor
body: {mass: 0.65, length: 0.25, width: 0.25, height: 0.08}
params: {layout: quad_x, arm_length: 0.125, thrust_max: 4.0, motor_tau: 0.03, max_tilt_deg: 35, max_speed: 12}
sensors:
  - {type: camera, name: front, forward: 0.1, height: 0.04, tilt_deg: 25, fov_deg: 90, width_px: 320, height_px: 240}
  - {type: imu, name: imu}
  - {type: battery, name: battery, chemistry: lipo, cells: 4, capacity_mah: 1500}
```

A multirotor is flown by a cascaded autopilot (position → velocity → attitude → body rate → motor thrust, written in `simlab/vehicles/multirotor.py`) so policies command *where to go*, not motor speeds. Its gains follow from the motor time constant, and the mixer gives up altitude before attitude when the motors saturate.

## Tasks

| Task | For | What |
|---|---|---|
| `goto` | ground | reach a goal circle among random obstacles |
| `waypoints`, `hover_hold`, `takeoff_waypoints` | multirotor | fly through 3D waypoints; hold a position; start on the ground |
| `land` | multirotor | touch down gently on a pad |
| `search` | ground, multirotor | find labelled targets among lookalike distractors by looking at them |
| `sandbox` | any | arena, no goal: drive anything by hand |
| `pipe_inspect` (presets `pipe_short`, `pipe_straight`, `pipe_star`) | ground | drive a sewer network **in MuJoCo** (textured pipes, cracks and leaks with labels, the vehicle's own lamp); `python -m simlab.tasks.pipe_eval` scores an inspect-everything mission, see [BUILDER.md](BUILDER.md) section 6 |
| `water_rescue` | boat | reach a person floating in open water and stay beside them; optional fixed obstacles; capsizing, swamping and a flat battery count as failures; stress conditions via `Sim(..., hazards={...})` ([GOAL.md](GOAL.md)) |
| `sewer_drive` | ground, **Webots** | a sewer map through the generic loop (inspection missions stay in the Sewer tab) |

Presets in `simlab/tasks/presets/`; reward weights, randomisation (mass, motor strength, friction, wind, sensor noise) and the number of obstacles are all in the YAML.

## Train a control policy

From the **Train** tab, or:

```powershell
.venv\Scripts\python.exe -m simlab.train --vehicle quad_250 --task waypoints_easy --steps 600000 --curriculum 0.3
.venv\Scripts\python.exe -m simlab.train eval --vehicle quad_250 --task waypoints_easy --model runs\train\<run>\model_best.zip
```

Each run is a folder in `runs/train/` with its config (full vehicle and task specs), metrics, best model and a trained-vs-baseline comparison; 95 % Wilson intervals are reported because a 20-episode success rate is mostly noise. Simulation is CPU-bound (one MuJoCo process per simulator, measured ≈ 3.9k policy steps/s on 8 workers for a rover); the small policy network trains on the CPU too. What it achieved: [RESULTS_LAB.md](RESULTS_LAB.md).

## Train a camera detector

The simulator renders images from sampled viewpoints (looking at objects from plausible distances and heights, sometimes at nothing), randomises lighting, floor and wall colours, camera optics, exposure and blur, and takes **exact boxes from the segmentation render**. Scenes are assigned whole to train / valid / test. RF-DETR (Apache-2.0) is fine-tuned on the GPU and scored on the held-out scenes. In the Lab, pick the trained *Detector* to see its boxes on the onboard camera.

```powershell
.venv\Scripts\python.exe -m simlab.perception pipeline --config cfg.json --out runs\detectors\mine   # capture + train + evaluate
```

## Bring your own machine

* **Any MJCF or URDF file:** Lab → Vehicle designer → *Import*, or `python -c "from simlab.vehicles.importer import draft_spec; ..."`. Its actuators become the `raw` channels (ranges from `ctrlrange`), its own cameras are used, relative mesh paths work, meshes show in the 3D view. A URDF's root link is welded to the world by MuJoCo (fixed-base arms); a free-floating MJCF base can be placed anywhere.
* **A new kind of machine (a plug-in):** write one module that registers a `VehicleKind` (parameter schema, command spaces, checks, derived facts) and a MuJoCo `Plant` (how it moves), and list it in `config/plugins.txt`. `simlab/plugins/boat.py` is a complete worked example (buoyancy sampled across the hull, water drag, rudder, 190 lines). A plug-in that fails to import is reported in the catalog and skipped.
* **A new task:** subclass `simlab.tasks.base.Task` (world, reset, observation, reward) and `TASKS.add(...)`.

## Backends

* **MuJoCo** (default): headless, deterministic, vectorisable, CPU. Ground vehicles use a planar acceleration-limited traction model with the measured skid-steer yaw loss, **not** wheel-level contact physics.
* **Webots**: the sewer world with photoreal-ish textures and wheel-level physics. One simulation at a time; starting it takes tens of seconds; no segmentation render. `Sim(vehicle, "sewer_straight", backend="webots")` drives it through the same loop.

## Honest limits

* Policies trained here have **not** been transferred to hardware. Randomisation and sensor noise are there to help; transfer is untested.
* The drone autopilot uses ground-truth state (no estimator), and the flight model has no rotor-flapping, ground effect, or propeller-gyro terms.
* Detectors trained on simulator renders will not read real camera frames reliably; the pipeline is the deliverable, real accuracy needs real images.
* Fixed-wing aircraft are not built in (the plug-in route is how to add one; JSBSim installs on this Python and is the natural flight-dynamics core).
* Windows OpenGL: all rendering is serialised on one thread (`simlab/backends/mujoco/glthread.py`) because GLFW contexts must not hop threads; segmentation uses its own non-anti-aliased renderer because multisampling blends object ids into wrong ones.
