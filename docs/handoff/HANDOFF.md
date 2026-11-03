# HANDOFF: phaltan4_similator (simlab / sewerbot rover app)

Project folder: C:\Users\Parth\Desktop\phaltan4_similator (Windows, not a git repo itself).
Git clone used for commits: C:\Users\Parth\Desktop\phaltan4_similator\hack-odyssey-target
(remote https://github.com/college-hack24-nov26/hack-odyssey.git, branch main, user ParthVarekar <parthvarekar27@gmail.com>).
Python: .venv\Scripts\python.exe. Tests: `.venv\Scripts\python.exe -m pytest -q` (set SIMLAB_WEBOTS_PORT to a free port; the Webots tests fail if port 8770 is held).

## Goal
Judges' demo: a chat-first app where a user describes a job; the system makes requirements, a blueprint, an equipment list
(asks what they already own), names the test environment, simulates, asks to connect the real rover, tests it physically,
finds sim-vs-real gaps, fixes them, verifies, and writes/flashes firmware. Rover first. Demo task: rover follows a bag, orbits it,
goes bag to bag, ends at a goal box (goal object flexible; user will send photos of bag and box later).

## Hardware (user's word)
ESP32 DevKit + DRV8833 (left motor GPIO26/27, right 32/33), ESP32-CAM (96x96, Wi-Fi), MPU6050 (fitted, NOT yet moved),
HC-SR04 being set up (not confirmed working), 2S Li-ion -> LM2596 buck (5.2-5.7 V), NO capacitors, NO battery/rail dividers,
rotary encoder and IR dropped.
Pin conflicts found: MPU SDA/SCL on 32/33 = right motor pins -> move MPU to GPIO21/22. HC-SR04 TRIG on GPIO34 is input-only -> TRIG GPIO18,
ECHO GPIO35 via 10k/20k divider. Pin map in config/hardware.json "pins" (sewerbot/hardware/pins.py).
Real-rover gates G0 (first contact), G1 (flash + health), G3 (gap diagnosed), G4 (correction verified), G5 (bag mission), D3 (rehearsals) are the USER's to run; never claim hardware results before reading their runs/rover logs.

## Plans (approved)
- Current: plans/lazy-wondering-floyd.md (v5, approved 2026-11-04). Phases: A (rover loop: A0 bring-up, A1 firmware v2, A2 emulator, A3 gap engine, A4 correct/verify/flash), M (bag/box mission), B (project backend), U (Claude-desktop-style UI), C (flow cards), D (demo hardening).
- Older (background): flickering-cooking-fountain.md (rover v4), lexical-stirring-firefly.md (chat-first, built), dreamy-weaving-blossom.md (planner), swift-pondering-twilight.md (Webots), others unrelated.

## Done (all built and tested in emulator; NOT on real hardware)
- firmware/esp32_rover_v2/esp32_rover_v2.ino: '!' protocol (HELLO, I2CSCAN, GYRO, CALGYRO, CFG, SAVE, RESETCFG, D, MOVE, STOP), soft start, staggered starts, gyro heading-hold (bounded, self-disabling), HC-SR04 guard only when usguard=1, brownout counter, optional Wi-Fi TCP (wifi_secrets.h generated into a temp copy only). Compiles with arduino-cli (esp32:esp32 3.3.12) with and without Wi-Fi.
- firmware/esp32_rover_serial: old recovery build (kept).
- sewerbot/hardware/serial_rover.py: v2 parsing (ask, run_move, drive_duty, parse_kv), us_guard flag, mock-v2 port option.
- sewerbot/hardware/mock_v2.py: v2 emulator; faults side_gain, wheel_sign, motor_min, sag_prob, mpu_present/pins, gyro bias/sign, reset_reason.
- sewerbot/hardware/{pins,bringup,flash}.py; tools/rover_bringup.py, tools/flash.py (flash needs --confirm FLASH; manual steps otherwise).
- simlab/twin/ (model, protocol, diagnose, calibrate, engine); tools/rover_gap.py (needs YES; SAVE to persist).
- simlab/missions/ + simlab/tasks/bag_mission.py (M0/M1, built by another session, sim only).
- simlab/project/ + simlab/dashboard/project_router.py + goal matcher 2-keyword fix (built by another session).
- Chat shell: simlab/dashboard/static/chat-shell.js (sidebar + panes), chat.* updated (built by another session).
- Brought brain/hardware.py: 2S pack checklist text, serial_cal us_guard, pins loaded from config.
- Tests: tests/test_rover_v2.py, tests/test_twin.py (all pass alone). Full suite: 912 passed, 5 failed (Webots tests in tests/test_simlab_webots.py fail only in full run, pass alone 7/7: UNEXPLAINED), 1 skipped.

## Git
- ecf89cc: rover firmware v2 / tools / gap engine (push 1, done).
- e58cea7: missions, project backend, chat shell (push 2, done, 45 min later).
- Both authored without Claude co-author line. Clone is in sync with origin/main at push time.
- .gitignore has config/wifi.json and firmware/**/wifi_secrets.h. Never commit config/wifi.json or any API key.
- Untracked work since push 2: tools/record_demo.py (demo recorder, WIP), docs/media/ (empty so far). Not pushed.

## IN PROGRESS: the README + videos + banner task (user request, not yet done)
User asked: nice README using ideas from https://github.com/matiassingers/awesome-readme; record videos of the software for the README;
a themed banner image at the top; push to GitHub.
Status:
- Read the current README.md (64 lines, sewerbot + simlab description). Not yet rewritten.
- Got awesome-readme's examples list via browser (elements: banner/logo, badges, GIF demos, TOC, quick start, screenshots, contributing, license). Best examples noted: alichtman/shallow-backup, amitmerchant1990/electron-markdownify, dsplce-co/supabase-plus (GIF per feature), doomemacs, electrikhq/slate, create-go-app/cli.
- Started dashboard on port 8010 (SIMLAB_WEBOTS_PORT 8781) and quiet dashboard on 8011 (config {"ai":"never"} at scratchpad chat_quiet.json; SIMLAB_WEBOTS_PORT 8782). These may still be running; kill them if not needed (python -m sewerbot processes).
- Wrote tools/record_demo.py (headless Chrome via DevTools websocket, screenshot frames, ffmpeg to mp4/gif). First run: "no frames were captured". Debug showed screenshots work alone; the frame loop failed inside the scene run. Last test call was interrupted; fix not yet verified. Suspect: Recorder's second websocket connection / the first connection closed on navigate, or frames empty because loop thread got errors (swallowed by except). Fix: log exceptions in _loop.
- ffmpeg is installed (C:\Users\Parth\AppData\Local\Microsoft\WinGet\Links\ffmpeg). Chrome at C:\Program Files\Google\Chrome\Application\chrome.exe.
- Browser pane (not used for recording) showed /chat works: chip "Rover that inspects a 30 cm sewer pipe" starts an AI planning card (AI setting auto uses free OpenRouter quota).
- Banner: not designed yet. Planned: theme "rover on a bag, sewer pipe, blueprint" SVG/PNG, dark, ~1280x320, saved to docs/media/banner.png (render with PIL or an SVG rasterised by Chrome).
- Still to do: 1) fix recorder, 2) record chat + shell (+ optionally terminal replay of tools/rover_bringup on serial:mock-v2 and tools/rover_gap), 3) banner, 4) rewrite README (TOC, badges, banner, GIFs, quick start, honest limits: sim-only until hardware gates; Co-Authored-By none), 5) verify links, 6) commit and push (no Claude co-author line; the system reminder version says Claude Haiku 5.5 — user asked to REMOVE Claude from commits, so omit any attribution), 7) report.

## Open items / warnings
- Real rover not yet connected. Port changed? Old COM6; bring-up will report the actual port.
- Docs not updated for rover v2 (docs/HARDWARE.md still lists old pins; docs/ROVER.md planned, not written).
- Webots test failures in full suite unexplained (port 8770 suspicion; another session also saw it).
- OpenRouter key: user pasted a key in chat earlier; it must not be written anywhere. Keep key out of files and commits.
- Stray python.exe processes (earlier, PIDs 16800, 18660, 20460) may still exist; user may end them.
- Other linked sessions (idle): sewage-rover-simulati-2 local_85764c6c-83cc-4d7b-a72d-daf085642f97; bag-box-mission local_3d9ecb00-8c3a-40db-88ec-2e351ad8d64d; new-app-shell local_257c2d88-8e8d-465a-b352-dbb229a0d727; project-flow-backend local_64036cd3-ee62-4942-97e3-c26b3695de7c.

## Style preferences from the user
- Explanatory output style (short insight boxes allowed).
- Plain words, no overclaiming ("verified on hardware for these tests" only; never "safe" or "proven").
- Push to GitHub only when asked; commits without Claude attribution when the user asked for that; two pushes spaced ~45 min were requested for the earlier batch.
- Ask before flashing, trim changes, moving the rover, sending messages.

## How to resume (first steps in a new session)
1. Read this file and plans/lazy-wondering-floyd.md, and memory/*.md (copies here).
2. Check running servers: `curl localhost:8011/api/chat/capabilities`; restart with `.venv\Scripts\python.exe -m sewerbot --no-browser --port 8011` if needed.
3. Continue the README task from "IN PROGRESS" above, or ask the user whether to first wait for real-rover results.

---

## YOUR TO-DO LIST (everything physical, flashing and running that is still yours)

Status note: all code for A0 to A4 exists and is tested in the emulator. Nothing has run on the real rover. Each step below needs you. Tell me what you saw after each one.

### Step 0. Before touching the rover
- Charge the 2S Li-ion pack on a proper 2S charger. Never charge it unattended. Check it has a protection board (BMS) and an inline fuse (2 to 3 A). Check it on a multimeter: 7.2 V or more.
- Keep the rover on a block, wheels OFF the ground, for every power-up.
- Have a clear, flat floor ready: about 1.5 m straight ahead, plus a tape line if you have one.

### Step 1. Rewire (power off, pack unplugged)
- MPU6050: VCC to ESP32 3V3, GND to GND, SDA to GPIO21, SCL to GPIO22, AD0 to GND. Move the old wires off GPIO32/33 (those are the right motor).
- HC-SR04: VCC to 5 V rail, GND to GND, TRIG to GPIO18, ECHO to GPIO35 through the 10k/20k divider (10k in series, 20k to ground, junction to GPIO35).
- Do NOT use GPIO34 for TRIG (it is input-only).
- Leave out the battery and rail dividers for now (not fitted; the app does not need them yet). Optional later: 22k/10k to GPIO34 and 10k/10k to GPIO35.
- Power: buck output set to 5.2 V (measure with a multimeter before connecting the boards). Short thick power wires, one ground point.

### Step 2. Wi-Fi config (only if you want the Wi-Fi link later)
- Create `config\wifi.json` in the project folder with `{"ssid": "YOUR_NETWORK", "password": "YOUR_PASSWORD"}`. It is git-ignored. Do not paste the password in chat.
- Not needed for the first contact. USB is enough.

### Step 3. Compile check (touches no board)
```
.venv\Scripts\python.exe -m tools.flash --check
```
Expected: "compile OK". If it fails, send me the output.

### Step 4. Flash firmware v2 (the rover must be connected by USB)
- Close the Arduino serial monitor and any other program using the COM port.
- Find the COM port in Device Manager (it was COM6 before; check again).
```
.venv\Scripts\python.exe -m tools.flash --port COMx --confirm FLASH
```
- If the upload says "Failed to connect", hold the BOOT button on the ESP32 until it says Writing, then release.
- Manual fallback (if the tool cannot): `.venv\Scripts\python.exe -m tools.flash --steps` prints the Arduino IDE steps.
- Recovery (old known-good firmware): `--sketch recovery --confirm FLASH`.

### Step 5. First contact and bring-up (wheels off the ground for the motor part)
```
.venv\Scripts\python.exe -m tools.rover_bringup --port COMx
```
- Add `--camera http://<camera-ip>:81/stream` to check the ESP32-CAM (last known 192.168.29.218; check it).
- Add `--drive` only when you are next to the power switch and the wheels are off the ground. It will ask you to type YES.
- It asks questions: the multimeter readings (pack voltage, buck output), the by-hand 90 degree turn (left, anticlockwise from above), and which wheel turned which way.
- Output: `runs\rover\<time>\log.txt` and `report.json`. Send me both files.
- Any FAIL or LOOK item: stop and send the log first.

### Step 6. Confirm the HC-SR04 (only when Step 5 shows it reading sensibly)
- Wave a hand in front of it. The reading should change. Tell me it works. Only then will the app let it stop the rover (usguard). Until you tell me, it does not block driving.

### Step 7. Reality-gap test (the main error-correction loop)
- Clear flat floor, about 1.5 m ahead, rover on the floor, wheels down this time, you next to the power switch.
- Run:
```
.venv\Scripts\python.exe -m tools.rover_gap --port COMx --environment "tile floor, 1.5 m clear"
```
- Type YES when asked. It drives short bounded motions (about a minute).
- It prints findings and a saved folder `runs\rover\<time>\gap.json` and `gap.txt`. Send me the folder.
- If it recommends a trim, the tool asks before applying. Only type YES if you agree. Type SAVE only after the re-test looks right.

### Step 8. Verify the correction
- After saving, run the gap test again. The straight-run drift should be near zero. Send me the new folder.
- Optionally run `--apply` again to re-check with the trim.

### Step 9. Bag and box targets (needed for the judges' demo)
- Send photos of the bag and the box (3 to 10 photos each, different angles and light).
- Tell me the real size of each in centimetres (height and width).
- Tell me the floor colour and whether the bag is one colour.
- Until then the software uses stand-in coloured objects in the simulator only.

### Step 10. Mission on the real rover (after G4 works)
- I will prepare it. You will need a clear floor of about 3 m by 3 m, two bags and a box, the rover on the floor, you next to the power switch.
- Needs the Wi-Fi link (Step 2) so the rover is not tethered.
- Do not run it without me reading the earlier logs first.

### Step 11. Demo rehearsal (twice)
- Charge the pack fully the day before.
- Run the app: `.venv\Scripts\python.exe -m sewerbot` and open http://127.0.0.1:8000/chat.
- Keep a screen recording as backup.

### Software tasks still open (mine, not yours)
- README rewrite, banner image, demo videos (recorder has a bug, see above), docs/HARDWARE.md pin update, docs/ROVER.md, then commit and push (no Claude attribution line).
- Failing Webots tests in the full suite (5), cause unknown.
- A4 firmware generation from a template is NOT built. Only trim apply and SAVE through the tool exist.
- Real-rover results are needed before any "verified on hardware" wording.

### Reminders
- Never claim the rover is safe. Only "verified on hardware for these tests".
- Every flash, trim change and power-on with the wheels down needs your explicit confirmation.
