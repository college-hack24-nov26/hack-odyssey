/*
  ESP32 + DRV8833 + 2WD rover  -  control over USB serial (115200 baud)

  Based on the rover_wasd sketch that is known to talk to wasd_control.py (kept as rover_wasd_original.ino.txt).
  Same keys, same pins, same replies. Added: telemetry, optional distance + IR sensors that stop forward motion,
  and a watchdog that is always armed while the rover moves.

  Keys (from the dashboard, wasd_control.py, or a serial monitor):
    w forward   s backward   a spin left   d spin right   space / x stop
    e / q speed up / down (steps of 25, 100..255)
    t self-test (forward, back, spin left, spin right; ~5 s, wheels OFF the ground)
    ? print one telemetry line now
    . heartbeat

  Safety:
    * While the rover is moving, it stops if no byte (a key or a '.') arrives for 1 second. The dashboard sends '.' every 0.2 s.
      Typing keys in a serial monitor therefore keeps it running only while you keep typing: that is intended.
    * ENABLE_ULTRASONIC / ENABLE_IR: forward motion is refused and stopped at once when the sensor sees an obstacle closer than
      STOP_CM, or when the ultrasonic sensor gives no valid reading.
    * Nothing is set at boot: both inputs of every motor start low (coast).

  Replies (one line each): forward · backward · spin left · spin right · stop · speed N · link lost - stopped ·
    obstacle - stopped · obstacle - refused · and every 0.5 s:  tel up=12 speed=180 dir=F dist=23.5 ir=0
    (dist and ir only when that sensor is enabled; dist=-1 means no valid reading; ir=1 means obstacle)

  Wiring, ESP32 DevKit (ESP32_CAM_PINS 0), as built:
      GPIO26 IN1, GPIO27 IN2 (left, OUT1/OUT2)     GPIO32 IN3, GPIO33 IN4 (right, OUT3/OUT4)
      EEP (sleep): left empty if the module's J1 jumper holds it high, otherwise wired to 3V3. ULT (fault) left empty.
      ESP32 GND shared with module GND.
  Optional sensors (DevKit only):
      HC-SR04  TRIG -> GPIO18, ECHO -> GPIO19 THROUGH A 10k/20k DIVIDER (the echo pin is 5 V, the ESP32 is 3.3 V only), VCC 5 V
      IR obstacle module  OUT -> GPIO21 (3.3 V module, or a divider if it runs on 5 V), most modules pull OUT low on detection
  Requires Arduino-ESP32 core 3.x.
*/

// 1 = AI-Thinker ESP32-CAM (only IO12/13/14/15 free; no room for the sensors), 0 = normal ESP32 DevKit
#ifndef ESP32_CAM_PINS
#define ESP32_CAM_PINS 0
#endif
#ifndef ENABLE_ULTRASONIC
#define ENABLE_ULTRASONIC 0
#endif
#ifndef ENABLE_IR
#define ENABLE_IR 0
#endif
#if ESP32_CAM_PINS && (ENABLE_ULTRASONIC || ENABLE_IR)
#error "The ESP32-CAM header has no free pins for the sensors"
#endif

#if ESP32_CAM_PINS
const int AIN1 = 13, AIN2 = 12;           // left motor  (module IN1, IN2)
const int BIN1 = 14, BIN2 = 15;           // right motor (module IN3, IN4)
#else
const int AIN1 = 26, AIN2 = 27;           // left motor
const int BIN1 = 32, BIN2 = 33;           // right motor
#endif
const int TRIG_PIN = 18, ECHO_PIN = 19;   // HC-SR04 (ECHO through a divider!)
const int IR_PIN = 21;
const bool IR_ACTIVE_LOW = true;

const int PWM_FREQ = 20000;
const int PWM_BITS = 8;                   // duty 0..255
const int MIN_DUTY = 90;                  // below this TT motors just hum
const int SPEED_STEP = 25;
const unsigned long WATCHDOG_MS = 1000;
const unsigned long TEL_MS = 500;
const unsigned long SENSE_MS = 100;
const float STOP_CM = 15.0;

int speedLevel = 180;                     // 100..255
int dirL = 0, dirR = 0;                   // -1, 0, +1
unsigned long lastByteMs = 0, lastTelMs = 0, lastSenseMs = 0;
float distCm = -1;                        // -1 = no valid reading
int irHit = 0;
int badReadings = 0;

// Forward = PWM on xIN1, xIN2 low. Reverse = the opposite. 0 = coast.
void setMotor(int pinA, int pinB, int speed) {         // speed: -255..255
  speed = constrain(speed, -255, 255);
  int duty = abs(speed);
  if (duty > 0 && duty < MIN_DUTY) duty = MIN_DUTY;
  ledcWrite(pinA, speed > 0 ? duty : 0);
  ledcWrite(pinB, speed < 0 ? duty : 0);
}

void drive(int left, int right) {
  setMotor(AIN1, AIN2, left);
  setMotor(BIN1, BIN2, right);
}

void apply() { drive(dirL * speedLevel, dirR * speedLevel); }

void go(int l, int r, const char *name) {
  dirL = l; dirR = r;
  apply();
  Serial.println(name);
}

bool moving() { return dirL != 0 || dirR != 0; }
bool movingForward() { return dirL > 0 && dirR > 0; }

char dirChar() {
  if (!moving()) return 'S';
  if (dirL > 0 && dirR > 0) return 'F';
  if (dirL < 0 && dirR < 0) return 'B';
  return dirL < dirR ? 'L' : 'R';
}

// true while something is in the way of forward motion
bool obstacleAhead() {
#if ENABLE_ULTRASONIC
  if (distCm < 0 || distCm < STOP_CM) return true;       // no valid reading counts as blocked
#endif
#if ENABLE_IR
  if (irHit) return true;
#endif
  return false;
}

void readSensors() {
#if ENABLE_ULTRASONIC
  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  // the HC-SR04 answers an empty path with a ~38 ms pulse, so a pulse of 0 means no sensor or a broken wire
  unsigned long us = pulseIn(ECHO_PIN, HIGH, 40000);
  if (us == 0) {
    if (++badReadings >= 3) distCm = -1;
  } else {
    badReadings = 0;
    distCm = min(400.0f, us / 58.0f);
  }
#endif
#if ENABLE_IR
  int v = digitalRead(IR_PIN);
  irHit = IR_ACTIVE_LOW ? (v == LOW) : (v == HIGH);
#endif
}

void printTelemetry() {
  Serial.printf("tel up=%lu speed=%d dir=%c", millis() / 1000, speedLevel, dirChar());
#if ENABLE_ULTRASONIC
  Serial.printf(" dist=%.1f", distCm);
#endif
#if ENABLE_IR
  Serial.printf(" ir=%d", irHit);
#endif
  Serial.println();
}

void selfTest() {
  Serial.println("Self-test: wheels should be off the ground");
  go(1, 1, "test: forward");     delay(1500);
  go(0, 0, "test: stop");        delay(400);
  go(-1, -1, "test: backward");  delay(1500);
  go(0, 0, "test: stop");        delay(400);
  go(-1, 1, "test: spin left");  delay(1000);
  go(0, 0, "test: stop");        delay(400);
  go(1, -1, "test: spin right"); delay(1000);
  go(0, 0, "test: done");
}

void handleKey(char c) {
  switch (tolower(c)) {
    case 'w':
      if (obstacleAhead()) { go(0, 0, "obstacle - refused"); break; }
      go(1, 1, "forward");     break;
    case 's': go(-1, -1, "backward");  break;
    case 'a': go(-1, 1, "spin left");  break;
    case 'd': go(1, -1, "spin right"); break;
    case ' ':
    case 'x': go(0, 0, "stop");        break;
    case 'e':
      speedLevel = min(255, speedLevel + SPEED_STEP);
      apply(); Serial.printf("speed %d\n", speedLevel); break;
    case 'q':
      speedLevel = max(100, speedLevel - SPEED_STEP);
      apply(); Serial.printf("speed %d\n", speedLevel); break;
    case 't': selfTest(); break;
    case '?': printTelemetry(); break;
    case '.': break;                       // heartbeat: its only job is to reset the watchdog
    default: break;                        // ignore CR/LF and unknown keys
  }
}

void setup() {
  Serial.begin(115200);
  for (int p : {AIN1, AIN2, BIN1, BIN2}) ledcAttach(p, PWM_FREQ, PWM_BITS);
  drive(0, 0);
#if ENABLE_ULTRASONIC
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
#endif
#if ENABLE_IR
  pinMode(IR_PIN, INPUT);
#endif
  lastByteMs = millis();
  Serial.println("Rover ready. w/a/s/d to drive, space to stop, t for self-test.");
}

void loop() {
  while (Serial.available()) {
    handleKey((char)Serial.read());
    lastByteMs = millis();
  }
  unsigned long now = millis();
  if (moving() && now - lastByteMs > WATCHDOG_MS) go(0, 0, "link lost - stopped");
  if (now - lastSenseMs >= SENSE_MS) {
    lastSenseMs = now;
    readSensors();
    if (movingForward() && obstacleAhead()) go(0, 0, "obstacle - stopped");
  }
  if (now - lastTelMs >= TEL_MS) {
    lastTelMs = now;
    printTelemetry();
  }
}
