# Hardware guide: ESP32 + ESP32-CAM + Arduino + DRV8833 + 4×AA NiMH

Roles (chosen for the parts you have):

| Board | Job | Why |
|---|---|---|
| **ESP32 DevKit** | rover brain: Wi-Fi, UDP `(v, ω)` commands, DRV8833 motor control, battery watch, failsafe | plenty of PWM pins; stays responsive because it does *no* video |
| **ESP32-CAM (AI-Thinker)** | camera only: MJPEG stream | camera + Wi-Fi already saturate it; the GPIOs left over are too few and shared with the SD slot |
| **Arduino Uno/Nano** | (1) USB-serial programmer for the ESP32-CAM, (2) optional 5 V sensor hub (gas, ultrasonic) | ESP32-CAM has no USB; the Uno's ADC works with 5 V sensors and is not disturbed by Wi-Fi (the ESP32's ADC2 is unusable while Wi-Fi is on) |
| **Laptop (RTX 5050)** | runs detection, tracking, the dashboard | the ESP32-CAM cannot run a useful detector |

## Parts
ESP32 DevKit · ESP32-CAM (AI-Thinker) with its OV2640 · Arduino Uno/Nano · DRV8833 module · 2 DC gear motors rated ~3-6 V (e.g. TT motors) with wheels + a ball caster (2WD chassis) · 4×AA NiMH holder + cells · **5 V boost module** (MT3608-class, adjustable, ≥2 A) · 3 × 470 µF capacitors · 2 × 100 kΩ resistors · jumper wires. Optional: external white LED + resistor/MOSFET, MPU6050, wheel encoders.

## Power: the part most likely to bite
4×AA NiMH is ≈4.8 V nominal (≈5.6 V fresh, ≈4.0 V spent). Both ESP32 boards use an AMS1117 linear regulator with a 1.1-1.3 V dropout, so they need **≥ ~4.6 V in**. Wired straight to the cells they will brown-out and reset whenever the motors load the pack.

```
4xAA (+) ──┬────────────► DRV8833 VM          (+ 470 µF across VM/GND, 10 µF ceramic right at the chip)
           ├──► 5 V boost (set to ~5.1 V) ──┬─► ESP32 DevKit 5V/VIN  (+ 470 µF)
           │                                └─► ESP32-CAM 5V         (+ 470 µF)
           └──► 100k ──┬── 100k ──► GND      ┬─ midpoint ► ESP32 GPIO34 (battery sense)
4xAA (-) ──────────────────────────── common ground to every board
```
* Set the boost output **before** connecting the ESP32s (use a multimeter).
* The firmware cuts the motors and **latches** at 4.0 V (1.0 V/cell) because NiMH voltage rebounds when unloaded; press *Reset battery latch* after recharging.
* Brown-out ("Brownout detector was triggered") on the ESP32-CAM = power, not code: short thick wires, capacitor at the 5 V pin, never power it from the Arduino's 5 V pin for streaming.
* The ESP32-CAM's bright flash LED (GPIO4) gets very hot: leave it off, or use an external LED.

## Wiring

**ESP32 DevKit → DRV8833** (matches `firmware/esp32_rover/config.h`)

| ESP32 | DRV8833 | |
|---|---|---|
| GPIO25 | AIN1 | left motor |
| GPIO26 | AIN2 | left motor |
| GPIO27 | BIN1 | right motor |
| GPIO14 | BIN2 | right motor |
| GPIO33 | nSLEEP | HIGH = enabled |
| GPIO32 | nFAULT | pull-up, LOW = over-current/over-temperature |
| GPIO34 | (battery divider midpoint) | ADC1, safe with Wi-Fi |
| GND | GND | common ground |

Motor wires to AOUT1/AOUT2 and BOUT1/BOUT2. If a wheel spins the wrong way, set `LEFT_INVERT`/`RIGHT_INVERT` in `config.h` or swap its two wires. Check your module's chip package: the PWP/RTY package is rated 1.5 A RMS / 2 A peak per bridge, the small PW package only 0.5 A RMS: size the motors accordingly (stall current!).

**Arduino sensor hub → ESP32 (optional):** MQ-4 → A0, MQ-135 → A1, HC-SR04 TRIG → D7, ECHO → D8. Arduino TX → 10 kΩ/20 kΩ divider → ESP32 GPIO16 (the ESP32 is 3.3 V only). Readings arrive in the telemetry as `hub`. MQ sensors need 24-48 h of burn-in and are only indicative.

## Flashing
Install the Arduino-CLI cores once (already present on this machine: `esp32:esp32` 3.3.12, `arduino:avr`).

1. **Check everything compiles** (Hardware tab → *Firmware & wiring* buttons, or):
   ```powershell
   arduino-cli compile --fqbn esp32:esp32:esp32     firmware\esp32_rover
   arduino-cli compile --fqbn esp32:esp32:esp32cam  firmware\esp32cam_stream
   arduino-cli compile --fqbn arduino:avr:uno       firmware\arduino_sensor_hub
   ```
2. Put your Wi-Fi name/password into both `config.h` files (`firmware/esp32_rover/config.h`, `firmware/esp32cam_stream/config.h`).
3. **ESP32 DevKit:** plug in USB → `arduino-cli upload -p COMx --fqbn esp32:esp32:esp32 firmware\esp32_rover`.
4. **ESP32-CAM using your Arduino as the programmer** (no FTDI needed):
   * Arduino **RESET → GND** (this disables the Arduino's own chip so its USB-serial part is just a bridge).
   * Arduino 5V → ESP32-CAM 5V, GND → GND, Arduino **RX → ESP32-CAM U0R**, Arduino **TX → U0T** (RX to RX, TX to TX: yes, straight through).
   * **IO0 → GND** on the ESP32-CAM, then press its RST button.
   * `arduino-cli upload -p COMy --fqbn esp32:esp32:esp32cam firmware\esp32cam_stream` (COMy = the Arduino's port).
   * Remove the IO0 jumper, press RST. The serial monitor (115200) prints `Camera ready: http://<ip>:81/stream`.
   * "Timed out waiting for packet header": IO0 not grounded, RX/TX crossed, or weak 5 V (use the boost supply).
5. **Arduino sensor hub (optional):** remove the RESET→GND jumper, then `arduino-cli upload -p COMy --fqbn arduino:avr:uno firmware\arduino_sensor_hub`.

## First run checklist
1. Wheels **off the ground**. Power on. The rover's LED blinks while it looks for Wi-Fi, then a short blink every 1.5 s.
2. Browser: `http://<rover-ip>/` should show JSON with `vbat`. `http://<cam-ip>/capture` should show a picture.
3. Dashboard → Hardware tab → rover `<rover-ip>`, camera `http://<cam-ip>:81/stream` → *Connect*. Both pills turn green; battery V and Wi-Fi dBm appear.
4. Hold ▲: both wheels forward. Release: they stop. Unplug Wi-Fi (or close the dashboard): they stop within 0.3 s (failsafe).
5. If it does not connect: Windows Firewall may block Python's UDP replies (allow `python.exe` on private networks); laptop and boards must share a network (a phone hotspot works); fallback access points are `SEWERBOT-ROVER` / `SEWERBOT-CAM` (rover at 192.168.4.1).

## Calibration (Hardware tab → Calibration)
* **Speed:** *Drive* runs the rover at the command speed for N seconds; measure the distance and enter it → top speed is corrected and stored in the ESP32's flash.
* **Turning:** *Spin* turns for N seconds; enter the measured angle → the yaw gain is corrected. (Skid-steer/2WD rovers turn less than the maths predicts; the simulator's presets show gains of 0.65-0.87.)
* Dimensions, camera height/tilt/FOV and the test-pipe diameter go into *Rover & pipe setup*: they drive both the fit check and the positions of found defects.

## Mounting the camera
From ~8 cm up, a level camera sees mostly floor. Tilt it **up ~10°** (the `micro_2wd` preset does the same) and keep the lens ≥ 8 cm above the floor. Add light: an external LED aimed forward matters more than resolution.

## Limits
* Wi-Fi does not travel well underground: use this for bench/test-pipe runs, or add a tether / on-board recording for the field.
* ESP32-CAM throughput: ~12-13 fps at SVGA, ~25 fps at CIF in published tests; VGA is the default.
* No encoders → distance along the pipe is estimated from commanded speed (calibrate it). Add encoders or an IMU to improve it.

## The USB-serial rover (the rover as it is actually built)

The rover on the bench is an ESP32 DevKit on a **USB cable** (no Wi-Fi), a DRV8833 on GPIO 26/27 (left) and 32/33 (right), and a separate ESP32-CAM board for video. The Wi-Fi firmware above does not match that wiring, so the dashboard also speaks the rover's own serial protocol.

* **Firmware:** `firmware/esp32_rover_serial/esp32_rover_serial.ino` is your `rover_wasd` sketch (kept as `rover_wasd_original.ino.txt`) plus: a telemetry line every 0.5 s, a watchdog that is always armed while the rover moves (1 s), and optional sensors. Check that it compiles with the **Check USB rover** button, then flash it like any sketch (`arduino-cli upload -p COM6 --fqbn esp32:esp32:esp32 firmware\esp32_rover_serial`). The default build has **no sensors enabled**, so it behaves like the sketch you already run.
* **Sensors (not fitted yet):** build with `-DENABLE_ULTRASONIC=1` and/or `-DENABLE_IR=1`. HC-SR04 TRIG → GPIO18, ECHO → GPIO19 **through a 10 kΩ / 20 kΩ divider**; IR module OUT → GPIO21 (most modules pull OUT low when they see something). With a sensor enabled, forward motion is refused at 15 cm, and also when the distance sensor gives no valid reading (so an unplugged sensor cannot look like a clear path).
* **Connect:** Hardware tab → type `COM6` (or press *Find USB ports*) → Connect. Only one program can hold a COM port: close the Arduino serial monitor and `wasd_control.py` first. Opening the port resets the ESP32; the dashboard waits 2.5 s, then sends a stop.
* **Before it drives:** the *Before you drive* card must be fully ticked and unlocked (wheels off the ground or the area clear, battery ≥ 4.4 V, Stop tested, power switch within reach). The unlock is checked on the server, not only in the browser. **Stop always works.** If the dashboard or the cable dies, the rover stops by itself within 1 s.
* **What it can do:** drive forward, reverse and spin on the spot at one of 100–255 speed levels (the firmware has no arcs and no proportional speed). Speed in m/s and turn rate are estimates until you run *Calibration* (drive, measure the distance, Apply).
* **Not possible with this hardware:** autonomous route following (no encoders or IMU), battery readings (no divider), Wi-Fi control.
* **Try it without the rover:** *Try USB emulator* runs the same protocol, watchdog and sensor behaviour in software.
