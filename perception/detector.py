# -*- coding: utf-8 -*-
"""Camera, YOLO shoe detection, and annotated MJPEG frames."""

from pathlib import Path
import threading
import time

import cv2
import numpy as np
from ultralytics import YOLO

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None


class YoloDetectorEngine:
    """Keep only the newest camera frame and newest single-shoe detection."""

    def __init__(
        self,
        model_path="best_ncnn_model",
        target_video_fps=20,
        infer_fps=5,
        confidence=0.35,
        alpha=0.35,
        detection_hold_seconds=0.6,
    ):
        self.model_path = Path(model_path).expanduser().resolve()
        self.target_video_fps = target_video_fps
        self.infer_fps = infer_fps
        self.confidence = confidence
        self.alpha = alpha
        self.detection_hold_seconds = detection_hold_seconds

        if not self.model_path.exists():
            print(f"[Vision] Warning: YOLO model path not found: {self.model_path}")
        self.model = None

        self.lock = threading.Lock()
        self.latest_annotated_frame = self._placeholder("Starting camera...")
        self.latest_target = None
        self.frame_at = 0.0
        self.inference_at = 0.0
        self.last_detection_at = 0.0
        self.target_sequence = 0
        self.ready = False
        self.error = ""

        self.smoothed_boxes = []
        self.running = False
        self.thread = None
        self.picam2 = None
        self.camera_type = None

    @staticmethod
    def _placeholder(message):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(
            frame,
            "Raspberry Pi 5 Robot",
            (145, 200),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
        )
        cv2.putText(
            frame,
            message,
            (70, 260),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2,
        )
        return frame

    def _open_camera(self):
        if Picamera2 is not None:
            try:
                camera = Picamera2()
                configuration = camera.create_video_configuration(
                    main={"size": (640, 480), "format": "RGB888"},
                    controls={"FrameRate": self.target_video_fps},
                    buffer_count=2,
                )
                camera.configure(configuration)
                camera.start()
                self.picam2 = camera
                return "picamera2"
            except Exception as error:
                print(f"[Vision] Picamera2 unavailable: {error}")
        return None

    def _read_frame(self):
        if self.camera_type == "picamera2":
            try:
                frame = self.picam2.capture_array("main")
                return frame is not None and frame.size > 0, frame
            except Exception:
                return False, None
        return False, None

    def _smooth(self, new_boxes):
        if not new_boxes:
            self.smoothed_boxes = []
            return
        if not self.smoothed_boxes:
            self.smoothed_boxes = new_boxes
            return

        smoothed = []
        for new_box in new_boxes:
            nx1, ny1, nx2, ny2, confidence, class_id = new_box
            match = None
            match_iou = 0.0
            for old_box in self.smoothed_boxes:
                ox1, oy1, ox2, oy2, _, old_class = old_box
                if int(old_class) != int(class_id):
                    continue
                ix1, iy1 = max(nx1, ox1), max(ny1, oy1)
                ix2, iy2 = min(nx2, ox2), min(ny2, oy2)
                intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                union = (
                    (nx2 - nx1) * (ny2 - ny1)
                    + (ox2 - ox1) * (oy2 - oy1)
                    - intersection
                )
                iou = intersection / (union + 1e-6)
                if iou > match_iou:
                    match, match_iou = old_box, iou
            if match is not None and match_iou > 0.3:
                ox1, oy1, ox2, oy2, _, _ = match
                keep = 1.0 - self.alpha
                new_box = [
                    self.alpha * nx1 + keep * ox1,
                    self.alpha * ny1 + keep * oy1,
                    self.alpha * nx2 + keep * ox2,
                    self.alpha * ny2 + keep * oy2,
                    confidence,
                    class_id,
                ]
            smoothed.append(new_box)
        self.smoothed_boxes = smoothed

    def _update_target(self, width, height):
        now = time.monotonic()
        target = None
        if self.smoothed_boxes:
            # The training set has one shoe class. Prefer the largest box,
            # breaking ties by confidence.
            best = max(
                self.smoothed_boxes,
                key=lambda box: (
                    max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1]),
                    box[4],
                ),
            )
            x1, y1, x2, y2, confidence, _ = best
            self.target_sequence += 1
            target = {
                "centre_x": ((x1 + x2) / 2.0 - width / 2.0) / (width / 2.0),
                "height_ratio": max(0.0, y2 - y1) / height,
                "confidence": float(confidence),
                "detected_at": now,
                "sequence": self.target_sequence,
            }
        with self.lock:
            self.latest_target = target
            self.inference_at = now
            self.ready = True

    def _annotate(self, frame):
        annotated = frame.copy()
        for x1, y1, x2, y2, confidence, _ in self.smoothed_boxes:
            cv2.rectangle(
                annotated,
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                (0, 255, 0),
                2,
            )
            cv2.putText(
                annotated,
                f"shoe {confidence:.2f}",
                (int(x1), max(20, int(y1) - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
        return annotated

    def _loop(self):
        if self.model is None and self.model_path.exists():
            print(f"[Vision] Async loading YOLO model: {self.model_path}")
            try:
                self.model = YOLO(str(self.model_path), task="detect")
                print(f"[Vision] YOLO model loaded successfully!")
            except Exception as error:
                print(f"[Vision] YOLO model load warning: {error}")
                self.error = f"YOLO model load warning: {error}"

        self.camera_type = self._open_camera()
        while self.running and self.camera_type is None:
            with self.lock:
                self.error = "camera busy / unavailable"
                self.latest_annotated_frame = self._placeholder("Camera busy / Reconnecting...")
            time.sleep(1.0)
            self.camera_type = self._open_camera()

        frame_period = 1.0 / max(1.0, self.target_video_fps)
        inference_period = 1.0 / max(0.1, self.infer_fps)
        next_inference = 0.0

        try:
            while self.running:
                started = time.monotonic()
                ok, frame = self._read_frame()
                if not ok or frame is None:
                    with self.lock:
                        self.error = "camera frame unavailable"
                    time.sleep(0.02)
                    continue

                now = time.monotonic()
                with self.lock:
                    self.frame_at = now
                    self.error = ""

                if self.model is not None and now >= next_inference:
                    next_inference = now + inference_period
                    try:
                        results = self.model.predict(
                            frame,
                            imgsz=640,
                            conf=self.confidence,
                            max_det=5,
                            verbose=False,
                        )
                        boxes = []
                        if results:
                            for result_box in results[0].boxes:
                                x1, y1, x2, y2 = (
                                    float(value)
                                    for value in result_box.xyxy[0].tolist()
                                )
                                boxes.append(
                                    [
                                        x1,
                                        y1,
                                        x2,
                                        y2,
                                        float(result_box.conf[0].item()),
                                        int(result_box.cls[0].item()),
                                    ]
                                )
                        if boxes:
                            self._smooth(boxes)
                            self._update_target(frame.shape[1], frame.shape[0])
                            self.last_detection_at = now
                        elif (
                            self.last_detection_at <= 0
                            or now - self.last_detection_at
                            > self.detection_hold_seconds
                        ):
                            self._smooth([])
                            self._update_target(frame.shape[1], frame.shape[0])
                        else:
                            with self.lock:
                                self.inference_at = now
                                self.ready = True
                    except Exception as error:
                        self.smoothed_boxes = []
                        with self.lock:
                            self.latest_target = None
                            self.error = f"YOLO inference failed: {error}"

                annotated = self._annotate(frame)
                with self.lock:
                    self.latest_annotated_frame = annotated

                remaining = frame_period - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            if self.picam2 is not None:
                self.picam2.stop()
                self.picam2.close()

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="vision", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=3.0)

    def get_target(self, max_age=0.5):
        with self.lock:
            target = None if self.latest_target is None else dict(self.latest_target)
        if target is None or time.monotonic() - target["detected_at"] > max_age:
            return None
        return target

    def get_status(self):
        now = time.monotonic()
        with self.lock:
            return {
                "ready": self.ready,
                "error": self.error,
                "frame_age_ms": (
                    None if not self.frame_at else round((now - self.frame_at) * 1000)
                ),
                "inference_age_ms": (
                    None
                    if not self.inference_at
                    else round((now - self.inference_at) * 1000)
                ),
                "target": None if self.latest_target is None else dict(self.latest_target),
            }

    def get_jpeg_frame(self):
        with self.lock:
            frame = self.latest_annotated_frame
            if frame is None:
                return None
            frame = frame.copy()
        ok, jpeg = cv2.imencode(".jpg", frame)
        return jpeg.tobytes() if ok else None
