---
name: project-missions-m0-m1
description: "simlab/missions (targets, steps, runner, MissionSim) + bag_mission task: what exists, how the real rover plugs in, sim pitfalls found"
metadata:
  node_type: memory
  type: project
  originSessionId: 19386aae-59eb-4cdb-819e-52c4201202ec
  modified: 2026-11-03T19:35:54.617Z
---

M0+M1 of the bag/box mission plan are built (2026-11-04): `simlab/missions/{targets,steps,runner,sim}.py`, task `simlab/tasks/bag_mission.py` + preset, tests `tests/test_missions_*.py` (68). Hand-over with API and limits: `side-session-notes/local_9683f168-.../M0_M1_missions_handover.md`.

- Runner is MuJoCo-free: `MissionRunner.tick(Obs(frame96, gyro_yaw, t)) -> Drive(left, right, ms)`; `Drive.line()` = `D l r ms`. M2 (real rover, Wi-Fi) reuses it unchanged; needs calibrated `DriveModel`.
- Targets are photos + size (`config/targets/<id>/`, `SIMLAB_TARGETS` redirect); built-in stand-ins bag_a (blue), bag_b (magenta), box (green). No real photos yet: hit-rate on real photos is unmeasured.
- Sim pitfalls: the sim rover's chassis is orange and its edge appears in a centre-mounted camera (blob split, distance +46 %); camera is mounted at the nose in MissionSim. A blob that touches the frame edge must still be shape-checked in its uncut dimension (a thin strip passed before).
- `bag_mission` has no baseline policy (driven via `MissionSim`, not `Sim.step`).
- Webots tests fail when TCP 8770 is held by another process; not related to missions.

**Why:** judges' demo is the rover following/orbiting a bag and ending at a box; the same runner must run in sim and on hardware.
**How to apply:** when touching M2/M3 or the planner vocabulary, extend `STEP_OPS` (closed vocabulary) rather than adding free-form steps; re-measure latency and detection range on the real rover.
