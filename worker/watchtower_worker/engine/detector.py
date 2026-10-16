# The OpenCV backend below is adapted from the YuNet demo in the OpenCV Zoo project,
# subject to the license terms of https://github.com/opencv/opencv_zoo.
# Copyright (C) 2021, Shenzhen Institute of Artificial Intelligence and Robotics for Society, all rights reserved.
# Third party copyrights are property of their respective owners.
"""YuNet face detectors. Both return [[x1, y1, x2, y2, confidence], ...] in frame pixels."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

# onnxruntime (1.31 on Linux) starts Microsoft's 1DS telemetry when it loads and posts to
# mobile.events.data.microsoft.com; disable_telemetry_events() alone doesn't stop that, the
# variable does. It must be set before onnxruntime is imported. No usage data leaves a camera host.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

WEIGHTS_DIR = Path(__file__).resolve().parent.parent / "weights"
ASPECT = 4 / 3  # input height = width / ASPECT; matches the bundled post-processing models

Detection = list[float]


class Detector(Protocol):
    def __call__(self, frame: np.ndarray) -> list[Detection]: ...


class OnnxYuNet:
    """YuNet via onnxruntime, with the bundled ONNX post-processor doing NMS (top 20, score >= 0.7)."""

    def __init__(self, resolution: int, confidence: float, max_faces: int, weights_dir: Path = WEIGHTS_DIR):
        import onnxruntime as ort

        ort.disable_telemetry_events()  # belt and braces; ORT_DISABLE_TELEMETRY (above) is what stops it
        self.width = resolution
        self.height = int(resolution / ASPECT)
        self.confidence = confidence
        self.max_faces = max_faces
        detector_path = weights_dir / "face_detection_yunet.onnx"
        post_path = weights_dir / f"postproc_yunet_top20_th70_{self.width}x{self.height}.onnx"
        for path in (detector_path, post_path):
            if not path.exists():
                raise FileNotFoundError(f"model file missing: {path}")
        opts = ort.SessionOptions()
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.intra_op_num_threads = 1  # one engine per stream; let streams use the cores
        providers = ["CPUExecutionProvider"]
        self._detector = ort.InferenceSession(str(detector_path), opts, providers=providers)
        self._input_name = self._detector.get_inputs()[0].name
        self._post = ort.InferenceSession(str(post_path), opts, providers=providers)
        self._post_inputs = [i.name for i in self._post.get_inputs()]

    def __call__(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        scale = min(self.height / h, self.width / w)
        rw, rh = int(w * scale), int(h * scale)
        padded = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        padded[:rh, :rw] = cv2.resize(frame, (rw, rh))
        blob = padded.astype(np.float32).transpose(2, 0, 1)[np.newaxis]
        raw = self._detector.run(None, {self._input_name: blob})
        rows = self._post.run(None, dict(zip(self._post_inputs, raw[:3], strict=True)))[0]

        # Coordinates come back normalised to the padded input.
        sx, sy = self.width / rw * w, self.height / rh * h
        out: list[Detection] = []
        for row in rows:
            conf = float(row[-1])
            if conf < self.confidence:
                continue
            x1 = int(max(0, min(w, row[0] * sx)))
            y1 = int(max(0, min(h, row[1] * sy)))
            x2 = int(max(0, min(w, row[2] * sx)))
            y2 = int(max(0, min(h, row[3] * sy)))
            if x2 > x1 and y2 > y1:
                out.append([x1, y1, x2, y2, conf])
        out.sort(key=lambda d: d[4], reverse=True)
        return out[: self.max_faces]


class OpenCvYuNet:
    """YuNet via OpenCV's FaceDetectorYN (no onnxruntime post-processor, any confidence)."""

    def __init__(self, resolution: int, confidence: float, max_faces: int, weights_dir: Path = WEIGHTS_DIR):
        self.size = (resolution, int(resolution / ASPECT))
        self.max_faces = max_faces
        self._model = cv2.FaceDetectorYN.create(
            model=str(weights_dir / "face_detection_yunet_2023mar.onnx"), config="", input_size=self.size,
            score_threshold=float(confidence), nms_threshold=0.3, top_k=5000,
        )

    def __call__(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        _, faces = self._model.detect(cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA))
        if faces is None:
            return []
        sx, sy = w / self.size[0], h / self.size[1]
        out = []
        for face in faces:  # x, y, w, h, 10 landmark coords, score
            x, y, fw, fh = face[:4]
            x1, y1 = int(max(0, x * sx)), int(max(0, y * sy))
            x2, y2 = int(min(w, (x + fw) * sx)), int(min(h, (y + fh) * sy))
            if x2 > x1 and y2 > y1:
                out.append([x1, y1, x2, y2, float(face[-1])])
        out.sort(key=lambda d: d[4], reverse=True)
        return out[: self.max_faces]


def build_detector(backend: str, resolution: int, confidence: float, max_faces: int) -> Detector:
    if backend == "opencv":
        return OpenCvYuNet(resolution, confidence, max_faces)
    return OnnxYuNet(resolution, confidence, max_faces)
