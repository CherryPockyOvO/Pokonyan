# 🤖 Raspberry Pi 5 Shoe-Seeking Robot (Pokonyan)

基於樹莓派 5 (Raspberry Pi 5) 與 Arduino Mega 2560 的全功能智慧尋物機器人專案。

本專案整合了 **AI 聲音辨識 (YAMNet + Whisper)**、**YOLO 視覺檢測**、**Arduino 串口 Serial (UART) 底盤控制** 及 **Web 雙模式 (AUTO / MANUAL) 遙控介面**。

---

## 📂 模組化專案目錄結構

```text
raspi/Pokonyan/
├── top.py                     <-- 🎯 樹莓派主程式入口 (調度與模式切換)
├── pc_audio_client.py         <-- 🎙️ PC 電腦端語音客戶端 (減輕樹莓派負擔)
├── run_remote_pi.sh           <-- 🚀 本地電腦一鍵 SSH 同步 git pull 並啟動全套系統
├── README.md                  <-- 本說明文件
│
├── control/                   <-- 🕹️ 控制模組
│   ├── __init__.py
│   ├── auto_controller.py     <-- 自動模式狀態機 (AutoController)
│   ├── manual_controller.py   <-- 手動模式 8 方向遙控 (ManualController)
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
    ├── best_ncnn_model/       <-- YOLO 視覺辨識模型
    └── yamnet.tflite          <-- 聲音事件辨識模型
```

---

## 💻 語音分流與 PC 端自動化架構 (PC Audio Offload)

為了減輕樹莓派 5 運行 Whisper / YAMNet 的負擔，聲音處理可**分離部署於本地 PC/Mac 電腦上**：

```mermaid
flowchart LR
    LocalPC[💻 本地 PC/Mac (pc_audio_client.py)] -->|16kHz 麥克風音訊| AI[YAMNet + Whisper]
    AI -- 聽見鬧鐘聲 (HTTP POST) --> Pi[🤖 樹莓派 5 (top.py --no-audio)]
    Pi -->|串口 Serial| Arduino[⚡ Arduino Mega 2560]
```

1. **樹莓派**：運行輕量化 `top.py --no-audio`（專注視覺與串口底盤操控）。
2. **PC 電腦**：運行 `pc_audio_client.py`，使用電腦麥克風執行 YAMNet / Whisper，聽見鬧鐘時自動發送 HTTP POST `/trigger_audio_event` 給樹莓派。

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
   * 支援 8 方向鍵盤與 Web UI 操作：`W` (前進)、`S` (後退)、`A` (左轉)、`D` (右轉)、`WD` (右前)、`WA` (左前)、`SD` (右後)、`SA` (左後)、`B` (煞車)。

---

## 🚀 快速啟動指南

### 1. 一鍵 SSH 自動同步 git pull 並啟動全套系統 (PC 端執行)
在本地電腦終端機直接執行：
```bash
./run_remote_pi.sh Milos-Pi5.local
```
此腳本會自動：
1. SSH 連線至 `xzm@Milos-Pi5.local`。
2. 在樹莓派上執行 `git fetch origin main && git reset --hard origin/main` 獲取最新代碼。
3. 在樹莓派上啟動 `python3 top.py --no-audio`。
4. 在本地電腦自動啟動 `pc_audio_client.py` 進行語音分流處理。

---

## 🌐 Web 監視器存取

瀏覽器開啟：`http://Milos-Pi5.local:8080/`
* 即時檢視相機 YOLO 視訊流與辨識框。
* 實時顯示超聲波距離、Arduino 串口連線狀態與 8 方向操控介面。
