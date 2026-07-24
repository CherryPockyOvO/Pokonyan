# -*- coding: utf-8 -*-
"""Minimal Raspberry Pi to Arduino motor/telemetry gateway."""

import threading
import time


def parse_telemetry(line):
    # TEL ms targetL rpmL pwmL targetR rpmR pwmR distance watchdog
    fields = line.split()
    if not fields or fields[0] != "TEL":
        return None
    if len(fields) != 10 or fields[9] not in ("0", "1"):
        raise ValueError("invalid TEL message")
    distance = float(fields[8])
    return {
        "left_target": float(fields[2]),
        "left_rpm": float(fields[3]),
        "left_pwm": int(fields[4]),
        "right_target": float(fields[5]),
        "right_rpm": float(fields[6]),
        "right_pwm": int(fields[7]),
        "distance_cm": None if distance < 0 else distance,
        "watchdog": fields[9] == "1",
    }


class MotorGateway:
    """Send a fixed-rate heartbeat and never restore motion after reconnecting."""

    def __init__(self, port="/dev/ttyACM0", baud=115200, heartbeat_hz=15, dry_run=False):
        self.port_name = port
        self.baud = baud
        self.heartbeat_hz = heartbeat_hz
        self.dry_run = dry_run

        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.serial_port = None
        self.target = (0, 0)
        self.connected = dry_run
        self.ready = dry_run
        self.telemetry = None
        self.telemetry_at = time.monotonic() if dry_run else 0.0
        self.error = ""

    def start(self):
        if self.dry_run or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._service, name="arduino", daemon=True)
        self.thread.start()

    def set_target(self, left_rpm, right_rpm):
        target = (
            max(-300, min(300, int(left_rpm))),
            max(-300, min(300, int(right_rpm))),
        )
        with self.lock:
            changed = target != self.target
            self.target = target
        if self.dry_run and changed:
            print(f"[Motor dry-run] V {target[0]} {target[1]}")

    def emergency_stop(self):
        self.set_target(0, 0)
        with self.lock:
            port = self.serial_port
        if port is not None:
            try:
                port.write(b"S\n")
                port.flush()
            except Exception:
                pass

    def _set_disconnected(self, message):
        with self.lock:
            self.connected = False
            self.ready = False
            self.target = (0, 0)
            self.telemetry = None
            self.telemetry_at = 0.0
            self.error = message
            self.serial_port = None

    def _service(self):
        import serial

        period = 1.0 / max(2.0, self.heartbeat_hz)
        while not self.stop_event.is_set():
            port = None
            try:
                port = serial.Serial(
                    self.port_name,
                    self.baud,
                    timeout=0.03,
                    write_timeout=0.2,
                )
                with self.lock:
                    self.serial_port = port
                    self.connected = True
                    self.ready = False
                    self.target = (0, 0)
                    self.error = ""
                print(f"[Motor] Arduino connected: {self.port_name}")

                # Opening USB serial resets a Mega. Motion remains zero during boot.
                if self.stop_event.wait(2.0):
                    port.close()
                    return
                port.reset_input_buffer()
                next_write = 0.0

                while not self.stop_event.is_set():
                    now = time.monotonic()
                    if now >= next_write:
                        next_write = now + period
                        with self.lock:
                            fresh = (
                                self.ready
                                and self.telemetry_at
                                and now - self.telemetry_at <= 0.8
                            )
                            if not fresh:
                                self.ready = False
                                self.target = (0, 0)
                            left, right = self.target
                        port.write(f"V {left} {right}\n".encode("ascii"))

                    line = port.readline().decode("ascii", errors="ignore").strip()
                    if not line:
                        continue
                    try:
                        telemetry = parse_telemetry(line)
                    except (ValueError, TypeError) as error:
                        print(f"[Motor] Ignored telemetry: {error}: {line}")
                        continue
                    if telemetry is not None:
                        with self.lock:
                            self.telemetry = telemetry
                            self.telemetry_at = time.monotonic()
                            self.ready = True
                            self.error = ""

                self.emergency_stop()
            except (OSError, serial.SerialException) as error:
                self._set_disconnected(str(error))
                print(f"[Motor] Arduino unavailable: {error}")
                self.stop_event.wait(1.0)
            finally:
                if port is not None:
                    try:
                        port.close()
                    except Exception:
                        pass

    def get_status(self):
        now = time.monotonic()
        with self.lock:
            telemetry = None if self.telemetry is None else dict(self.telemetry)
            age = None if not self.telemetry_at else now - self.telemetry_at
            fresh = self.dry_run or (
                self.ready and age is not None and age <= 0.8
            )
            left, right = self.target
            distance = (
                100.0
                if self.dry_run and telemetry is None
                else (None if telemetry is None else telemetry["distance_cm"])
            )
            return {
                "connected": self.connected,
                "ready": bool(fresh),
                "error": self.error,
                "target_left": left,
                "target_right": right,
                "telemetry_age_ms": None if age is None else round(age * 1000),
                "distance_cm": distance,
                "watchdog": True if telemetry is None else telemetry["watchdog"],
                "left_rpm": None if telemetry is None else telemetry["left_rpm"],
                "right_rpm": None if telemetry is None else telemetry["right_rpm"],
            }

    def close(self):
        self.emergency_stop()
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3.0)
