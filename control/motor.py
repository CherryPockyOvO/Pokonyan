# -*- coding: utf-8 -*-
"""Raspberry Pi Serial (UART) Motor Gateway sending direct control(pwml, pwmr, dirL, dirR) parameters to Arduino Mega 2560."""

import threading
import time

try:
    import serial
except ImportError:
    serial = None

# AFMotor 方向常數定義 (與 Arduino motor_control.h / AFMotor.h 完全對齊)
FORWARD = 1
BACKWARD = 2
BRAKE = 3
RELEASE = 4


class MotorGateway:
    """Serial (UART) Motor Gateway communicating with Arduino Mega over USB/Serial (/dev/ttyACM0)."""

    def __init__(self, port="/dev/ttyACM0", baudrate=115200, heartbeat_hz=15, dry_run=False, address=None, bus_id=None):
        self.port = port or "/dev/ttyACM0"
        self.baudrate = baudrate
        self.heartbeat_hz = heartbeat_hz
        self.dry_run = dry_run or (serial is None)

        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.ser = None

        # (pwml, pwmr, dirL, dirR)
        self.target_frame = (0, 0, RELEASE, RELEASE)

        self.connected = self.dry_run
        self.ready = self.dry_run
        self.distance_cm = None
        self.last_serial_at = time.monotonic() if self.dry_run else 0.0
        self.error = "" if serial is not None else "pyserial module unavailable (dry-run mode)"

    def start(self):
        if self.dry_run or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._service, name="serial_motor", daemon=True)
        self.thread.start()

    def _convert_speed_to_frame(self, left_speed, right_speed):
        """將帶符號速度 (-255 ~ 255) 轉換為 (pwml, pwmr, dirL, dirR) 參數。
        - 0: PWM=0, Direction=RELEASE (4)
        - 正數: PWM=val, Direction=FORWARD (1)
        - 負數: PWM=|val|, Direction=BACKWARD (2)
        """
        l_val = int(left_speed)
        r_val = int(right_speed)

        # 左輪方向與 PWM
        if l_val > 0:
            pwml = min(255, l_val)
            dirl = FORWARD
        elif l_val < 0:
            pwml = min(255, abs(l_val))
            dirl = BACKWARD
        else:
            pwml = 0
            dirl = RELEASE

        # 右輪方向與 PWM
        if r_val > 0:
            pwmr = min(255, r_val)
            dirr = FORWARD
        elif r_val < 0:
            pwmr = min(255, abs(r_val))
            dirr = BACKWARD
        else:
            pwmr = 0
            dirr = RELEASE

        return (pwml, pwmr, dirl, dirr)

    def set_target(self, left, right):
        """完全相容 top.py: 輸入範圍 -255 ~ 255 (0為RELEASE, >0前進, <0後退)。"""
        frame = self._convert_speed_to_frame(left, right)
        with self.lock:
            changed = frame != self.target_frame
            self.target_frame = frame
        if self.dry_run and changed:
            print(f"[Motor Serial dry-run] set_target({left}, {right}) -> (pwmL={frame[0]}, pwmR={frame[1]}, dirL={frame[2]}, dirR={frame[3]})")

    def control(self, pwml, pwmr, dir_l, dir_r):
        """直接設置 4 個 control 參數 (0-255, FORWARD=1, BACKWARD=2, RELEASE=4)。"""
        pwml = max(0, min(255, int(pwml)))
        pwmr = max(0, min(255, int(pwmr)))
        frame = (pwml, pwmr, dir_l, dir_r)
        with self.lock:
            self.target_frame = frame

    def emergency_stop(self):
        self.control(0, 0, RELEASE, RELEASE)
        if not self.dry_run and self.ser is not None and self.ser.is_open:
            try:
                self.ser.write(b"C 0 0 4 4\n")
            except Exception:
                pass

    def _send_serial_control_frame(self, frame):
        """透過 Serial 串口發送 4 個參數 'C <pwml> <pwmr> <dirL> <dirR>\n' 給 Arduino。"""
        pwml, pwmr, dirl, dirr = frame
        cmd_str = f"C {pwml} {pwmr} {dirl} {dirr}\n"
        self.ser.write(cmd_str.encode("ascii"))

    def _read_serial_telemetry(self):
        """讀取 Arduino 串口回傳的超聲波數據 (如 'D 25.4' 或 '25.4')。"""
        while self.ser.in_waiting > 0:
            line = self.ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if line.startswith("D"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        self.distance_cm = float(parts[1])
                    except ValueError:
                        pass
            else:
                try:
                    val = float(line)
                    if 0 <= val <= 500:
                        self.distance_cm = round(val, 1)
                except ValueError:
                    pass

    def _service(self):
        period = 1.0 / max(2.0, self.heartbeat_hz)

        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            time.sleep(2.0)  # 等待 Arduino 串口重置完成
            with self.lock:
                self.connected = True
                self.error = ""
            print(f"[Motor] Connected to Arduino Serial port {self.port} at {self.baudrate} baud")
        except Exception as error:
            with self.lock:
                self.connected = False
                self.ready = False
                self.error = f"Serial port init error: {error}"
            print(f"[Motor] Serial port unavailable: {error}")
            return

        last_sent_frame = None

        while not self.stop_event.is_set():
            now = time.monotonic()
            with self.lock:
                current_frame = self.target_frame

            try:
                # 1. 當 4 參數 Frame 改變時，透過串口發送
                if current_frame != last_sent_frame:
                    self._send_serial_control_frame(current_frame)
                    last_sent_frame = current_frame

                # 2. 讀取串口超聲波數據
                self._read_serial_telemetry()

                with self.lock:
                    self.last_serial_at = now
                    self.ready = True
                    self.connected = True
                    self.error = ""

            except Exception as error:
                with self.lock:
                    self.ready = False
                    self.error = f"Serial comm error: {error}"

            time.sleep(period)

        self.emergency_stop()
        if self.ser is not None and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

    def get_status(self):
        now = time.monotonic()
        with self.lock:
            age = None if not self.last_serial_at else now - self.last_serial_at
            fresh = self.dry_run or (self.ready and age is not None and age <= 1.0)
            pwml, pwmr, dirl, dirr = self.target_frame
            distance = 100.0 if self.dry_run and self.distance_cm is None else self.distance_cm

            return {
                "connected": self.connected,
                "ready": bool(fresh),
                "error": self.error,
                "target_left": pwml if dirl == FORWARD else (-pwml if dirl == BACKWARD else 0),
                "target_right": pwmr if dirr == FORWARD else (-pwmr if dirr == BACKWARD else 0),
                "telemetry_age_ms": None if age is None else round(age * 1000),
                "distance_cm": distance,
                "watchdog": False,
                "left_rpm": float(pwml),
                "right_rpm": float(pwmr),
            }

    def close(self):
        self.emergency_stop()
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3.0)
