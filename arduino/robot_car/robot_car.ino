// Simple four-wheel PI controller for Arduino Mega + Motor Shield v1.
//
// Wiring:
//   left encoder D18, right encoder D19
//   HC-SR04 TRIG D24, ECHO D25
//   left motors M3/M4, right motors M1/M2
//
// Serial at 115200 baud:
//   V <left_rpm> <right_rpm>
//   S
//
// Telemetry:
//   TEL ms targetL rpmL pwmL targetR rpmR pwmR distanceCm watchdog

#include <Arduino.h>
#include <AFMotor.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

AF_DCMotor rightFront(1);
AF_DCMotor rightRear(2);
AF_DCMotor leftFront(3);
AF_DCMotor leftRear(4);

const uint8_t LEFT_ENCODER_PIN = 18;
const uint8_t RIGHT_ENCODER_PIN = 19;
const uint8_t TRIG_PIN = 24;
const uint8_t ECHO_PIN = 25;

const float LEFT_COUNTS_PER_REV = 8.2f;
const float RIGHT_COUNTS_PER_REV = 8.2f;
const float KP = 0.55f;
const float KI = 0.20f;
const uint8_t STRAIGHT_HOLD_PWM = 80;
const uint8_t PIVOT_HOLD_PWM = 150;
const uint8_t PIVOT_MAX_PWM = 205;
const uint8_t PWM_STEP = 18;
const float MAX_RPM = 300.0f;

const unsigned long COMMAND_TIMEOUT_MS = 900;
const unsigned long CONTROL_PERIOD_MS = 100;
const unsigned long SPEED_PERIOD_MS = 500;
const unsigned long RANGE_PERIOD_MS = 60;
const unsigned long TELEMETRY_PERIOD_MS = 250;
const float SPEED_FILTER_ALPHA = 0.35f;

volatile unsigned long leftCount = 0;
volatile unsigned long rightCount = 0;

struct Wheel {
  float target;
  float measured;
  float integral;
  uint8_t pwm;
};

Wheel left = {0, 0, 0, 0};
Wheel right = {0, 0, 0, 0};

unsigned long previousLeftCount = 0;
unsigned long previousRightCount = 0;
unsigned long lastCommandAt = 0;
unsigned long lastControlAt = 0;
unsigned long lastSpeedAt = 0;
unsigned long lastRangeAt = 0;
unsigned long lastTelemetryAt = 0;
float distanceCm = -1.0f;
bool watchdogStopped = true;

char commandBuffer[64];
uint8_t commandLength = 0;
bool discardingCommand = false;

void leftPulse() { leftCount++; }
void rightPulse() { rightCount++; }

int signOf(float value) {
  return (value > 0.0f) - (value < 0.0f);
}

void drivePair(AF_DCMotor &front, AF_DCMotor &rear, float target, uint8_t pwm) {
  uint8_t direction = RELEASE;
  if (target > 0.0f) direction = FORWARD;
  if (target < 0.0f) direction = BACKWARD;
  front.setSpeed(pwm);
  rear.setSpeed(pwm);
  front.run(direction);
  rear.run(direction);
}

void applyOutputs() {
  drivePair(leftFront, leftRear, left.target, left.pwm);
  drivePair(rightFront, rightRear, right.target, right.pwm);
}

void stopTargets() {
  left.target = 0.0f;
  right.target = 0.0f;
  left.integral = 0.0f;
  right.integral = 0.0f;
  left.pwm = 0;
  right.pwm = 0;
  applyOutputs();
}

void setTargets(float leftRpm, float rightRpm) {
  float nextLeft = constrain(leftRpm, -MAX_RPM, MAX_RPM);
  float nextRight = constrain(rightRpm, -MAX_RPM, MAX_RPM);
  if (signOf(nextLeft) != signOf(left.target)) left.integral = 0.0f;
  if (signOf(nextRight) != signOf(right.target)) right.integral = 0.0f;
  left.target = nextLeft;
  right.target = nextRight;
}

uint8_t moveToward(uint8_t current, uint8_t desired) {
  if (desired > current + PWM_STEP) return current + PWM_STEP;
  if (current > desired + PWM_STEP) return current - PWM_STEP;
  return desired;
}

void updateWheel(Wheel &wheel,
                 float elapsedSeconds,
                 uint8_t holdPwm,
                 uint8_t maximumPwm) {
  if (wheel.target == 0.0f) {
    wheel.integral = 0.0f;
    wheel.pwm = 0;
    return;
  }

  float error = fabs(wheel.target) - wheel.measured;
  float proposedIntegral = constrain(
      wheel.integral + KI * error * elapsedSeconds,
      -(float)holdPwm,
      (float)maximumPwm - holdPwm);
  float proposedOutput = holdPwm + KP * error + proposedIntegral;
  bool saturatingHigh = proposedOutput > maximumPwm && error > 0.0f;
  bool saturatingLow = proposedOutput < 0.0f && error < 0.0f;
  if (!saturatingHigh && !saturatingLow) {
    wheel.integral = proposedIntegral;
  }

  uint8_t desired = (uint8_t)(
      constrain(holdPwm + KP * error + wheel.integral,
                0.0f,
                (float)maximumPwm) +
      0.5f);
  wheel.pwm = moveToward(wheel.pwm, desired);
}

void updateControl(unsigned long now) {
  if (now - lastSpeedAt >= SPEED_PERIOD_MS) {
    noInterrupts();
    unsigned long currentLeft = leftCount;
    unsigned long currentRight = rightCount;
    interrupts();

    unsigned long elapsed = now - lastSpeedAt;
    float leftRaw = (currentLeft - previousLeftCount) * 60000.0f /
                    (LEFT_COUNTS_PER_REV * elapsed);
    float rightRaw = (currentRight - previousRightCount) * 60000.0f /
                     (RIGHT_COUNTS_PER_REV * elapsed);
    left.measured += SPEED_FILTER_ALPHA * (leftRaw - left.measured);
    right.measured += SPEED_FILTER_ALPHA * (rightRaw - right.measured);
    previousLeftCount = currentLeft;
    previousRightCount = currentRight;
    lastSpeedAt = now;
  }

  bool pivot = signOf(left.target) != 0 &&
               signOf(right.target) != 0 &&
               signOf(left.target) != signOf(right.target);
  uint8_t hold = pivot ? PIVOT_HOLD_PWM : STRAIGHT_HOLD_PWM;
  uint8_t maximum = pivot ? PIVOT_MAX_PWM : 255;
  float elapsedSeconds = (now - lastControlAt) / 1000.0f;
  lastControlAt = now;
  updateWheel(left, elapsedSeconds, hold, maximum);
  updateWheel(right, elapsedSeconds, hold, maximum);
  applyOutputs();
}

void readRange() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 12000UL);
  distanceCm = duration == 0 ? -1.0f : duration * 0.0343f / 2.0f;
}

bool parseNumber(char *text, float &value) {
  if (text == NULL || *text == '\0') return false;
  char *end = NULL;
  double parsed = strtod(text, &end);
  if (end == text || *end != '\0' || isnan(parsed) || isinf(parsed)) return false;
  value = (float)parsed;
  return true;
}

void processCommand() {
  char *operation = strtok(commandBuffer, " \t");
  if (operation == NULL) return;

  if ((operation[0] == 'S' || operation[0] == 's') && operation[1] == '\0') {
    stopTargets();
    watchdogStopped = true;
    lastCommandAt = millis();
    return;
  }

  if ((operation[0] == 'V' || operation[0] == 'v') && operation[1] == '\0') {
    float leftRpm;
    float rightRpm;
    char *leftText = strtok(NULL, " \t");
    char *rightText = strtok(NULL, " \t");
    char *extra = strtok(NULL, " \t");
    if (extra == NULL &&
        parseNumber(leftText, leftRpm) &&
        parseNumber(rightText, rightRpm)) {
      setTargets(leftRpm, rightRpm);
      watchdogStopped = false;
      lastCommandAt = millis();
      return;
    }
  }
  Serial.println(F("ERR use V <left_rpm> <right_rpm> or S"));
}

void readCommands() {
  while (Serial.available() > 0) {
    char value = (char)Serial.read();
    if (value == '\n' || value == '\r') {
      if (!discardingCommand && commandLength > 0) {
        commandBuffer[commandLength] = '\0';
        processCommand();
      }
      commandLength = 0;
      discardingCommand = false;
    } else if (discardingCommand) {
      continue;
    } else if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = value;
    } else {
      commandLength = 0;
      discardingCommand = true;
    }
  }
}

void printTelemetry() {
  Serial.print(F("TEL "));
  Serial.print(millis());
  Serial.print(' '); Serial.print(left.target, 1);
  Serial.print(' '); Serial.print(signOf(left.target) * left.measured, 1);
  Serial.print(' '); Serial.print(left.pwm);
  Serial.print(' '); Serial.print(right.target, 1);
  Serial.print(' '); Serial.print(signOf(right.target) * right.measured, 1);
  Serial.print(' '); Serial.print(right.pwm);
  Serial.print(' '); Serial.print(distanceCm, 1);
  Serial.print(' '); Serial.println(watchdogStopped ? 1 : 0);
}

void setup() {
  Serial.begin(115200);
  pinMode(LEFT_ENCODER_PIN, INPUT_PULLUP);
  pinMode(RIGHT_ENCODER_PIN, INPUT_PULLUP);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(LEFT_ENCODER_PIN), leftPulse, CHANGE);
  attachInterrupt(digitalPinToInterrupt(RIGHT_ENCODER_PIN), rightPulse, CHANGE);
  stopTargets();
  unsigned long now = millis();
  lastCommandAt = now;
  lastControlAt = now;
  lastSpeedAt = now;
  lastRangeAt = now;
  lastTelemetryAt = now;
  Serial.println(F("Shoe robot motor controller ready"));
}

void loop() {
  readCommands();
  unsigned long now = millis();
  if (!watchdogStopped && now - lastCommandAt > COMMAND_TIMEOUT_MS) {
    stopTargets();
    watchdogStopped = true;
  }
  if (now - lastRangeAt >= RANGE_PERIOD_MS) {
    lastRangeAt = now;
    readRange();
  }
  if (now - lastControlAt >= CONTROL_PERIOD_MS) {
    updateControl(now);
  }
  if (now - lastTelemetryAt >= TELEMETRY_PERIOD_MS) {
    lastTelemetryAt = now;
    printTelemetry();
  }
}
