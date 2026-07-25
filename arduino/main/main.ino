// Robot Car Main Entry Point (main.ino)
// Arduino Mega 2560 + Adafruit Motor Shield v1
//
// Modular Header Files:
//   - motor_control.h  : 馬達驅動與 control() 運動控制函數
//   - ultrasonic.h     : 超聲波 D22/D23 腳位與 getDistance() 測距函數
//   - serial_comm.h    : 樹莓派 Serial 串口通訊協定 (波特率 115200)

#include <Arduino.h>
#include "motor_control.h"
#include "ultrasonic.h"
#include "serial_comm.h"

// 實體化 AFMotor 馬達物件 (於全域中宣告一次)
AF_DCMotor rightFront(1); // M1 (右前)
AF_DCMotor rightRear(2);  // M2 (右後)
AF_DCMotor leftFront(3);  // M3 (左前)
AF_DCMotor leftRear(4);   // M4 (左後)

unsigned long lastRangeAt = 0;

void setup() {
  // 🎯 初始化各模組
  setupSerialComm(115200); // 串口通訊初始化 (波特率 115200)
  setupMotors();           // 馬達初始化
  setupUltrasonic();       // 超聲波 D22/D23 腳位初始化
}

void loop() {
  unsigned long now = millis();

  // 1. 處理樹莓派傳入的串口控制指令 (C pwml pwmr dirL dirR 或 WASD)
  handleSerialComm();

  // 2. 實時超聲波測距 (每 50ms 執行一次)
  if (now - lastRangeAt >= 50) {
    lastRangeAt = now;
    readUltrasonic();
  }

  // 3. 發送超聲波遙測數據給樹莓派 (D distanceCm)
  sendSerialTelemetry();
}
