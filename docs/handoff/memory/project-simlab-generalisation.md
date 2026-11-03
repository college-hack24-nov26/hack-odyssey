---
name: project-simlab-generalisation
description: "simlab (any-machine simulator + Builder, 2026-10-30/31): user decisions, Windows/MuJoCo/JS pitfalls, and where results live"
metadata:
  node_type: memory
  type: project
  originSessionId: d4bae174-9f2d-4c49-962f-ceb3e4a57665
  modified: 2026-11-01T19:11:09.790Z
---

Project: C:\Users\Parth\Desktop\phaltan4_similator. The user asked (2026-10-30) for a configurable, trainable simulator for ANY machine (rover, 4WD, aeroplane...). Approved plan: MuJoCo as fast/trainable backend + Webots kept for the sewer; machines = YAML `VehicleSpec` + kind plug-in; tasks; RL (SB3) and RF-DETR detector training; dashboard tabs Lab/Train. Built as package `simlab/`, docs in `docs/LAB.md` and `docs/RESULTS_LAB.md`. See [[user-hardware-and-machine]] and [[project-sewerbot-webots-gotchas]].

User choices: train BOTH control policies (RL) and camera detectors; first release = ground rovers + multirotors + "anything else" via MJCF/URDF import (fixed-wing deliberately NOT built; JSBSim 1.3.3 installs on py3.11 if wanted); real hardware stays ESP32 rover only (flight-controller/MAVLink adapter not built).

Gotchas that cost real time (Windows, MuJoCo 3.17, torch):
- **All rendering must run on ONE thread** (`backends/mujoco/glthread.py`): GLFW/GL contexts are thread-bound; freeing a `mujoco.Renderer` from the GC thread gave an intermittent access violation (hit ~1 in 5 full test runs). Renderers also need their own context current before `close()`.
- **Segmentation renders must not be anti-aliased**: with torch imported first the driver's multisample resolve blended object ids into wrong ones (phantom boxes). Separate renderer with `vis.quality.offsamples = 0`.
- Parked-item bug class: never reuse a hidden/parked z as the placed z (`Item.rest_z`); tests that only check x,y miss it. The 3D viewer caught it.
- PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM: loaders use `utf-8-sig`. A `..` run id is a valid regex match: ids must start alphanumeric.
- Webots TCP port 8770 is shared by `SimManager`, the Webots backend and its tests: never run them concurrently. `pytest` never launches Webots (fake controller); live check is `python -m tools.webots_smoke`.
- MuJoCo welds a URDF's root link to the world (fixed base). `mj_multiRay` skips geoms in group 3 (decor) via the `geomgroup` mask.
- RF-DETR leaves ~1 GB of checkpoints/run: `perception.train.prune_checkpoints` keeps only best weights (disk is tight).
- Measured honestly: RL policies matched but did not beat the classical baselines (rover touched obstacles ~21 steps/episode); results in docs/RESULTS_LAB.md.

Builder mission (2026-10-31, milestones B0-B7, docs/BUILDER.md): parts library + derivation (`simlab/parts/`), Builder tab, verified part suggestions, calibration, fast MuJoCo pipe environment, drone/boat builds. User constraints: MVP not polished; "what to build from a goal" generator deliberately NOT built; user's real rover has NO measurement in the repo (`micro_2wd` numbers are design assumptions, yaw_gain came from Webots), so predictions are estimates until the user calibrates.
- All dashboard scripts share ONE global JS scope: a duplicate top-level name silently replaces the other (broke the Builder twice: `schedule`, `fillTasks`); `tests/test_parts_api.py` now audits this.
- MuJoCo lights per vertex: a big wall quad renders black unless subdivided into <=0.45 m cells (bisection made slivers: 102k triangles; grid gave ~10k).
- A recommender must skip zero-finding fixes and verify each candidate by re-evaluating the patched build; library data errors (75C pack = 112 A, frame envelope) are caught by sanity tests, not by eye.

Goal-tab mission (2026-11-01, approved by the user; docs/GOAL.md): the rover control is PRIMARY, everything else is built around it. Real rover facts (from two earlier chats, not measured by me): ESP32 DevKit on COM6 over USB serial only (no Wi-Fi), DRV8833 on GPIO 26/27 + 32/33, firmware `rover_wasd` (keys w s a d x e q . t), separate ESP32-CAM-MB (COM7, GC2145, 96x96 ~9 fps, http://192.168.29.218:81/stream), no encoders/IMU; motors hum without spinning (user fixes hardware later); user plans an HC-SR04 + IR sensor. Wi-Fi password lives in the camera sketch: never copy it into the repo. Implemented: `sewerbot/hardware/serial_rover.py` + firmware/esp32_rover_serial (compile-checked, NOT flashed: ask before flashing), server-side pre-drive checklist, Goal tab (`simlab/goals`, `simlab/hazards`, `goal_router.py`, `goal.js`), water_rescue task, hazard hooks via `Sim(hazards=...)`. User wants honesty over polish: reports always list what was not tested, never say "safe".
- Gotchas: package re-exports hide submodules (`simlab.dashboard.goal_router` is the router object: use importlib in tests); the browser caches static files (hard refresh after editing JS/CSS); YAML text with ": " must be quoted; `config/hardware.json` is tracked and the dashboard rewrites it on connect (reset rover_host/cam_url after emulator tests).

AI-planner + visuals mission (2026-11-01/02, approved; docs/PLANNER.md; code in `simlab/llm/`): Goal tab asks an OpenRouter free model to plan, the simulator/Builder verifies. User chose "inkling 975B" but OpenRouter answers it (and inkling-small) with 403 "only available on agentic harnesses" to any plain API client: do NOT disguise this tool as a harness; the default is `nvidia/nemotron-3-ultra-550b-a55b:free` (works, reasoning model, ~1.5-3 min per plan), gated models are skipped and remembered 6 h. The user's key is in the environment variable `OPENROUTER_API_KEY` (never print it); free quota is 50/day shared, refused (403) calls did not count. Reasoning tokens eat `max_tokens` (cap is 8000 now; 3500 cut the JSON off once). The model ignores stated ranges (asked for 100-150 kg payload vs 20 allowed): the validator clamps and lists it, that is by design. Visuals: three.js procedural models in `static/models/`, `env.js` sky/water matching the physics waves.
- Status 2026-11-02: the whole mission (L0-L3, V0-V4, docs) is committed (latest `tools: build_brief`; 479 tests pass). Judges' brief: edit `docs/PROJECT_BRIEF.md`, run `python -m tools.build_brief`, print the PDF with headless Chrome (command in that file's docstring). Not done: nothing live was tried with a user-supplied key in the settings box (the env var worked); user may still send reference photos to refine the models.
- Gotchas: never use `python - <<EOF ... || fallback` in the bash tool (it hangs and leaves `python.exe -` strays; the sandbox refused to kill them); the preview server keeps the old Python until restarted (preview_stop + preview_start); the browser tool aborts a script after ~45 s (poll in slices); `.claude/launch.json` name is `dashboard`.

**How to apply:** read docs/LAB.md and docs/BUILDER.md first; run `pytest -q` (350+ tests); check these before debugging rendering, crashes, or odd labels.
