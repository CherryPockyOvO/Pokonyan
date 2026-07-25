#ifndef I2C_COMM_H
#define I2C_COMM_H

#include <Arduino.h>
#include <Wire.h>
#include "motor_control.h"
#include "ultrasonic.h"

// I2C Slave 從機預設位址 (可根據需要修改，如 0x08)
#ifndef I2C_SLAVE_ADDRESS
#define I2C_SLAVE_ADDRESS 0x08
#endif

// 🎯 1. 接收樹莓派 (Master) 發送過來的 I2C 數據
inline void onI2CReceive(int howMany) {
  if (Wire.available() <= 0) return;

  // 讀取第一個字元作為指令
  char cmd = (char)Wire.read();

  switch (cmd) {

    // 自訂高階協議 0x01: 直接傳送 4 個位元組 (pwmL, pwmR, dirL, dirR)
    case 0x01:
      if (Wire.available() >= 4) {
        uint8_t pwml = Wire.read();
        uint8_t pwmr = Wire.read();
        uint8_t dirL = Wire.read();
        uint8_t dirR = Wire.read();
        control(pwml, pwmr, dirL, dirR);
      }
      break;

    default: break;
  }
}

// 🎯 2. 當樹莓派索取數據時，回傳即時超聲波距離 (4-byte float)
inline void onI2CRequest() {
  uint8_t *dataPtr = (uint8_t *)&distanceCm;
  Wire.write(dataPtr, sizeof(distanceCm));
}

// 🎯 3. I2C 從機初始化函數
inline void setupI2C(uint8_t address = I2C_SLAVE_ADDRESS) {
  Wire.begin(address);         // 以從機身份加入 I2C 總線
  Wire.onReceive(onI2CReceive); // 註冊接收指令事件
  Wire.onRequest(onI2CRequest); // 註冊請求數據事件
}

#endif
