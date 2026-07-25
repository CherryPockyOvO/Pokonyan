// Robot Car Main Entry Point (main.ino)
// Arduino Mega 2560 + Adafruit Motor Shield v1
//
// Modular Header Files:
//   - motor_control.h  : 馬達驅動與 control() 運動控制函數
//   - ultrasonic.h     : 超聲波 D22/D23 腳位與 getDistance() 測距函數
//   - i2c_comm.h       : 樹莓派 I2C 通訊協議 (位址 0x08)

#include <Arduino.h>
#include "motor_control.h"
#include "ultrasonic.h"
#include "i2c_comm.h"

// 實體化 AFMotor 馬達物件 (於全域中宣告一次)
AF_DCMotor rightFront(1); // M1 (右前)
AF_DCMotor rightRear(2);  // M2 (右後)
AF_DCMotor leftFront(3);  // M3 (左前)
AF_DCMotor leftRear(4);   // M4 (左後)

unsigned long lastRangeAt = 0;
unsigned long lastTelemetryAt = 0;

void setup() {
  Serial.begin(115200);

  // 🎯 初始化各模組
  setupMotors();      // 馬達初始化
  setupUltrasonic();  // 超聲波 D22/D23 腳位初始化
  setupI2C(0x08);     // I2C 0x08 從機初始化
}

void loop() {
  unsigned long now = millis();

  // 2. 實時超聲波測距 (每 50ms 執行一次)
  if (now - lastRangeAt >= 50) {
    lastRangeAt = now;
    readUltrasonic();
  }
  /*
  if (now - lastTelemetryAt >= 0) {
    lastTelemetryAt = now;
    Serial.print(F("Dist: "));
    if (distanceCm < 0) {
      Serial.println(F(">200 cm"));
    } else {
      Serial.print(distanceCm, 1);
      Serial.println(F(" cm"));
    }
  }
  */
}
