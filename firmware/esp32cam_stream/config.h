// ESP32-CAM (AI-Thinker) streaming configuration.  Board: "AI Thinker ESP32-CAM"
#pragma once

#define WIFI_SSID        "your-wifi-name"
#define WIFI_PASS        "your-wifi-password"
#define HOSTNAME         "sewerbot-cam"           // http://sewerbot-cam.local:81/stream
#define AP_FALLBACK_SSID "SEWERBOT-CAM"           // used if the Wi-Fi above is unreachable for 20 s
#define AP_FALLBACK_PASS "sewerbot123"            // -> camera at http://192.168.4.1:81/stream

// Resolution trade-off on the OV2640 (one published benchmark): ~25 fps at CIF 400x296, ~13 fps at SVGA 800x600.
// VGA (640x480) is a good default: the same resolution the simulated rovers use.
#define DEFAULT_FRAMESIZE FRAMESIZE_VGA
#define JPEG_QUALITY      12          // 10 = best/biggest, 63 = worst/smallest. 12 is a good Wi-Fi compromise.
#define XCLK_HZ           20000000

// Flash LED on GPIO4 is very bright and gets hot: it is OFF by default, adjust with /led?v=0..255 (brief use only).
// For real inspection use an external LED (+ resistor/MOSFET) powered from the 5 V rail.
#define LED_PIN 4
