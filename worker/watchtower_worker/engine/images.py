from __future__ import annotations

import base64

import cv2
import numpy as np

JPEG_QUALITY = 85


def resize_to_width(frame: np.ndarray, max_width: int | None) -> np.ndarray:
    if not max_width or frame.shape[1] <= max_width:
        return frame
    scale = max_width / frame.shape[1]
    return cv2.resize(frame, (max_width, max(1, round(frame.shape[0] * scale))), interpolation=cv2.INTER_AREA)


def crop_face(frame: np.ndarray, bbox: list[float], long_side: int, margin: float) -> np.ndarray:
    """Crop bbox (x1, y1, x2, y2) plus a margin, then scale the long side down to `long_side`."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    dx, dy = (x2 - x1) * margin, (y2 - y1) * margin
    x1, y1 = max(0, int(x1 - dx)), max(0, int(y1 - dy))
    x2, y2 = min(w, int(x2 + dx)), min(h, int(y2 + dy))
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    longest = max(crop.shape[:2])
    if longest > long_side:
        scale = long_side / longest
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))),
                          interpolation=cv2.INTER_AREA)
    return crop


def jpeg_b64(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")
