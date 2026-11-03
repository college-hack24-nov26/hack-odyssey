// Sewerbot rover firmware: UDP (v, omega) commands -> DRV8833 -> two DC motors.
// Same command the simulator uses, so the whole brain (detection, dashboard) is shared with the Webots rover.
//
//  laptop -> rover  {"t":"cmd","v":0.12,"w":0.0,"ts":1730000000.1}   every 50 ms, rover stops 300 ms after the last one
//                   {"t":"stop"} {"t":"reset"} {"t":"cfg","track":0.1,"vmax":0.2,"yaw_gain":0.85,"trim":0.0,...}
//  rover -> laptop  {"t":"tel","vbat":4.71,"fault":0,"latched":0,"rssi":-52,"up":12.3,"v":0.12,"w":0.0,"cfg":{...},"ts":...}
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include "config.h"

WiFiUDP udp;
WebServer http(80);
Preferences prefs;

struct Cfg { float track, vmax, yaw, trim, vscale, deadband; } cfg;

IPAddress peerIp;
uint16_t peerPort = 0;
float tgtV = 0, tgtW = 0, curV = 0, curW = 0;
uint32_t lastCmdMs = 0, lastTelMs = 0, lastLoopUs = 0, lowSinceMs = 0;
double lastTs = 0;
float vbat = 0;
bool latched = false, fault = false;
char hubLine[128] = "";
uint32_t bootMs = 0;

// ---------------------------------------------------------------- tiny flat-JSON helpers (no library needed)
static bool hasKey(const char* s, const char* key) { return strstr(s, key) != nullptr; }
static bool getNum(const char* s, const char* key, float& out) {
  const char* p = strstr(s, key);
  if (!p) return false;
  p += strlen(key);
  while (*p == ' ' || *p == ':') p++;
  char* end;
  float v = strtof(p, &end);
  if (end == p) return false;
  out = v;
  return true;
}

// ---------------------------------------------------------------- settings in flash
void loadCfg() {
  prefs.begin("rover", true);
  cfg.track = prefs.getFloat("track", DEF_TRACK_M);
  cfg.vmax = prefs.getFloat("vmax", DEF_VMAX_MPS);
  cfg.yaw = prefs.getFloat("yaw", DEF_YAW_GAIN);
  cfg.trim = prefs.getFloat("trim", 0.0f);
  cfg.vscale = prefs.getFloat("vscale", 1.0f);
  cfg.deadband = prefs.getFloat("dead", DEF_DEADBAND);
  prefs.end();
}
void saveCfg() {
  prefs.begin("rover", false);
  prefs.putFloat("track", cfg.track); prefs.putFloat("vmax", cfg.vmax); prefs.putFloat("yaw", cfg.yaw);
  prefs.putFloat("trim", cfg.trim); prefs.putFloat("vscale", cfg.vscale); prefs.putFloat("dead", cfg.deadband);
  prefs.end();
}

// ---------------------------------------------------------------- motors (DRV8833, slow-decay PWM)
void motorWrite(int in1, int in2, float duty) {                 // duty -1..1
  int d = (int)(fabsf(duty) * 255.0f + 0.5f);
  if (d > 255) d = 255;
  if (d == 0) { ledcWrite(in1, 0); ledcWrite(in2, 0); return; } // coast
  if (duty > 0) { ledcWrite(in1, 255); ledcWrite(in2, 255 - d); }  // forward: IN1 high, IN2 low for d/255 of the period
  else          { ledcWrite(in2, 255); ledcWrite(in1, 255 - d); }
}
float speedToDuty(float vside) {
  float a = fabsf(vside) / cfg.vmax;
  if (a < 0.02f) return 0;
  if (a > 1) a = 1;
  float d = cfg.deadband + (1.0f - cfg.deadband) * a;
  float comp = (vbat > 2.0f) ? VBAT_REFERENCE / vbat : 1.0f;     // keep speed roughly constant as the cells sag
  if (comp > 1.35f) comp = 1.35f;
  d *= comp;
  if (d > 1) d = 1;
  return vside < 0 ? -d : d;
}
void driveWheels(float v, float w) {
  float wy = w / (cfg.yaw > 0.05f ? cfg.yaw : 0.05f);            // skid/diff steering loses yaw: command more
  float l = (v - wy * cfg.track * 0.5f) * (1.0f - cfg.trim);
  float r = (v + wy * cfg.track * 0.5f) * (1.0f + cfg.trim);
  float dl = speedToDuty(l), dr = speedToDuty(r);
  if (LEFT_INVERT) dl = -dl;
  if (RIGHT_INVERT) dr = -dr;
  motorWrite(PIN_AIN1, PIN_AIN2, dl);
  motorWrite(PIN_BIN1, PIN_BIN2, dr);
}
void stopMotors() { curV = curW = tgtV = tgtW = 0; driveWheels(0, 0); }

// ---------------------------------------------------------------- battery
void readBattery() {
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(PIN_VBAT_ADC);
  float v = (mv / 16.0f) / 1000.0f * VBAT_DIVIDER * cfg.vscale;
  vbat = (vbat < 0.5f) ? v : 0.9f * vbat + 0.1f * v;
  // latch below the cut-off (debounced 1.5 s; NiMH voltage recovers when unloaded, so only a reset clears it)
  if (vbat > 1.0f && vbat < VBAT_LOW_CUTOFF) {
    if (!lowSinceMs) lowSinceMs = millis();
    if (millis() - lowSinceMs > 1500) latched = true;
  } else lowSinceMs = 0;
}

// ---------------------------------------------------------------- messages
void sendTelemetry() {
  if (peerPort == 0) return;
  char buf[420];
  int n = snprintf(buf, sizeof(buf),
    "{\"t\":\"tel\",\"vbat\":%.2f,\"fault\":%d,\"latched\":%d,\"rssi\":%d,\"up\":%.1f,\"v\":%.3f,\"w\":%.3f,"
    "\"cfg\":{\"track\":%.3f,\"vmax\":%.3f,\"yaw_gain\":%.3f,\"trim\":%.3f,\"vbat_scale\":%.3f,\"deadband\":%.2f},\"ts\":%.3f",
    vbat, fault ? 1 : 0, latched ? 1 : 0, WiFi.RSSI(), (millis() - bootMs) / 1000.0, curV, curW,
    cfg.track, cfg.vmax, cfg.yaw, cfg.trim, cfg.vscale, cfg.deadband, lastTs);
  if (USE_SENSOR_HUB && hubLine[0] == '{') n += snprintf(buf + n, sizeof(buf) - n, ",\"hub\":%s", hubLine);
  n += snprintf(buf + n, sizeof(buf) - n, "}");
  udp.beginPacket(peerIp, peerPort);
  udp.write((const uint8_t*)buf, n);
  udp.endPacket();
}

void handlePacket(char* s) {
  float x;
  if (hasKey(s, "\"cmd\"")) {
    float v = 0, w = 0;
    getNum(s, "\"v\"", v); getNum(s, "\"w\"", w);
    double ts = 0; float t = 0; if (getNum(s, "\"ts\"", t)) lastTs = t;
    if (v > cfg.vmax) v = cfg.vmax; if (v < -cfg.vmax) v = -cfg.vmax;
    tgtV = v; tgtW = w;
    lastCmdMs = millis();
  } else if (hasKey(s, "\"stop\"")) {
    stopMotors();
  } else if (hasKey(s, "\"reset\"")) {
    latched = false; lowSinceMs = 0;
  } else if (hasKey(s, "\"cfg\"")) {
    if (getNum(s, "\"track\"", x) && x > 0.02f && x < 1.5f) cfg.track = x;
    if (getNum(s, "\"vmax\"", x) && x > 0.02f && x < 3.0f) cfg.vmax = x;
    if (getNum(s, "\"yaw_gain\"", x) && x > 0.1f && x < 1.5f) cfg.yaw = x;
    if (getNum(s, "\"trim\"", x) && x > -0.4f && x < 0.4f) cfg.trim = x;
    if (getNum(s, "\"vbat_scale\"", x) && x > 0.5f && x < 2.0f) cfg.vscale = x;
    if (getNum(s, "\"deadband\"", x) && x >= 0.0f && x < 0.9f) cfg.deadband = x;
    saveCfg();
  }
}

// ---------------------------------------------------------------- setup / loop
void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    delay(150);
  }
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_FALLBACK_SSID, AP_FALLBACK_PASS);
    Serial.printf("Wi-Fi failed -> access point '%s' at %s\n", AP_FALLBACK_SSID, WiFi.softAPIP().toString().c_str());
  } else {
    Serial.printf("Wi-Fi ok: %s  (%s.local)\n", WiFi.localIP().toString().c_str(), HOSTNAME);
  }
  WiFi.setSleep(false);                    // lowest latency
  MDNS.begin(HOSTNAME);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_NSLEEP, OUTPUT);
  digitalWrite(PIN_NSLEEP, LOW);           // driver asleep until everything is configured
  pinMode(PIN_NFAULT, INPUT_PULLUP);
  analogSetPinAttenuation(PIN_VBAT_ADC, ADC_11db);
  int pins[4] = {PIN_AIN1, PIN_AIN2, PIN_BIN1, PIN_BIN2};
  for (int i = 0; i < 4; i++) { ledcAttach(pins[i], PWM_FREQ_HZ, 8); ledcWrite(pins[i], 0); }
  loadCfg();
  bootMs = millis();
  connectWifi();
  udp.begin(UDP_PORT);
  http.on("/", []() {
    char b[240];
    snprintf(b, sizeof(b), "{\"name\":\"sewerbot-rover\",\"vbat\":%.2f,\"latched\":%d,\"fault\":%d,\"ip\":\"%s\",\"udp\":%d}",
             vbat, latched, fault, WiFi.localIP().toString().c_str(), UDP_PORT);
    http.send(200, "application/json", b);
  });
  http.begin();
#if USE_SENSOR_HUB
  Serial2.begin(115200, SERIAL_8N1, HUB_RX_PIN, HUB_TX_PIN);
#endif
  readBattery();
  digitalWrite(PIN_NSLEEP, HIGH);
  lastLoopUs = micros();
}

void loop() {
  // --- network input
  int sz = udp.parsePacket();
  while (sz > 0) {
    char buf[300];
    int n = udp.read(buf, sizeof(buf) - 1);
    buf[n > 0 ? n : 0] = 0;
    peerIp = udp.remoteIP(); peerPort = udp.remotePort();
    handlePacket(buf);
    sz = udp.parsePacket();
  }
  http.handleClient();
#if USE_SENSOR_HUB
  static char line[128]; static int li = 0;
  while (Serial2.available()) {
    char c = Serial2.read();
    if (c == '\n') { line[li] = 0; if (li > 2 && line[0] == '{') strncpy(hubLine, line, sizeof(hubLine) - 1); li = 0; }
    else if (li < (int)sizeof(line) - 1 && c != '\r') line[li++] = c;
  }
#endif

  // --- safety
  fault = (digitalRead(PIN_NFAULT) == LOW);
  readBattery();
  bool timedOut = millis() - lastCmdMs > CMD_TIMEOUT_MS;
  if (timedOut || fault || latched) { tgtV = 0; tgtW = 0; }

  // --- speed ramp (same limits as the simulated rover)
  uint32_t nowUs = micros();
  float dt = (nowUs - lastLoopUs) * 1e-6f;
  lastLoopUs = nowUs;
  if (dt > 0.1f) dt = 0.1f;
  float dv = MAX_ACCEL_MPS2 * dt, dw = 6.0f * dt;
  curV += constrain(tgtV - curV, -dv, dv);
  curW += constrain(tgtW - curW, -dw, dw);
  driveWheels(curV, curW);

  // --- telemetry + status LED
  if (millis() - lastTelMs >= TELEMETRY_MS) { lastTelMs = millis(); sendTelemetry(); }
  uint32_t m = millis();
  bool led = latched ? (m / 120) % 2 : fault ? (m / 300) % 2 : (peerPort ? (m % 1500) < 80 : (m / 600) % 2);
  digitalWrite(PIN_LED, led);
  delay(2);
}
