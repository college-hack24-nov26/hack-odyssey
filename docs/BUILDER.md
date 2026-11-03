# The Builder: your hardware as a simulated machine

The **Builder** tab turns a list of parts into a machine the simulator can run. You pick (or describe) the parts you have, it predicts what that
machine can do, tells you what is wrong or missing, and with one button the machine appears in the Lab, ready for every task and for training.

```
 parts you have  ──►  Build (YAML)  ──►  predicted speed, thrust, runtime, mass, current   ──►  findings and part suggestions
 (a library of            │                                                                       (checked against a task)
  datasheet numbers       └──►  VehicleSpec  ──►  Lab / Train / pipe environments / Webots
  + your own parts)
```

Open it with `startup.bat` (or `python -m sewerbot`) and click **Builder**. It starts with an example you can edit: the ESP32 rover from
[HARDWARE.md](HARDWARE.md). The **Kind of machine** switch gives the other two examples: a 5-inch quadcopter and a small boat.

## What it can and cannot tell you

* **Predictions are estimates from datasheets, which are optimistic.** The software says so on screen ("estimate") and lists every assumption it
  made. Until you calibrate with measurements from your real machine (section 7), expect tens of percent of error.
* **Nothing here has been checked against *your* hardware.** What has been checked is the physics against published data (section 5).
* **Ground vehicles are the best-modelled; drones are rougher; boats are first guesses.**

## 1. Parts

A part is one YAML file with its numbers, **where they came from** (`source`) and **how far to trust them** (`confidence`):

| confidence | meaning |
|---|---|
| `datasheet` | every number is from the manufacturer's page or a published measurement |
| `typical` | typical values for that kind of part, or some numbers are estimates (the notes say which) |
| `assumed` | round numbers for a generic part: replace them with your own |
| `measured` | you measured it |

The library ships 55 parts in `simlab/parts/library/<kind>/`. Kinds: motor, wheel or track, motor driver, battery cell, voltage regulator,
controller board, camera, range sensor, IMU, GPS, lamp, drone frame, motor + propeller, ESC, boat hull, boat thruster.

**Adding your own part:** Builder → *Parts library* → *Add your own part*, choose the kind, optionally start from a similar part, fill in the
datasheet numbers and save. The form is generated from the same schema the server checks, so a typo or a unit mistake is rejected with a reason
(a motor whose numbers imply more than 90 % efficiency is refused: usually torque in kg·cm typed as N·m). Your parts live in `config/parts/`
and override shipped parts of the same name. Tick **I own this** in the library and your parts are listed first in every picker.

## 2. A build

A build (`config/builds/<name>.yaml`, written by *Save*) names parts and says how they fit together. Ground example:

```yaml
name: esp32_pipe_rover
platform: ground                       # ground | air | water
chassis: {length: 0.16, width: 0.12, height: 0.055, frame_mass_kg: 0.12, drive: diff2wd, caster_count: 1}
drive:   {motor: tt_motor_6v, motor_count: 2, wheel: wheel_65mm_tt, driver: drv8833_module_pwp, speed_limit_mps: 0}
power:   {battery: aa_nimh_2000, series: 4, parallel: 1, regulator: mt3608_boost_module}
controller: esp32_devkit
sensors:
  - {part: esp32cam_stream_board, name: front, mount: {forward: 0.07, height: 0.075, tilt_deg: -10}}
  - {part: hc_sr04, name: sonar, mount: {forward: 0.08, height: 0.03}}
lamp: {part: led_headlamp_small, count: 1}
```

*Save* also writes the matching vehicle to `config/vehicles/`, so the Lab, the Train tab and every task see it like a built-in preset.
Air builds use `chassis: {frame}` and `drive: {propulsor, esc}`; water builds use `chassis: {hull}` and `drive: {thruster}`.
The rotor count follows the frame's layout.

## 3. What is predicted

**Ground** (`simlab/parts/derive/ground.py`): a brushed DC motor has a straight torque-speed line fixed by four datasheet numbers (rated voltage,
no-load speed, stall torque, stall current). Fed from a battery that sags and through a driver that loses some voltage, the torque available at
any speed has a closed form, so top speed (against rolling resistance and a little drag), start torque, acceleration, spin speed, climbing
slope and the stall current need no iteration. Battery runtime follows from the current at 70 % of top speed and the electronics' draw through
the regulator. Constants that are assumptions, not data, are listed in the *Assumptions* card: a 0.85 torque derate, 80 % usable battery
capacity, 10 % extra mass for wiring, and the turn-rate gain per drive layout (measured in the Webots simulation, not on hardware).

**Air** (`derive/air.py`): the thrust-test point of a motor + propeller (thrust and current at full throttle at one voltage) is scaled with the
battery voltage, thrust and current both as V². Hover current is an idle current plus a thrust^1.5 curve. The autopilot's limits (maximum tilt,
climb, descent, yaw rate, drag, motor time constant) come from simple rules of thumb and are conservative limits for the simulator's autopilot,
not claims about the airframe.

**Water** (`derive/water.py`): hull draft, freeboard and top speed come from the boat plug-in's own model; thrust and current scale with V².

## 4. Findings and suggestions

Every problem is a finding with an id, a severity (**error** blocks saving, **warn**, **info**), an explanation, and often a fix.

| ids | what they check |
|---|---|
| E01–E04 | battery voltage against the motor and driver ratings; motor stall current through the driver (a side's motors share a channel); battery current at stall |
| E05 | controller power: the documented ESP32 brown-out on 4×AA NiMH (no boost converter), regulator limits, and every load on the rail over the battery's whole charge range |
| E06–E09 | 5 V sensor outputs into 3.3 V pins, pin count, interfaces (I²C, UART, PWM, camera), a camera board used as the only brain |
| E10 | NiMH discharge rate |
| M01–M06 | cannot move, cannot start, cannot turn in place, sluggish, wheels too big for the body |
| A01–A06 | drone: thrust-to-weight, hover thrust, flight time, ESC and battery current, propeller against frame |
| W01–W04 | boat: sinks, little freeboard, thrust/weight, thruster voltage |
| T01–T06, M04 | the **task** (below): missing camera, lamp, pipes it does not fit, battery that will not last, too slow, slopes |

**Suggestions are verified, not guessed.** For a finding with a fix hint (for example "add a boost regulator"), each candidate part is applied to
a copy of your build and the whole build is checked again; a part is offered only if it removes the finding without creating a new error.
Parts you own come first. If nothing in the library fits, the card says so: add the part you have.

**Will it do the job?** Pick a task in the *Will it do the job?* card. Each task states what it needs (`Task.requirements()`): a camera for
inspection, a lamp in a dark pipe, that the vehicle fits the pipes of the map, runtime for the whole episode. The simulator quietly adds an
ideal rangefinder for navigation tasks so the run works; the finding reminds you that real hardware needs one.

## 5. How far the physics has been checked

Against published data (`docs/RESULTS_LAB.md` has the numbers):

* the motor model's maximum output power matches Pololu's published figure for three motors within about 4 %, and reproduces Pololu's
  published maximum-efficiency operating point (speed within 1 %, current within about 13 %);
* with the wheel radius fitted on one gear ratio, the model predicts the other two measured Pololu Zumo speeds within 8 % and 2 %;
* the drone current model matches a measured thrust table (five points, 3 % to 100 % of maximum thrust) within about 11 %;
* a calibrated vehicle in the simulator reproduces the measured top speed (test tolerance 4 %), a spin (12 %) and the time to 90 % speed
  (the test allows about a third, because the simulator's acceleration is a slew-limited ramp plus motor lag, not a straight line).

Not checked: any prediction against a real ESP32 rover, a real drone flight or a real boat. That is what calibration is for.

## 6. The pipe environment (fast, in MuJoCo)

Tasks `pipe_short`, `pipe_straight` and `pipe_star` (Lab → pick a ground vehicle) build a sewer map from `maps/` in MuJoCo: textured pipe walls,
floor and ceiling, defect patches carrying a `crack` or `leak` label, collision boxes identical to the Webots world's (a test compares them),
and the vehicle's own lamp as the only light. The camera applies the same on-chip auto-exposure emulation the Webots path uses. It is fast
(about 1,800 policy steps/s on one core without the camera, about 22 with a 640×480 frame every step) and can run many copies in parallel for
training, which Webots cannot.

* **Drive:** the baseline follows the route with the sewer pipeline's own path follower; a trained policy can be compared on the same seeds.
* **Datasets:** the Train tab / `python -m simlab.perception` captures labelled images with exact boxes from the segmentation render. Poses are
  grouped by defect so a defect appears in only one of train / valid / test.
* **Inspection missions:** `python -m simlab.tasks.pipe_eval --task pipe_star --detector oracle|classical|rfdetr:<dir>` drives the
  inspect-everything route and scores it like the Webots regression (recall, precision, false alarms).
* **Honest limits:** the pipe pictures are rendered by MuJoCo, not by Webots' physically based renderer, and not real footage. Both worlds make
  their defect patches with the same generator, so the defects themselves look alike: a detector moving between them says little about real
  pipes. The vehicle's wheels are visual only in this backend (the ground model is planar), so wheel-level slip in curved pipes is Webots-only.

## 7. Calibration: tie the predictions to your machine

Measure what you can (any subset, about two minutes with a ruler, a stopwatch and a kitchen scale), enter it in *Calibrate with your real machine*:

| measure | how | replaces |
|---|---|---|
| mass | weigh the assembled vehicle | the sum of the parts |
| straight run | full speed on a flat floor; ignore the first second, then note the distance covered in 3 s | the speed prediction |
| spin | full-speed spin on the spot; the angle turned in 3 s | the turn-rate gain |
| time to 90 % | video the start from standstill: seconds to reach 90 % of top speed | the acceleration |
| runtime | drive at a steady moderate speed on a full battery until it cuts out | the usable battery fraction |

A table shows predicted, measured and the error, then the calibrated prediction (the model is scaled so it reproduces your measurement; the simulator
then follows it to within the tolerances in section 5). A prediction more than 30 %
off usually means a motor or battery entry does not match what you have. Calibration is available for ground builds; the air and water models
are first guesses, so compare them with a flight or a float test yourself.

## 8. API

`GET /api/build/library` · `POST /api/build/evaluate {build, task?}` · `/preview` · `/save` · `/calibrate` · `/part` · `/part/delete` ·
`/inventory` · `/delete` · `GET /api/build/list` · `GET /api/build/get?name=`. `evaluate` never answers with a server error: bad input comes back
as issues. The same code is a library: `from simlab.parts import evaluate, Build, build_to_vehicle`.

## 9. Not built

Choosing hardware from a goal (the Builder checks the hardware you have and suggests parts that fix a problem, it does not design a machine
from scratch), prices and availability, a drag-and-drop layout editor, fixed-wing aircraft, a motor thermal model, centre-of-mass editing,
firmware generation, importing the Hardware tab's calibration, and any claim about how a simulation-trained policy behaves on real hardware.
