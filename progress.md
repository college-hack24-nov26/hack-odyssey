# RoboForge: progress

Last updated: 2026-11-04. Kept up to date with each push. Status words: **done** (in the repo and tested), **built, not verified** (code exists, not run on the real hardware), **planned**, **blocked on you** (needs a physical step or your decision).

## Goal
Describe a job in plain words. The software plans a machine from real parts, simulates it, runs the real rover through a bring-up that finds faults (a slow motor, a tilted camera, weak power) and corrects them, calibrates the model to the real machine, then sets up the code and any small recognition model on the device. The rover is the first target and the one the judges see.

## Done
- **Chat-first app** at `/chat`: one conversation with live cards (understanding, design, watch, tests, results, parts, AI improve). Deterministic routing; the AI plans only for a new job. Test suite was 727 passed, 1 skipped at that point.
- **Simulator** for ground rovers, boats and observing drones, with a parts Builder, stress tests and build sheets.
- **Chat shell** (Claude-desktop style): sidebar of projects and chats, right-hand panes (Plan, Simulation, Tests, Parts, Rover, Background tasks).
- **Bag/box mission module** (`simlab/missions`, `bag_mission` task): the same runner is meant to drive the sim and the real rover. 68 tests.
- **Rover firmware v2** (`firmware/esp32_rover_v2/`): '!' command protocol, soft start, gyro heading hold, runtime sensor guard, optional Wi-Fi link. Host side: `sewerbot/hardware/{bringup,flash,pins,mock_v2}.py`, `tools/rover_bringup.py`, `tools/flash.py`. Tests in `tests/test_rover_v2.py`.
- **Pin map** in `config/hardware.json` (`sewerbot/hardware/pins.py`), with the conflicts found in the wiring: MPU on GPIO 32/33 clashes with the right motor, and HC-SR04 TRIG on GPIO 34 is input-only. Recommended moves are MPU to 21/22, TRIG to 18, ECHO to 35 through a 10k/20k divider.
- **Project backend** (`simlab/project/`) and the RoboForge rename with banner and demo clips.

## Built, not verified
- The rover v2 firmware has **not been flashed** to the real board.
- The mission runner has been tested only in the simulator. Hit rate and detection range on **real photos of the bag and box** are unmeasured; no real photos are in the repo yet.
- The planner's rover-loop steps (find the fault, correct it, re-run) exist only as design, not as working code.

## Planned (order from the approved plan v5)
1. **A: rover loop.** Run bring-up on the real rover, measure the fault list, correct it, re-run, and write `runs/rover/<time>/log.txt`.
2. **M: bag/box mission on hardware.** Follow the bag, orbit it, go bag to bag, end at the goal box.
3. **B: project backend** for saved projects (currently in localStorage).
4. **U: UI polish** for the chat shell.
5. **C: flow cards** for requirements, equipment, the rover loop, and the build.
6. **D: judges' demo hardening**, with the rover as the showcased outcome.

## Repository housekeeping
- Commit messages from Claude sessions carry a `Co-Authored-By: Claude` line, which makes GitHub list Claude as a contributor. Ten commits have it: three by us (`33e1a52`, `0f043b3`, `0ccdc72`) and seven by Om Tawde's earlier work. Commits after this note carry no such line.
- The cleaned history for our three commits exists locally as `rewrite/strip-claude-trailers`. Pushing it needs a force-push to `main`, which was not allowed in the session; a backup is in `backup/pre-trailer-strip`.

## Blocked on you
- **Commit history:** approve the force-push of `rewrite/strip-claude-trailers`, and agree with Om Tawde on whether his seven commit messages should be cleaned too.
- **G0/G1 hardware gates:** wire the rover to the fixed pin map, then run the bring-up. Nothing about the real rover is claimed until its log exists.
- **Flashing:** the firmware is flashed only after you confirm and I have given you the steps.
- **Photos** of the bag and box, for target recognition.
- **HC-SR04:** tell us when it works, since it is still being set up.

## Honest limits (carry into every report)
- The simulator and the real rover are not calibrated against each other yet.
- No measured results on real hardware exist yet.
- Estimates are estimates until calibrated.
