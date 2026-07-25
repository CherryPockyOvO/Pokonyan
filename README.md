# 🤖 Raspberry Pi 5 Shoe-Seeking Robot (Pokonyan)

基於樹莓派 5 (Raspberry Pi 5) 與 Arduino Mega 2560 的全功能智慧尋物機器人專案。

本專案整合了 **AI 聲音辨識 (YAMNet + Whisper)**、**YOLO 視覺檢測**、**Arduino 串口 Serial (UART) 底盤控制** 及 **Web 雙模式 (AUTO / MANUAL) 遙控介面**。

---

## 📂 模組化專案目錄結構

```text
raspi/Pokonyan/
├── top.py                     <-- 🎯 主程式入口 (調度與模式切換)
├── README.md                  <-- 本說明文件
│
├── control/                   <-- 🕹️ 控制模組
│   ├── __init__.py
│   ├── auto_controller.py     <-- 自動模式狀態機 (AutoController)
│   ├── manual_controller.py   <-- 手動模式 WASD 遙控 (ManualController)
│   └── motor.py               <-- Arduino Mega 串口 (UART) 底盤通訊門戶 (MotorGateway)
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
```

---

## 🔌 樹莓派與 Arduino Mega 串口 (UART) 接線與協定

### 1. 接線方式
使用 USB Type-A 轉 Type-B 傳輸線直接連接樹莓派 USB 埠與 Arduino Mega 的 USB 序列埠（預設序列埠 `/dev/ttyACM0`，波特率 `115200`）。

### 2. 串口通訊協定 (Serial Protocol)

* **指令發送 (樹莓派 -> Arduino)**：
  發送 4 位元組運動控制指令：`C <pwml> <pwmr> <dirL> <dirR>\n`
  * `pwml`, `pwmr`: PWM 數值 `0 ~ 255`
  * `dirL`, `dirR`: 方向 `1` = FORWARD, `2` = BACKWARD, `4` = RELEASE
* **數據接收 (樹莓派 <- Arduino)**：
  讀取 Arduino 印出的即時超聲波距離：`D <distance_cm>\n`

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
pip install pyserial opencv-python ultralytics numpy sounddevice scipy
```

### 2. 執行主程式
```bash
python3 top.py --serial /dev/ttyACM0
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
* 實時顯示超聲波距離、Arduino 串口連線狀態與音訊分析文字。
* 提供一鍵緊急停止按鈕。
