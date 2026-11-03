/*
  ESP32 DevKit + DRV8833 + 2WD rover  -  firmware v2  (USB serial 115200, optionally the same protocol over Wi-Fi TCP)

  Keeps everything of esp32_rover_serial (keys, replies, the 1 s watchdog, nothing set at boot) and adds, behind a '!' prefix
  so a letter can never be mistaken for a key:

    !HELLO                 one line: firmware, reset reason (brownout!), sensors that answered, config, pins
    !I2CSCAN sda scl       scan an I2C pin pair (refused for motor pins and input-only pins)
    !GYRO                  gyro status: bias, noise, accelerometer (gravity) vector, yaw
    !CALGYRO               re-zero the gyro (only while stopped and still)
    !CFG k=v ...           change settings in RAM (trimL trimR invL invR minduty usguard kp kd holdmax gyrosign holddef); !CFG alone prints them
    !SAVE / !RESETCFG      store the settings in flash / go back to defaults
    !D l r ms              direct duties -255..255 for at most 3000 ms (arcs); the host repeats it to keep going
    !MOVE F|B|L|R ms [h]   a bounded move (<= 5000 ms) that streams "mv t= yaw= rate= corr=" lines; h = heading-hold on (F/B only)
    !STOP

  Safety (unchanged in spirit):
    * any moving state stops if no byte arrives for 1 s (the host sends '.' every 0.2 s); stops are always immediate
    * !D and !MOVE end by themselves; starts are ramped (~200 ms), the two motors start 50 ms apart, a reversal coasts 150 ms first
    * heading-hold is bounded (holdmax % of the duty) and switches itself off if the gyro goes quiet or reads nonsense
    * the HC-SR04 only stops the rover when usguard=1 (set after you confirmed the sensor works); a missing sensor never blocks driving

  Pins (defaults, override with -D at compile time or through the generated flags of tools/flash.py):
    left motor  GPIO26 / GPIO27      right motor GPIO32 / GPIO33      (DRV8833 IN1/IN2, IN3/IN4)
    MPU6050     SDA GPIO21, SCL GPIO22, 3V3 supply, AD0 to GND
    HC-SR04     TRIG GPIO18 (must be an OUTPUT pin), ECHO GPIO35 through a 10k/20k divider (input-only pin is fine), VCC 5 V
  Requires Arduino-ESP32 core 3.x.
*/
#include <Wire.h>
#include <Preferences.h>
#include <esp_system.h>
#include <stdarg.h>

#ifndef FW_VERSION
#define FW_VERSION "2.0.0"
#endif
#ifndef AIN1_PIN
#define AIN1_PIN 26
#define AIN2_PIN 27
#define BIN1_PIN 32
#define BIN2_PIN 33
#endif
#ifndef SDA_PIN
#define SDA_PIN 21
#define SCL_PIN 22
#endif
#ifndef TRIG_PIN
#define TRIG_PIN 18
#endif
#ifndef ECHO_PIN
#define ECHO_PIN 35
#endif
#ifndef ENABLE_ULTRASONIC
#define ENABLE_ULTRASONIC 1
#endif
#ifndef ENABLE_WIFI
#define ENABLE_WIFI 0
#endif
#ifndef WIFI_TCP_PORT
#define WIFI_TCP_PORT 8023
#endif

#if ENABLE_WIFI
#include <WiFi.h>
#include "wifi_secrets.h"          // defines WIFI_SSID and WIFI_PASS; written by tools/flash.py into a temporary copy, never committed
WiFiServer wifiServer(WIFI_TCP_PORT);
WiFiClient wifiClient;
#endif

const int AIN1 = AIN1_PIN, AIN2 = AIN2_PIN, BIN1 = BIN1_PIN, BIN2 = BIN2_PIN;
const int PWM_FREQ = 20000, PWM_BITS = 8;
const int SPEED_STEP = 25;
const unsigned long WATCHDOG_MS = 1000, TEL_MS = 500, RAMP_MS = 10, COAST_MS = 150, STAGGER_MS = 50;
const int RAMP_STEP = 13;                              // 255 duty in ~200 ms at one step per 10 ms
const float STOP_CM = 15.0;
const unsigned long GYRO_PERIOD_US = 10000;            // 100 Hz
const float GYRO_LSB_PER_DPS = 65.5f;                  // +-500 dps range

enum Mode { M_IDLE, M_KEY, M_DIRECT, M_MOVE };         // declared before any function: the Arduino build inserts prototypes above the first one

// ------------------------------------------------------------------ settings (flash-backed)
struct Cfg {
  float trimL = 1.0f, trimR = 1.0f;                    // duty multipliers 0.7..1.3 (the weak motor gets > 1)
  int invL = 1, invR = 1;                              // -1 reverses a wheel
  int minDuty = 90;                                    // below this TT motors just hum
  int usGuard = 0;                                     // 1 = the HC-SR04 may stop the rover (only after you confirmed it)
  float kp = 2.0f, kd = 0.3f;                          // heading-hold: duty per degree, duty per degree/second
  int holdMax = 15;                                    // max correction, % of the duty
  int gyroSign = 1;                                    // +1 or -1 so that a left (counter-clockwise) turn is positive
  int holdDef = 0;                                     // 1 = heading-hold also on the plain 'w' key
} cfg;
Preferences prefs;

void loadCfg() {
  prefs.begin("rover", true);
  cfg.trimL = prefs.getFloat("trimL", 1.0f);  cfg.trimR = prefs.getFloat("trimR", 1.0f);
  cfg.invL = prefs.getInt("invL", 1);         cfg.invR = prefs.getInt("invR", 1);
  cfg.minDuty = prefs.getInt("minduty", 90);  cfg.usGuard = prefs.getInt("usguard", 0);
  cfg.kp = prefs.getFloat("kp", 2.0f);        cfg.kd = prefs.getFloat("kd", 0.3f);
  cfg.holdMax = prefs.getInt("holdmax", 15);  cfg.gyroSign = prefs.getInt("gyrosign", 1);
  cfg.holdDef = prefs.getInt("holddef", 0);
  prefs.end();
}
void saveCfg() {
  prefs.begin("rover", false);
  prefs.putFloat("trimL", cfg.trimL);  prefs.putFloat("trimR", cfg.trimR);
  prefs.putInt("invL", cfg.invL);      prefs.putInt("invR", cfg.invR);
  prefs.putInt("minduty", cfg.minDuty); prefs.putInt("usguard", cfg.usGuard);
  prefs.putFloat("kp", cfg.kp);        prefs.putFloat("kd", cfg.kd);
  prefs.putInt("holdmax", cfg.holdMax); prefs.putInt("gyrosign", cfg.gyroSign);
  prefs.putInt("holddef", cfg.holdDef);
  prefs.end();
}

// ------------------------------------------------------------------ output (USB, and the Wi-Fi client when there is one)
void say(const char *fmt, ...) {
  char b[220];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  Serial.println(b);
#if ENABLE_WIFI
  if (wifiClient && wifiClient.connected()) wifiClient.println(b);
#endif
}

// ------------------------------------------------------------------ state
int speedLevel = 180;                                  // 100..255
Mode mode = M_IDLE;
int baseL = 0, baseR = 0;                              // requested signed duties before hold, trim and inversion
unsigned long modeUntil = 0, lastByteMs = 0, lastTelMs = 0;
char moveDir = 'F';
unsigned long moveStart = 0, moveMs = 0, lastMvMs = 0;
float moveYaw0 = 0, corrSum = 0; unsigned long corrN = 0;

int tgtL = 0, tgtR = 0, curL = 0, curR = 0;           // signed duties after hold/trim/inversion: wanted and applied
unsigned long coastL = 0, coastR = 0, startR = 0, lastRamp = 0;

// gyro
bool mpuOk = false; uint8_t mpuId = 0;
float gyroBias = 0, gyroNoise = 0, yawDeg = 0, rateDps = 0, axg = 0, ayg = 0, azg = 0;
unsigned long lastGyroUs = 0, lastGyroOkMs = 0; int gyroFails = 0, wildCount = 0;
bool holdOn = false; float holdTarget = 0, holdCorr = 0;

// ultrasonic
volatile unsigned long echoRise = 0, echoUs = 0; volatile bool echoNew = false;
float distCm = -1; bool usSeen = false; int badEcho = 0; unsigned long lastTrigMs = 0, trigSentMs = 0;

// boot facts
const char *resetName = "other"; int brownouts = 0;

// ------------------------------------------------------------------ motors
void setMotorRaw(int pinA, int pinB, int speed) {      // speed -255..255, forward = PWM on xIN1
  speed = constrain(speed, -255, 255);
  int duty = abs(speed);
  if (duty > 0 && duty < cfg.minDuty) duty = cfg.minDuty;
  ledcWrite(pinA, speed > 0 ? duty : 0);
  ledcWrite(pinB, speed < 0 ? duty : 0);
}
void writeMotors() { setMotorRaw(AIN1, AIN2, curL); setMotorRaw(BIN1, BIN2, curR); }

static int sgn(int v) { return v > 0 ? 1 : (v < 0 ? -1 : 0); }

void computeTargets() {
  float l = baseL, r = baseR;
  if (holdOn && baseL == baseR && baseL != 0) { l += holdCorr; r -= holdCorr; }
  l *= cfg.trimL * cfg.invL;  r *= cfg.trimR * cfg.invR;
  tgtL = constrain((int)lroundf(l), -255, 255);
  tgtR = constrain((int)lroundf(r), -255, 255);
}

void stopNow() {                                        // stops are immediate, only starts are ramped
  baseL = baseR = 0; tgtL = tgtR = curL = curR = 0;
  mode = M_IDLE; holdOn = false; holdCorr = 0;
  writeMotors();
}

void rampSide(int &cur, int tgt, unsigned long &coastUntil, unsigned long now, unsigned long notBefore) {
  if (tgt == 0) { cur = 0; return; }
  if (cur != 0 && sgn(cur) != sgn(tgt)) { cur = 0; coastUntil = now + COAST_MS; return; }       // a reversal passes through a coast
  if (now < coastUntil || now < notBefore) { cur = 0; return; }
  if (cur == 0) cur = sgn(tgt) * min(abs(tgt), cfg.minDuty);                                      // start at the hum threshold, not at 1
  if (abs(cur) < abs(tgt)) cur = sgn(tgt) * min(abs(tgt), abs(cur) + RAMP_STEP);
  else cur = tgt;                                                                                 // lowering the duty is immediate
}

void motorTick(unsigned long now) {
  if (now - lastRamp < RAMP_MS) return;
  lastRamp = now;
  bool fromRest = (curL == 0 && curR == 0);
  if (fromRest && (tgtL != 0 || tgtR != 0) && startR < now && now >= coastL && now >= coastR) startR = now + STAGGER_MS;
  rampSide(curL, tgtL, coastL, now, 0);
  rampSide(curR, tgtR, coastR, now, (tgtL != 0 && tgtR != 0) ? startR : 0);
  writeMotors();
}

void setBase(int l, int r, Mode m) {
  baseL = l; baseR = r; mode = m;
  computeTargets();
}

// ------------------------------------------------------------------ gyro
bool i2cWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(0x68); Wire.write(reg); Wire.write(val);
  return Wire.endTransmission() == 0;
}
bool mpuRead(int16_t *ax, int16_t *ay, int16_t *az, int16_t *gz) {
  Wire.beginTransmission(0x68); Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)0x68, (uint8_t)14) != 14) return false;
  *ax = (Wire.read() << 8) | Wire.read(); *ay = (Wire.read() << 8) | Wire.read(); *az = (Wire.read() << 8) | Wire.read();
  Wire.read(); Wire.read();                                                        // temperature
  Wire.read(); Wire.read(); Wire.read(); Wire.read();                              // gyro x, y
  *gz = (Wire.read() << 8) | Wire.read();
  return true;
}
bool mpuInit() {
  Wire.end();
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  Wire.setTimeOut(20);
  Wire.beginTransmission(0x68); Wire.write(0x75);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom((uint8_t)0x68, (uint8_t)1) != 1) return false;
  mpuId = Wire.read();
  if (!i2cWrite(0x6B, 0x01) || !i2cWrite(0x1B, 0x08) || !i2cWrite(0x1C, 0x00) || !i2cWrite(0x1A, 0x03)) return false;
  delay(50);
  return true;
}
// mean and spread of the gyro while the rover is still; false if it was not still
bool zeroGyro(int samples = 200) {
  if (!mpuOk) return false;
  double sum = 0, sum2 = 0; int n = 0;
  for (int i = 0; i < samples; i++) {
    int16_t ax, ay, az, gz;
    if (mpuRead(&ax, &ay, &az, &gz)) { double d = gz / GYRO_LSB_PER_DPS; sum += d; sum2 += d * d; n++; }
    delay(8);
  }
  if (n < samples / 2) return false;
  double mean = sum / n, var = sum2 / n - mean * mean;
  gyroNoise = sqrt(var > 0 ? var : 0);
  if (gyroNoise > 1.5) return false;                   // it was moving: keep the old bias
  gyroBias = mean; yawDeg = 0; holdTarget = 0;
  return true;
}
bool gyroFresh() { return mpuOk && millis() - lastGyroOkMs < 200; }

void gyroTick() {
  if (!mpuOk) return;
  unsigned long nowUs = micros();
  if (nowUs - lastGyroUs < GYRO_PERIOD_US) return;
  float dt = (nowUs - lastGyroUs) / 1e6f; if (lastGyroUs == 0 || dt > 0.1f) dt = 0.01f;
  lastGyroUs = nowUs;
  int16_t ax, ay, az, gz;
  if (!mpuRead(&ax, &ay, &az, &gz)) { gyroFails++; return; }
  gyroFails = 0; lastGyroOkMs = millis();
  axg = ax / 16384.0f; ayg = ay / 16384.0f; azg = az / 16384.0f;
  rateDps = cfg.gyroSign * (gz / GYRO_LSB_PER_DPS - gyroBias);
  yawDeg += rateDps * dt;
  wildCount = fabsf(rateDps) > 450.0f ? wildCount + 1 : 0;
}

void holdTick() {
  if (!holdOn) { holdCorr = 0; return; }
  if (!gyroFresh() || wildCount > 3) { holdOn = false; holdCorr = 0; say("warn hold_off %s", wildCount > 3 ? "gyro_implausible" : "gyro_lost"); computeTargets(); return; }
  float lim = fabsf((float)baseL) * cfg.holdMax / 100.0f;
  holdCorr = constrain(cfg.kp * (yawDeg - holdTarget) + cfg.kd * rateDps, -lim, lim);
  corrSum += holdCorr; corrN++;
  computeTargets();
}

// ------------------------------------------------------------------ ultrasonic (interrupt driven: never blocks the gyro loop)
void IRAM_ATTR echoIsr() {
  if (digitalRead(ECHO_PIN)) echoRise = micros();
  else { echoUs = micros() - echoRise; echoNew = true; }
}
void usTick(unsigned long now) {
#if ENABLE_ULTRASONIC
  if (echoNew) {
    noInterrupts(); unsigned long us = echoUs; echoNew = false; interrupts();
    if (us > 100 && us < 30000) { distCm = us / 58.0f; usSeen = true; badEcho = 0; }
  }
  if (now - lastTrigMs >= 60) {
    if (trigSentMs && now - trigSentMs > 55 && !echoNew && usSeen && ++badEcho >= 3) distCm = -1;      // seen before, now silent
    lastTrigMs = trigSentMs = now;
    digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10); digitalWrite(TRIG_PIN, LOW);
  }
#endif
}
bool obstacleAhead() {
#if ENABLE_ULTRASONIC
  if (cfg.usGuard && usSeen && (distCm < 0 || distCm < STOP_CM)) return true;
#endif
  return false;
}

// ------------------------------------------------------------------ reporting
char dirChar() {
  if (mode == M_IDLE) return 'S';
  if (baseL > 0 && baseR > 0) return baseL == baseR ? 'F' : 'A';
  if (baseL < 0 && baseR < 0) return 'B';
  return baseL < baseR ? 'L' : 'R';
}
void printTelemetry() {
  char b[200]; int n = snprintf(b, sizeof b, "tel up=%lu speed=%d dir=%c hold=%d", millis() / 1000, speedLevel, dirChar(), holdOn ? 1 : 0);
  if (mpuOk) n += snprintf(b + n, sizeof b - n, " yaw=%.1f rate=%.1f", yawDeg, rateDps);
  if (usSeen) n += snprintf(b + n, sizeof b - n, " dist=%.1f", distCm);
  say("%s", b);
}
void printCfg() {
  say("cfg trimL=%.3f trimR=%.3f invL=%d invR=%d minduty=%d usguard=%d kp=%.2f kd=%.2f holdmax=%d gyrosign=%d holddef=%d",
      cfg.trimL, cfg.trimR, cfg.invL, cfg.invR, cfg.minDuty, cfg.usGuard, cfg.kp, cfg.kd, cfg.holdMax, cfg.gyroSign, cfg.holdDef);
}
void printHello() {
  char ip[24] = "";
#if ENABLE_WIFI
  if (WiFi.status() == WL_CONNECTED) snprintf(ip, sizeof ip, "%s", WiFi.localIP().toString().c_str());
#endif
  say("hello fw=" FW_VERSION " board=esp32 reset=%s brownouts=%d mpu=%d mpu_id=0x%02X gyro_bias=%.2f gyro_noise=%.2f us=%d wifi=%d ip=%s port=%d pins=%d,%d,%d,%d,%d,%d,%d,%d",
      resetName, brownouts, mpuOk ? 1 : 0, mpuId, gyroBias, gyroNoise, usSeen ? 1 : 0, ENABLE_WIFI, ip[0] ? ip : "-", WIFI_TCP_PORT,
      AIN1, AIN2, BIN1, BIN2, SDA_PIN, SCL_PIN, TRIG_PIN, ECHO_PIN);
  printCfg();
}
void printGyro() {
  say("gyro ok=%d bias=%.2f noise=%.2f ax=%.2f ay=%.2f az=%.2f yaw=%.1f rate=%.1f sign=%d fails=%d",
      gyroFresh() ? 1 : 0, gyroBias, gyroNoise, axg, ayg, azg, yawDeg, rateDps, cfg.gyroSign, gyroFails);
}

// ------------------------------------------------------------------ commands
void go(int l, int r, const char *name) { setBase(l, r, (l || r) ? M_KEY : M_IDLE); if (!(l || r)) stopNow(); say("%s", name); }

void startHold(bool on) {
  holdOn = on && mpuOk && gyroFresh() && baseL == baseR && baseL != 0;
  holdTarget = yawDeg; holdCorr = 0;
}

void i2cScan(int sda, int scl) {
  auto bad = [](int p) { return p == AIN1 || p == AIN2 || p == BIN1 || p == BIN2 || p < 0 || p > 33 || p == 6 || p == 7 || p == 8 || p == 9 || p == 10 || p == 11; };
  if (mode != M_IDLE) { say("err i2cscan needs the rover stopped"); return; }
  if (bad(sda) || bad(scl)) { say("i2cscan refused sda=%d scl=%d (motor pin, input-only or flash pin)", sda, scl); return; }
  Wire.end(); Wire.begin(sda, scl); Wire.setTimeOut(20);
  char found[80] = ""; int n = 0;
  for (uint8_t a = 8; a < 120 && n < 8; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { n += snprintf(found + strlen(found), sizeof found - strlen(found), "%s0x%02X", n ? "," : "", a); }
  }
  say("i2cscan sda=%d scl=%d found=%s", sda, scl, n ? found : "-");
  Wire.end(); Wire.begin(SDA_PIN, SCL_PIN); Wire.setClock(400000); Wire.setTimeOut(20);                 // back to the real bus
}

void startMove(char d, unsigned long ms, bool hold) {
  ms = constrain(ms, 50UL, 5000UL);
  int v = speedLevel;
  switch (d) {
    case 'F': setBase(v, v, M_MOVE); break;
    case 'B': setBase(-v, -v, M_MOVE); break;
    case 'L': setBase(-v, v, M_MOVE); break;
    case 'R': setBase(v, -v, M_MOVE); break;
    default: say("err move dir"); return;
  }
  moveDir = d; moveStart = millis(); moveMs = ms; modeUntil = moveStart + ms; lastMvMs = moveStart;
  moveYaw0 = yawDeg; corrSum = 0; corrN = 0;
  startHold(hold && (d == 'F' || d == 'B'));
  say("mv start dir=%c ms=%lu hold=%d yaw0=%.2f", d, ms, holdOn ? 1 : 0, yawDeg);
}

void moveTick(unsigned long now) {
  if (mode != M_MOVE) return;
  if (now - lastMvMs >= 100) { lastMvMs = now; say("mv t=%lu yaw=%.2f rate=%.2f corr=%.1f", now - moveStart, yawDeg - moveYaw0, rateDps, holdCorr); }
}

void endMove(const char *why) {
  say("mv done why=%s ms=%lu dir=%c yaw=%.2f corr_avg=%.2f", why, millis() - moveStart, moveDir, yawDeg - moveYaw0, corrN ? corrSum / corrN : 0.0f);
  stopNow();
}

void setCfgKV(const char *k, const char *v) {
  float f = atof(v); int i = atoi(v);
  if (!strcmp(k, "trimL")) cfg.trimL = constrain(f, 0.7f, 1.3f);
  else if (!strcmp(k, "trimR")) cfg.trimR = constrain(f, 0.7f, 1.3f);
  else if (!strcmp(k, "invL")) cfg.invL = i < 0 ? -1 : 1;
  else if (!strcmp(k, "invR")) cfg.invR = i < 0 ? -1 : 1;
  else if (!strcmp(k, "minduty")) cfg.minDuty = constrain(i, 40, 160);
  else if (!strcmp(k, "usguard")) cfg.usGuard = i ? 1 : 0;
  else if (!strcmp(k, "kp")) cfg.kp = constrain(f, 0.0f, 10.0f);
  else if (!strcmp(k, "kd")) cfg.kd = constrain(f, 0.0f, 3.0f);
  else if (!strcmp(k, "holdmax")) cfg.holdMax = constrain(i, 0, 25);
  else if (!strcmp(k, "gyrosign")) cfg.gyroSign = i < 0 ? -1 : 1;
  else if (!strcmp(k, "holddef")) cfg.holdDef = i ? 1 : 0;
  else say("err unknown setting %s", k);
}

void handleLine(char *line) {
  char *save = nullptr;
  char *cmd = strtok_r(line, " ", &save);
  if (!cmd) return;
  for (char *p = cmd; *p; p++) *p = toupper(*p);
  if (!strcmp(cmd, "HELLO")) printHello();
  else if (!strcmp(cmd, "GYRO")) printGyro();
  else if (!strcmp(cmd, "CALGYRO")) {
    if (mode != M_IDLE) say("err calgyro needs the rover stopped");
    else if (zeroGyro()) say("gyro zeroed bias=%.2f noise=%.2f ok=1", gyroBias, gyroNoise);
    else say("gyro zero failed noise=%.2f (the rover must be still, and the gyro wired)", gyroNoise);
  }
  else if (!strcmp(cmd, "I2CSCAN")) { char *a = strtok_r(nullptr, " ", &save); char *b = strtok_r(nullptr, " ", &save); if (a && b) i2cScan(atoi(a), atoi(b)); else say("err i2cscan sda scl"); }
  else if (!strcmp(cmd, "CFG")) {
    for (char *t = strtok_r(nullptr, " ", &save); t; t = strtok_r(nullptr, " ", &save)) { char *eq = strchr(t, '='); if (eq) { *eq = 0; setCfgKV(t, eq + 1); } }
    printCfg();
  }
  else if (!strcmp(cmd, "SAVE")) { saveCfg(); say("saved"); }
  else if (!strcmp(cmd, "RESETCFG")) { cfg = Cfg(); printCfg(); say("defaults in RAM; !SAVE to keep them"); }
  else if (!strcmp(cmd, "STOP")) go(0, 0, "stop");
  else if (!strcmp(cmd, "D")) {
    int l = atoi(strtok_r(nullptr, " ", &save) ?: "0"), r = atoi(strtok_r(nullptr, " ", &save) ?: "0");
    long ms = atol(strtok_r(nullptr, " ", &save) ?: "0");
    if (ms <= 0) { go(0, 0, "stop"); return; }
    if ((l > 0 && r > 0 && obstacleAhead())) { say("obstacle - refused"); return; }
    setBase(constrain(l, -255, 255), constrain(r, -255, 255), M_DIRECT);
    modeUntil = millis() + constrain(ms, 20L, 3000L);
    holdOn = false;
    say("d l=%d r=%d ms=%ld", l, r, ms);
  }
  else if (!strcmp(cmd, "MOVE")) {
    char *d = strtok_r(nullptr, " ", &save); char *ms = strtok_r(nullptr, " ", &save); char *h = strtok_r(nullptr, " ", &save);
    if (!d || !ms) { say("err move dir ms [h]"); return; }
    if (d[0] == 'F' && obstacleAhead()) { say("obstacle - refused"); return; }
    startMove(toupper(d[0]), atol(ms), h && (h[0] == 'h' || h[0] == '1'));
  }
  else say("err unknown command %s", cmd);
}

char lineBuf[96]; int lineLen = 0; bool inLine = false;

void selfTest() {
  say("Self-test: wheels should be off the ground");
  go(1 * speedLevel, 1 * speedLevel, "test: forward");     for (unsigned long t = millis(); millis() - t < 1500;) { motorTick(millis()); gyroTick(); delay(2); }
  stopNow(); delay(400);
  go(-speedLevel, -speedLevel, "test: backward");          for (unsigned long t = millis(); millis() - t < 1500;) { motorTick(millis()); gyroTick(); delay(2); }
  stopNow(); delay(400);
  go(-speedLevel, speedLevel, "test: spin left");          for (unsigned long t = millis(); millis() - t < 1000;) { motorTick(millis()); gyroTick(); delay(2); }
  stopNow(); delay(400);
  go(speedLevel, -speedLevel, "test: spin right");         for (unsigned long t = millis(); millis() - t < 1000;) { motorTick(millis()); gyroTick(); delay(2); }
  stopNow(); say("test: done");
}

void handleKey(char c) {
  switch (tolower(c)) {
    case 'w':
      if (obstacleAhead()) { stopNow(); say("obstacle - refused"); break; }
      go(speedLevel, speedLevel, "forward"); startHold(cfg.holdDef); break;
    case 's': go(-speedLevel, -speedLevel, "backward");  break;
    case 'a': go(-speedLevel, speedLevel, "spin left");  break;
    case 'd': go(speedLevel, -speedLevel, "spin right"); break;
    case ' ':
    case 'x': go(0, 0, "stop");                           break;
    case 'e': speedLevel = min(255, speedLevel + SPEED_STEP); if (mode == M_KEY) { baseL = sgn(baseL) * speedLevel; baseR = sgn(baseR) * speedLevel; computeTargets(); } say("speed %d", speedLevel); break;
    case 'q': speedLevel = max(100, speedLevel - SPEED_STEP); if (mode == M_KEY) { baseL = sgn(baseL) * speedLevel; baseR = sgn(baseR) * speedLevel; computeTargets(); } say("speed %d", speedLevel); break;
    case 't': selfTest(); break;
    case '?': printTelemetry(); break;
    default: break;                                        // '.' (heartbeat) and CR/LF and unknown keys
  }
}

void feed(char c) {
  lastByteMs = millis();
  if (inLine) {
    if (c == '\n' || c == '\r') { inLine = false; lineBuf[lineLen] = 0; lineLen = 0; handleLine(lineBuf); }
    else if (lineLen < (int)sizeof(lineBuf) - 1) lineBuf[lineLen++] = c;
    else { inLine = false; lineLen = 0; say("err line too long"); }
    return;
  }
  if (c == '!') { inLine = true; lineLen = 0; return; }
  handleKey(c);
}

void setup() {
  Serial.begin(115200);
  for (int p : {AIN1, AIN2, BIN1, BIN2}) ledcAttach(p, PWM_FREQ, PWM_BITS);
  stopNow();
  loadCfg();
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: resetName = "poweron"; break;
    case ESP_RST_BROWNOUT: resetName = "brownout"; break;
    case ESP_RST_SW: resetName = "sw"; break;
    case ESP_RST_PANIC: resetName = "panic"; break;
    case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: case ESP_RST_WDT: resetName = "wdt"; break;
    case ESP_RST_EXT: resetName = "ext"; break;
    default: resetName = "other";
  }
  prefs.begin("rover", false);
  brownouts = prefs.getInt("brownouts", 0);
  if (esp_reset_reason() == ESP_RST_BROWNOUT) { brownouts++; prefs.putInt("brownouts", brownouts); }
  prefs.end();
#if ENABLE_ULTRASONIC
  pinMode(TRIG_PIN, OUTPUT); digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(ECHO_PIN), echoIsr, CHANGE);
#endif
  mpuOk = mpuInit();
  if (mpuOk) { zeroGyro(); lastGyroOkMs = millis(); }
#if ENABLE_WIFI
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASS); wifiServer.begin();
#endif
  lastByteMs = millis();
  Serial.println("Rover ready. w/a/s/d to drive, space to stop, t for self-test, !HELLO for details.");
  delay(300);                                              // let the echo ISR see a first reading before the hello
  usTick(millis()); delay(70); usTick(millis());
  printHello();
}

void loop() {
  while (Serial.available()) feed((char)Serial.read());
#if ENABLE_WIFI
  if (!(wifiClient && wifiClient.connected())) {
    WiFiClient c = wifiServer.accept();
    if (c) { wifiClient = c; wifiClient.setNoDelay(true); lastByteMs = millis(); printHello(); }
  } else {
    while (wifiClient.available()) feed((char)wifiClient.read());
  }
#endif
  unsigned long now = millis();
  gyroTick();
  holdTick();
  motorTick(now);
  if (mode != M_IDLE && now - lastByteMs > WATCHDOG_MS) { if (mode == M_MOVE) endMove("link_lost"); else go(0, 0, "link lost - stopped"); }
  if ((mode == M_DIRECT || mode == M_MOVE) && (long)(now - modeUntil) >= 0) { if (mode == M_MOVE) endMove("time"); else go(0, 0, "stop"); }
  moveTick(now);
  usTick(now);
  if ((mode == M_KEY || mode == M_DIRECT || mode == M_MOVE) && baseL > 0 && baseR > 0 && obstacleAhead()) { stopNow(); say("obstacle - stopped"); }
  if (now - lastTelMs >= TEL_MS) { lastTelMs = now; printTelemetry(); }
}
