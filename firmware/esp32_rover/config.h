// ESP32 rover configuration. Edit this file, then flash.  (Board: "ESP32 Dev Module")
#pragma once

// ---------------------------------------------------------------- Wi-Fi
// The laptop and both ESP32 boards must be on the same network (a phone hotspot works for bench tests).
#define WIFI_SSID        "your-wifi-name"
#define WIFI_PASS        "your-wifi-password"
#define HOSTNAME         "sewerbot-rover"        // reachable as sewerbot-rover.local
#define AP_FALLBACK_SSID "SEWERBOT-ROVER"        // used if the Wi-Fi above is unreachable for 20 s
#define AP_FALLBACK_PASS "sewerbot123"           // (min 8 characters) -> rover IP is then 192.168.4.1

#define UDP_PORT         4210

// ---------------------------------------------------------------- DRV8833 wiring (left motor = A, right motor = B)
#define PIN_AIN1   25
#define PIN_AIN2   26
#define PIN_BIN1   27
#define PIN_BIN2   14
#define PIN_NSLEEP 33      // HIGH = driver enabled
#define PIN_NFAULT 32      // open-drain, LOW = fault (over-current / over-temperature)
#define PIN_LED     2

// Swap if a motor turns the wrong way (or just swap its two wires)
#define LEFT_INVERT   false
#define RIGHT_INVERT  false

// ---------------------------------------------------------------- battery sensing (4 x AA NiMH)
#define PIN_VBAT_ADC  34        // ADC1 channel: usable while Wi-Fi is on
#define VBAT_DIVIDER  2.0f      // 100k / 100k divider -> measured * 2.0 = pack voltage
#define VBAT_LOW_CUTOFF 4.0f    // 4 cells x 1.0 V; the motors are cut and LATCHED below this
#define VBAT_REFERENCE  4.8f    // nominal pack voltage; duty is scaled by reference / actual (capped)

// ---------------------------------------------------------------- default calibration (changeable at run time from the dashboard)
#define DEF_TRACK_M     0.10f   // distance between left and right wheel centres
#define DEF_VMAX_MPS    0.20f   // ground speed at 100 % duty (measure it with the speed calibration)
#define DEF_YAW_GAIN    0.85f   // measured actual/ideal yaw rate of an in-place turn
#define DEF_DEADBAND    0.30f   // duty needed before the motors start to turn (0..1); raise if it stalls at low speed
#define PWM_FREQ_HZ     20000   // above hearing range
#define MAX_ACCEL_MPS2  0.5f
#define CMD_TIMEOUT_MS  300     // failsafe: stop if no command arrives for this long
#define TELEMETRY_MS    100

// Optional: 5 V sensor hub (Arduino) on UART2: Arduino TX -> divider -> GPIO16.  Set to 0 to disable.
#define USE_SENSOR_HUB  1
#define HUB_RX_PIN      16
#define HUB_TX_PIN      17
