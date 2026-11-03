// Arduino Uno/Nano 5 V sensor hub: gas sensors + ultrasonic ranger -> one JSON line per 100 ms on the serial port.
// Why a separate board: the ESP32's ADC2 cannot be used while Wi-Fi is on, MQ-type sensors want 5 V, and the Arduino
// is a cheap, robust 5 V I/O expander.  Wire: Arduino TX(D1) -> 10k/20k divider -> ESP32 GPIO16 (5 V must not reach it).
//
// Wiring:  MQ-4 (methane) AO -> A0     MQ-135 (air quality / H2S-ish) AO -> A1     (sensors heat for 24-48 h first!)
//          HC-SR04  TRIG -> D7  ECHO -> D8 (through a 1k/2k divider if the ESP32 ever reads it directly)
#include <Arduino.h>

const uint8_t PIN_TRIG = 7, PIN_ECHO = 8;
const uint8_t PIN_MQ1 = A0, PIN_MQ2 = A1;

float readRangeM() {
  digitalWrite(PIN_TRIG, LOW); delayMicroseconds(3);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long us = pulseIn(PIN_ECHO, HIGH, 25000UL);     // 25 ms = ~4.3 m
  if (us == 0) return -1.0f;
  return us * 0.000343f / 2.0f;
}

int readAvg(uint8_t pin) {
  long s = 0;
  for (int i = 0; i < 8; i++) s += analogRead(pin);
  return (int)(s / 8);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
}

void loop() {
  static unsigned long last = 0;
  if (millis() - last < 100) return;
  last = millis();
  float r = readRangeM();
  Serial.print("{\"gas1\":"); Serial.print(readAvg(PIN_MQ1));
  Serial.print(",\"gas2\":"); Serial.print(readAvg(PIN_MQ2));
  Serial.print(",\"range\":"); Serial.print(r, 3);
  Serial.println("}");
}
