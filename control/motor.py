# -*- coding: utf-8 -*-
"""Raspberry Pi I2C Motor Gateway sending direct control(pwml, pwmr, dirL, dirR) parameters to Arduino Mega Slave (Address 0x08)."""

import struct
import threading
import time

try:
    import smbus2 as smbus
except ImportError:
    try:
        import smbus
    except ImportError:
        smbus = None

# AFMotor 方向常數定義 (與 Arduino motor_control.h / AFMotor.h 完全對齊)
FORWARD = 1
BACKWARD = 2
BRAKE = 3
RELEASE = 4


class MotorGateway:
    """I2C Motor Gateway sending direct 4-parameter control frames to Arduino (Address 0x08)."""

    def __init__(self, bus_id=1, address=0x08, heartbeat_hz=15, dry_run=False, port=None):
        self.bus_id = bus_id
        self.address = address
        self.heartbeat_hz = heartbeat_hz
        self.dry_run = dry_run or (smbus is None)

        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.bus = None

        # (pwml, pwmr, dirL, dirR)
        self.target_frame = (0, 0, RELEASE, RELEASE)

        self.connected = self.dry_run
        self.ready = self.dry_run
        self.distance_cm = None
        self.last_i2c_at = time.monotonic() if self.dry_run else 0.0
        self.error = "" if smbus is not None else "smbus2 module unavailable (dry-run mode)"

    def start(self):
        if self.dry_run or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._service, name="i2c_motor", daemon=True)
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
            print(f"[Motor I2C dry-run] set_target({left}, {right}) -> (pwmL={frame[0]}, pwmR={frame[1]}, dirL={frame[2]}, dirR={frame[3]})")

    def control(self, pwml, pwmr, dir_l, dir_r):
        """直接設置 4 個 control 參數 (0-255, FORWARD=1, BACKWARD=2, RELEASE=4)。"""
        pwml = max(0, min(255, int(pwml)))
        pwmr = max(0, min(255, int(pwmr)))
        frame = (pwml, pwmr, dir_l, dir_r)
        with self.lock:
            self.target_frame = frame

    def emergency_stop(self):
        self.control(0, 0, RELEASE, RELEASE)
        if not self.dry_run and self.bus is not None:
            try:
                self.bus.write_i2c_block_data(self.address, 0x01, [0, 0, RELEASE, RELEASE])
            except Exception:
                pass

    def _send_i2c_control_frame(self, frame):
        """直接透過 I2C 發送 0x01 暫存器指令及 4 個參數 [pwml, pwmr, dirL, dirR]。"""
        pwml, pwmr, dirl, dirr = frame
        payload = [pwml, pwmr, dirl, dirr]
        self.bus.write_i2c_block_data(self.address, 0x01, payload)

    def _read_i2c_distance(self):
        """向 Arduino I2C 從機索取 4 位元組的 float 超聲波距離 (cm)。"""
        try:
            data = self.bus.read_i2c_block_data(self.address, 0, 4)
            if len(data) == 4:
                dist = struct.unpack('<f', bytes(data))[0]
                return None if dist < 0 else round(dist, 1)
        except Exception:
            pass
        return None

    def _service(self):
        period = 1.0 / max(2.0, self.heartbeat_hz)

        try:
            self.bus = smbus.SMBus(self.bus_id)
            with self.lock:
                self.connected = True
                self.error = ""
            print(f"[Motor] Connected to Arduino I2C bus {self.bus_id} at address 0x{self.address:02X}")
        except Exception as error:
            with self.lock:
                self.connected = False
                self.ready = False
                self.error = f"I2C init error: {error}"
            print(f"[Motor] I2C bus unavailable: {error}")
            return

        last_sent_frame = None

        while not self.stop_event.is_set():
            now = time.monotonic()
            with self.lock:
                current_frame = self.target_frame

            try:
                # 1. 4 參數 Frame 發生改變時，直接透過 I2C 發送 0x01 [pwml, pwmr, dirL, dirR]
                if current_frame != last_sent_frame:
                    self._send_i2c_control_frame(current_frame)
                    last_sent_frame = current_frame

                # 2. 定期透過 I2C 讀取 Arduino 的超聲波距離
                dist = self._read_i2c_distance()

                with self.lock:
                    self.distance_cm = dist
                    self.last_i2c_at = now
                    self.ready = True
                    self.connected = True
                    self.error = ""

            except Exception as error:
                with self.lock:
                    self.ready = False
                    self.error = f"I2C comm error: {error}"

            time.sleep(period)

        self.emergency_stop()
        if self.bus is not None:
            try:
                self.bus.close()
            except Exception:
                pass

    def get_status(self):
        now = time.monotonic()
        with self.lock:
            age = None if not self.last_i2c_at else now - self.last_i2c_at
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
