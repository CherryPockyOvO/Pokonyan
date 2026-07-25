#ifndef SERIAL_COMM_H
#define SERIAL_COMM_H

#include <Arduino.h>
#include "motor_control.h"
#include "ultrasonic.h"

// 🎯 1. 串口初始化 (預設波特率 115200)
inline void setupSerialComm(unsigned long baudrate = 115200) {
  Serial.begin(baudrate);
}

// 🎯 2. 解析樹莓派傳入的串口指令 (支援 4 參數指令: 'C <pwml> <pwmr> <dirL> <dirR>' 與 WASD 指令)
inline void handleSerialComm() {
  while (Serial.available() > 0) {
    char cmd = Serial.peek();

    // 4 參數精確控制指令: 'C pwml pwmr dirL dirR'
    if (cmd == 'C' || cmd == 'c') {
      Serial.read(); // 吃掉 'C' 字元
      int pwml = Serial.parseInt();
      int pwmr = Serial.parseInt();
      int dirL = Serial.parseInt();
      int dirR = Serial.parseInt();

      // 清理尾隨的換行與空白字元
      while (Serial.available() > 0 && (Serial.peek() == '\r' || Serial.peek() == '\n' || Serial.peek() == ' ')) {
        Serial.read();
      }

      control((uint8_t)pwml, (uint8_t)pwmr, (uint8_t)dirL, (uint8_t)dirR);
    }
    // WASD 單字元相容指令
    else {
      cmd = (char)Serial.read();
      switch (cmd) {
        case 'W': case 'w': control(defaultPWM, defaultPWM, FORWARD, FORWARD); break;
        case 'S': case 's': control(defaultPWM, defaultPWM, BACKWARD, BACKWARD); break;
        case 'A': case 'a': control(defaultPWM, defaultPWM, BACKWARD, FORWARD); break;
        case 'D': case 'd': control(defaultPWM, defaultPWM, FORWARD, BACKWARD); break;
        case 'E': case 'e': control(200, 120, FORWARD, FORWARD); break;
        case 'B': case 'b': control(0, 0, RELEASE, RELEASE); break;
        default: break;
      }
    }
  }
}

// 🎯 3. 每隔 100ms 經由串口發送超聲波距離給樹莓派 ('D <distance_cm>')
inline void sendSerialTelemetry() {
  static unsigned long lastTelemetryAt = 0;
  unsigned long now = millis();

  if (now - lastTelemetryAt >= 100) {
    lastTelemetryAt = now;
    Serial.print(F("D "));
    if (distanceCm < 0) {
      Serial.println(F("-1.0"));
    } else {
      Serial.println(distanceCm, 1);
    }
  }
}

#endif
