# Plan (v4): make the software really run the rover, and make the error-correction loop work on it (no capacitors available)

## 1. Context
**Priority:** the loop on the REAL rover: run it for real, detect that reality differs from the simulation (one motor slower, weak power, tilted camera), correct it, verify, put the code in the machine. UI restyle, non-moving devices and the rest wait until this is proven.

**Hardware as it is:** ESP32 DevKit (USB) + DRV8833 + 2 TT motors + ESP32-CAM (Wi-Fi, GC2145 96x96) + a **2S Li-ion pack through an LM2596 buck at 5.7 V feeding everything on one rail**. **No capacitors available.** Baseline sensor = the camera only; you add the rest (battery/rail dividers, MPU6050, one encoder, HC-SR04, IR) one at a time and tell me. **No rover is connected to this computer right now** (checked: only Bluetooth COM ports; camera not reachable), so the synthetic rover proves the software until first contact.

**Power facts that shape the design:** the LM2596 only holds 5.7 V while the pack is above about 7.2 V (~25 % charge left), so 7.2 V on the pack counts as "empty" (or lower the buck to ~5.2 V to get down to ~6.7 V); a shared rail means every motor start can sag the rail and reset the ESP32/camera; with no bulk capacitance the **software must remove the surge** instead.

## 2. Power hardening without capacitors (firmware + wiring, done first)
- **Soft start and ramp:** duty ramps over ~200 ms (never a step); reversing coasts ~150 ms first; the two motors start **staggered** (50 ms apart); a lower initial duty cap that rises only if the rail stays healthy.
- **Rail-sag telemetry and back-off (needs the two dividers):** the firmware samples the rail while starting; if it dips below a threshold it lowers the duty cap and reports the minimum seen. Without the dividers it logs ESP32 brownout resets (reset reason on boot) and reports them.
- **Low-impedance dividers instead of capacitors:** pack 22 kΩ over 10 kΩ (about 2.6 V at 8.4 V) to GPIO34; rail 10 kΩ over 10 kΩ to GPIO35; the firmware oversamples (64 reads, median) to remove noise.
- **Wiring habits that cost nothing:** short, thick motor and power leads; twist each motor's two wires; one common ground point at the buck output; set the buck with a multimeter before connecting boards; keep the camera lead short. If you can salvage a 470-1000 uF electrolytic from any old charger or power supply, put it across the DRV8833's motor power pins (observe polarity); it is optional, not required.
- The camera may still brown out when the rail sags: the firmware lowers camera frame rate/quality while the motors start, and the health check reports it.

## 3. How the loop measures with the sensors you actually have (layered by reliability)
1. **Guided measurement (always works):** bounded runs along a tape line or toward a printed target; the app asks "how far left/right of the line, and how far did it travel?" (ruler). About 30 s of your effort per test.
2. **Camera (automatic when confident):** target tracking and frame phase-correlation as a cross-check; low confidence -> it asks for the ruler.
3. **Human-observed checks:** yes/no for "did the left wheel turn forward?", "hum but no move?".
4. **Sensor ladder, on your word:** battery/rail dividers (weak power becomes measurable), MPU6050 (direct turn rate), one encoder (one wheel's speed), HC-SR04/IR (obstacles). Each raises confidence without changing the flow.

## 4. Milestones: hardware first; each ships alone; full suite green; commit per milestone
- **H0 First-contact tool (`tools/rover_bringup.py` + Rover health check):** finds the port, identifies the board, checks the firmware answers (version), boot reset reason (brownout?), watchdog, guided motor check with the wheels off the ground (per wheel and direction yes/no; wiring inversion, dead or humming motor), camera check (reachable, fps, brightness, texture/blur, usable for camera measurement?). Everything logged to `runs/rover/<time>/`. **Gate:** you plug it in and run it; we read the log.
- **H1 Firmware v2 + safe flashing:** version/hello line, soft start/stagger/ramp, rail-sag back-off, runtime config in NVS (per-motor trim, invert, minimum duty), firmware-enforced timed bounded move, same watchdog and boot-coast; a recovery build of your known-good `rover_wasd` behaviour always available; flashing with arduino-cli only after your confirm click (exact manual steps if it cannot); sensor support behind compile flags (dividers first, then MPU6050/encoder/HC-SR04/IR as you confirm them). **Gate:** flash on the real board, health check passes, no brownout on starts.
- **H2 Synthetic rover (in parallel):** same serial protocol, camera frames from the simulator's onboard camera, injectable faults (weak motor, inverted wheel, humming motor, rail sag/low battery, tilted camera). Used for development, CI and as a labelled "simulated rover" demo fallback.
- **H3 Reality-gap engine (`simlab/twin/`):** test protocol, measurement layers, deterministic diagnosis (motor imbalance and side, asymmetric turns, start threshold, power sag, camera tilt, dead/inverted motor) with evidence and confidence, twin calibration (`calibrate.fit`, `side_gain`, yaw gain, speed scale), trim computation, before/after. Tested with every fault on the synthetic rover. **Gate:** real drift measured and diagnosed.
- **H4 Correct, verify, persist, deploy:** apply the trim (firmware NVS + `config/hardware.json` + calibration), re-simulate, re-run on the rover, before/after labelled "verified on hardware for these tests" (never "safe"); generate and flash the firmware from a template (confirm first). Detection runs on the laptop from the camera stream (96x96 is too small for the ESP32). **Gate:** the corrected rover drives straighter than before.
- **H5 Chat flow:** cards and intents for all of the above (inventory "I have...", Rover card: connect/flash/checklist/Stop; Reality card; Deploy card), the matcher fix (a ready-made job needs 2+ keyword hits, so the plant monitor is no longer the boat) and the "using what I have vs best" comparison.
- **Later, after the rover loop is proven:** U (Claude-desktop style sidebar and panes), N (non-moving devices, blueprint-only), S (further sensor upgrades), D (docs and brief).

## 5. Safety rules (enforced in code)
No movement without the server-side checklist; tests bounded by the firmware; watchdog stays; Stop always on screen; first test always with the wheels off the ground; no free driving and no global key handler on the chat page; flashing and trim changes only after a confirm click; every real run logged. Li-ion: the pack needs a protection board and a proper 2S charger; the app warns at 7.2 V and refuses motion below 6.8 V pack once the battery divider exists.

## 6. Verification
`pytest -q` at every gate (727 now): soft-start/stagger logic on the emulator, every fault diagnosed with the right evidence (12 % weak right motor -> right side, correct trim; inverted wheel; humming motor; rail sag; tilted camera), low confidence falls back to the ruler, end-to-end on the synthetic rover, firmware compile test (skipped without arduino-cli), flash refuses without the confirm token, matcher tests including the plant-monitor sentence. **Real-rover gates (you present, I read the logs):** G0 first contact, G1 flash + health, G3 real drift diagnosed, G4 correction verified.

## 7. Honest limits
Until the rover is connected only the synthetic rover proves the software; no capacitors means brownouts are reduced by software, not eliminated; the camera alone measures only a few degrees at best; the firmware has one speed level and no arcs; without the battery divider there is no battery reading.
