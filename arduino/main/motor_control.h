#ifndef MOTOR_CONTROL_H
#define MOTOR_CONTROL_H

#include <Arduino.h>
#include <AFMotor.h>

// 宣告外部 AFMotor 物件 (實體化於 main.ino)
extern AF_DCMotor rightFront;
extern AF_DCMotor rightRear;
extern AF_DCMotor leftFront;
extern AF_DCMotor leftRear;

const uint8_t defaultPWM = 120;

inline void control(uint8_t pwml, uint8_t pwmr, uint8_t leftDir, uint8_t rightDir) {
  leftFront.setSpeed(pwml);
  leftRear.setSpeed(pwml);
  rightFront.setSpeed(pwmr);
  rightRear.setSpeed(pwmr);

  leftFront.run(leftDir);
  leftRear.run(leftDir);
  rightFront.run(rightDir);
  rightRear.run(rightDir);
}

inline void setupMotors() {
  control(0, 0, RELEASE, RELEASE);
}

#endif
