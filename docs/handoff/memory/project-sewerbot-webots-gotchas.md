---
name: project-sewerbot-webots-gotchas
description: Non-obvious Webots R2025a / Windows / ESP32 pitfalls found while building the sewerbot simulator (not derivable from the code)
metadata:
  node_type: memory
  type: project
  originSessionId: dc501a60-1a80-4f12-a4a4-1642c261ee13
  modified: 2026-10-30T18:59:08.902Z
---

Project: C:\Users\Parth\Desktop\phaltan4_similator (Webots R2025a sim + ESP32/ESP32-CAM hardware tab, shared Python "brain"). See [[user-hardware-and-machine]].

Gotchas that cost real time:
- **ENU world: a `Cylinder`'s axis is z, not y.** Wheels need `Pose { rotation 1 0 0 1.5708 }` (visual AND boundingObject) or they lie flat and the body drags (symptoms: sinking ~2 cm, hopping, 30% speed loss).
- **Webots forbids nested Pose/Transform inside `boundingObject`**: compose rotations into one axis-angle (`tools/gen_world._compose_rot`). Concave IndexedFaceSet collision was unstable in ODE: use box slabs for collision, meshes for visuals only.
- **The `Track` node did not turn the rover** here: tracked = road wheels under a belt visual (kinematically a skid-steer). Skid-steer needs a measured `yaw_gain` (~0.66); 2WD ~0.87.
- `IS` cannot bind part of a vector field (e.g. `rotation 0 1 0 IS tilt`); use `%<= fields.x.value >%`. Physics with `mass` needs `density -1`. `getFromProtoDef` is a Node method (`robot.getSelf().getFromProtoDef`).
- Webots R2025a Python API is pure Python over Controller.dll (no per-version .pyd); controller python is chosen via `controllers/<name>/runtime.ini` `[python] COMMAND=`.
- First Webots launch shows a Welcome dialog (telemetry checkbox pre-ticked) that blocks `--batch`; dismissed via Windows UI Automation. Console text is readable through UIA (`ControlType.Edit`).
- Webots rescales non-power-of-two textures itself: resize baked defect textures to POT.
- Windows UDP: `recvfrom` raises `ConnectionResetError` after an ICMP port-unreachable: use `SIO_UDP_CONNRESET=False` and catch it, or the rx thread dies on a Wi-Fi blip.
- ESP32 parses JSON numbers as float32: send small wrapping timestamps, never epoch seconds.
- CSS: `.grid{display:grid}` overrides the `hidden` attribute; add `[hidden]{display:none!important}`.
- Camera must emulate auto-exposure (`core/imaging.auto_expose`) and be tilted UP (~-10 deg for the 7.5 cm micro rover), otherwise labelled defects are invisible/dark and training learns noise.
- Windows TCP: `SO_REUSEADDR` lets two processes bind port 8770 and connections go to only one (a forgotten dashboard silently ate the rover's hello: "starting Webots" forever, 0 bytes received). Listeners use `SO_EXCLUSIVEADDRUSE`; one dashboard or Webots run at a time; check `Get-NetTCPConnection -LocalPort 8770` first.

**How to apply:** check these first when something in the sim, firmware or dashboard behaves oddly.
