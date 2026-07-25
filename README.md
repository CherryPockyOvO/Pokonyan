# 🤖 Raspberry Pi 5 Shoe-Seeking Robot (Pokonyan)

基於樹莓派 5 (Raspberry Pi 5) 與 Arduino Mega 2560 的全功能智慧尋物機器人專案。

本專案整合了 **AI 聲音辨識 (YAMNet + Whisper)**、**YOLO 視覺檢測**、**Arduino I2C 底盤控制 (0x08)** 及 **Web 雙模式 (AUTO / MANUAL) 遙控介面**。

---

## 📂 模組化專案目錄結構

```text
raspi/Pokonyan/
├── top.py                     <-- 🎯 主程式入口 (調度與模式切換)
├── README.md                  <-- 本說明文件
├── monitor.py                 <-- 系統硬體狀態監測腳本
│
├── control/                   <-- 🕹️ 控制模組
│   ├── __init__.py
│   ├── auto_controller.py     <-- 自動模式狀態機 (AutoController)
│   ├── manual_controller.py   <-- 手動模式 WASD 遙控 (ManualController)
│   └── motor.py               <-- Arduino Mega I2C 底盤通訊門戶 (MotorGateway)
│
├── perception/                <-- 👁️ 聲學與視覺感知模組
│   ├── __init__.py
│   ├── detector.py            <-- YOLO NCNN 拖鞋目標檢測 (YoloDetectorEngine)
│   └── audio_pipeline.py      <-- 雙路 YAMNet + Whisper 音訊分析管道
│
├── ui/                        <-- 🌐 Web 監控與控制介面
│   ├── __init__.py
│   └── web_server.py          <-- Web 視訊流與 WASD 雙模式頁面 (WebStreamServer)
│
└── model/                     <-- 🧠 AI 模型資料夾
    └── best_ncnn_model/       <-- YOLO 視覺辨識模型
    ├── (yamnet.tflite)        <-- 聲音事件辨識模型
    └── (ggml-tiny.en.bin)     <-- Whisper 語音轉文字模型
```

---

## 🔌 樹莓派與 Arduino Mega I2C 接線與協定

### 1. 腳位接線

| 樹莓派 Pin | Arduino Mega 2560 Pin | 說明 |
| :--- | :--- | :--- |
| **SDA (GPIO 2 / Pin 3)** | **Pin 20 (SDA)** | I2C 數據線 |
| **SCL (GPIO 3 / Pin 5)** | **Pin 21 (SCL)** | I2C 時鐘線 |
| **GND (Pin 6 / GND)** | **GND** | **共地 (必接)** |

### 2. I2C 通訊協定 (位址 `0x08`)

* **指令發送 (Master -> Slave)**：
  發送 `0x01` 協議連同 4 位元組參數 `[pwml, pwmr, dirL, dirR]` 傳給 Arduino `control()` 函數。
  * `dir`: `1` = FORWARD, `2` = BACKWARD, `4` = RELEASE
* **數據索取 (Master <- Slave)**：
  請求 4 位元組 float，讀取即時超聲波距離 (cm)。

---

## 🎮 雙模式控制 (AUTO vs MANUAL)

在 Web 介面 (`http://<樹莓派IP>:8080/`) 上可切換模式：

1. **🤖 AUTO 自動模式**：
   * 聽覺（鬧鐘/門鈴聲）自動觸發尋物任務。
   * 視覺（YOLO 識別）自動對準與追蹤拖鞋。
2. **🎮 MANUAL 手動模式**：
   * 停用聲音自動觸發。
   * 透過 Web UI 畫面按鈕或鍵盤 **`W` (前進)、`S` (後退)、`A` (左轉)、`D` (右轉)、`B` (煞車)** 即時遙控小車。

---

## 🚀 快速啟動指南

### 1. 安裝樹莓派依賴環境
```bash
pip install smbus2 opencv-python ultralytics numpy sounddevice scipy
```

### 2. 執行主程式
```bash
python3 top.py
```

* **模擬鬧鐘測試**：
  ```bash
  python3 top.py --simulate-alarm
  ```
* **無硬體測試 (Dry-Run)**：
  ```bash
  python3 top.py --dry-run
  ```

---

## 🌐 Web 監視器存取

瀏覽器開啟：`http://<RaspberryPi-IP>:8080/`
* 即時檢視相機 YOLO 視訊流與辨識框。
* 實時顯示超聲波距離、Arduino I2C 連線狀態與音訊分析文字。
* 提供一鍵緊急停止按鈕。
