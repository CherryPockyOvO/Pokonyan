#ifndef ULTRASONIC_H
#define ULTRASONIC_H

#include <Arduino.h>

// 超聲波腳位 (D22 / D23)
const uint8_t TRIG_PIN = 22;
const uint8_t ECHO_PIN = 23;

inline float distanceCm = -1.0f;

inline void setupUltrasonic() {
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
}

inline float getDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 12000UL); // 12ms 超時 (對應 2m 範圍)
  if (duration == 0) return -1.0f;
  return (duration * 0.0343f / 2.0f);
}

inline void readUltrasonic() {
  distanceCm = getDistance();
}

#endif
