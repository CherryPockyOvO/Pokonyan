# 鞋子提醒机器人

`top.py` 是树莓派唯一入口。它启动相机/YOLO、YAMNet/本地 Whisper、
Arduino 串口、循鞋状态机和 8080 网页监视。

## 文件

```text
rpi5_deploy/
├── top.py                         唯一入口和任务状态机
├── detector.py                    相机、YOLO、唯一 shoe 的位置
├── audio_pipeline.py              alarm/clock/speech 与本地 Whisper
├── motor.py                       Arduino 串口、遥测和速度心跳
├── web_server.py                  视频、状态和紧急停止
├── yamnet.tflite
├── ggml-tiny.en.bin               当前只识别英文
├── best_ncnn_model/               shoe YOLO26n NCNN 模型
└── arduino/robot_car/robot_car.ino
```

烧进 Mega 的文件只有 `arduino/robot_car/robot_car.ino`。其余文件复制到
树莓派；树莓派不需要 Arduino IDE。

## 当前算法

1. YAMNet 连续检测 `alarm`、`alarm_clock` 和 `speech`。两个连续窗口超过
   阈值才产生事件。
2. `speech` 打开语音门，语音结束后由树莓派上的 Whisper.cpp 转成英文；
   `alarm` 和 `alarm_clock` 才启动找鞋任务。
3. 小车最多执行 8 次扫描：原地左转约 45°，停车 2 秒检测。
4. 鞋子横向中心超出 `±0.18` 时先停车，再向对应方向原地转；鞋子回到
   中央后直行。
5. 正常接近速度 85 RPM。鞋框较大或超声波距离小于 30 cm 后降为
   35 RPM。
6. 只有鞋子曾经“较近且居中”后消失，才允许进入最长 2 秒的 25 RPM
   超声波盲行；测距无效或距离仍大于 45 cm 都会停车失败。
7. 距离小于 15 cm 时不停车，也不结束任务，只把当时的前进速度降为
   60%。例如 35 RPM 会降为 21 RPM，25 RPM 会降为 15 RPM。
8. Arduino 断线、遥测超过 0.8 秒、任务超过 120 秒或网页急停都会产生
   零速度；鞋子消失后的盲行阶段如果没有有效超声波读数也会立即停车。

45°目前由 `top.py` 中的 `rotate_step_seconds = 0.75` 开环估算，必须在
实际地面重新标定。

## Arduino 接线

```text
左侧电机：Motor Shield M3/M4
右侧电机：Motor Shield M1/M2
左/右编码器：D18/D19
HC-SR04 TRIG/ECHO：D24/D25
USB 串口：115200
```

固件只上报超声波距离，不会因为近距离物体自行停车；15 cm 的 60%降速由
`top.py` 执行。串口命令 900 ms 超时仍会停车。

## 树莓派安装

推荐把整个目录复制为：

```text
/home/pi/rpi5_deploy/
```

安装系统相机包和虚拟环境：

```bash
sudo apt update
sudo apt install python3-picamera2 python3-opencv python3-venv \
  libportaudio2 portaudio19-dev build-essential cmake
cd /home/pi/rpi5_deploy
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install ultralytics ai-edge-litert pywhispercpp sounddevice scipy numpy pyserial
```

如串口没有权限：

```bash
sudo usermod -aG dialout $USER
```

重新登录后生效。

## 分阶段运行

先测试相机、YOLO和网页，不开电机：

```bash
python top.py --vision-only
```

只测试 YAMNet、麦克风和本地 Whisper：

```bash
python top.py --audio-only
```

在电脑上模拟完整状态机，不打开 Arduino：

```bash
python top.py --dry-run --no-audio --simulate-alarm
```

连接 Arduino 后，用模拟告警测试真实小车循鞋。第一次必须架空车轮：

```bash
python top.py --no-audio --simulate-alarm
```

全部模块合成运行：

```bash
python top.py
```

浏览器监视地址：

```text
http://树莓派IP:8080/
```

网页红色按钮会将状态机置为 `E_STOP`，同时向 Arduino 发送停止命令。

正式落地测试前，先在 Serial Monitor 中验证：

```text
V 60 60      前进
V -60 -60    后退
V -60 60     左转
V 60 -60     右转
S            停止
```
