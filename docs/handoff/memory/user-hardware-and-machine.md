---
name: user-hardware-and-machine
description: "Parth's rover hardware parts and laptop specs/constraints (GPU, disk, network speed) for the sewerbot project"
metadata:
  node_type: memory
  type: user
  originSessionId: dc501a60-1a80-4f12-a4a4-1642c261ee13
  modified: 2026-10-29T20:38:03.005Z
---

Hardware owned (confirmed by the user): Arduino (model unknown, assume Uno/Nano), ESP32 DevKit, ESP32-CAM (AI-Thinker), **DRV8833** motor driver, **4x AA NiMH** battery pack (~4.8 V). Motors/chassis/encoders/IMU not stated (assumed small 3-6 V gear motors, 2WD, no encoders).

Laptop: Windows 11, Intel Core 7 240H (10C/16T), 23.6 GB RAM, **RTX 5050 Laptop 8 GB** (Blackwell, needs PyTorch CUDA >= 12.8), Intel iGPU too. Only ~42 GB free disk (of ~675 GB) at project start.

Network: downloads run ~0.85 MB/s, so large wheels (CUDA PyTorch ~2.6 GB) take ~50 min and `uv` hits its stall timeout; use resumable `curl -C -` into a scratch folder and verify SHA-256.

Default python is 3.14; the project uses a 3.11 uv venv (`.venv`).

**Update 2026-11-01:** the user destroyed their DRV8833 while building the rover (motors hummed without spinning earlier). The physical rover is down; the user is now focusing on the software/simulation side (Goal tab, Builder, stress tests, MuJoCo + Webots). Do not plan live-hardware steps until they say a new driver is in; the USB-serial driver and firmware stay emulator-tested only. If a replacement driver is bought, the Builder already warns that a 0.5 A PW-package DRV8833 cannot take a 1.2 A stall (use a bigger driver or current-limit).

**Why:** the plan (Webots + RF-DETR + UDP firmware) was chosen specifically around these constraints. **How to apply:** avoid heavy stacks (WSL/ROS 2/Gazebo), keep datasets small, never assume fast downloads, and design power for 4xNiMH (AMS1117 dropout -> needs a 5 V boost).
