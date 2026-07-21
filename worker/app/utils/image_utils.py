# app/utils/image_utils.py
from __future__ import annotations
from typing import Any, Tuple, List, Union
import numpy as np
import cv2
import math

BBoxLike = Union[dict, list, tuple]

def visualize(
    image: np.ndarray,
    face_id: int,
    bbox: list,
    confidence: float,
    landmarks: list = None,
    box_color: tuple = (0, 255, 0),
    text_color: tuple = (0, 0, 255),
    fps: float = None
) -> np.ndarray:
    output = image.copy()
    landmark_color = [
        (255,   0,   0), # right eye
        (  0,   0, 255), # left eye
        (  0, 255,   0), # nose tip
        (255,   0, 255), # right mouth corner
        (  0, 255, 255)  # left mouth corner
    ]
    if fps is not None:
        cv2.putText(output, 'FPS: {:.2f}'.format(fps), (0, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, text_color)
    cv2.rectangle(output, (bbox[0], bbox[1]), (bbox[2], bbox[3]), box_color, 2)
    cv2.putText(output, '{}_{:.4f}'.format(str(face_id), confidence), (bbox[0], bbox[1]+12), cv2.FONT_HERSHEY_DUPLEX, 0.5, text_color)

    if landmarks:
        for idx, landmark in enumerate(landmarks):
            cv2.circle(output, landmark, 2, landmark_color[idx], 2)
    return output


def cropImage(
    img: np.ndarray,
    box: list,
    ratio: tuple = (3, 4),
    offset_x: float = 0.3,
    width: int = 120
) -> tuple[np.ndarray, list]:
    xmin, ymin, xmax, ymax = box
    # xmax, ymax = xmin + w, ymin + h
    img_h, img_w = img.shape[:2]
    box_w, box_h = abs(xmax - xmin), abs(ymax - ymin)
    box_hn = int((box_w * ratio[1]) / ratio[0])
    diff_h = abs(box_hn - box_h)
    box = [xmin, (ymin - int(diff_h * 0.9)), xmax, (ymax + int(diff_h * 0.1))]
    target_xmin = int(xmin - (box_w * offset_x))
    step_px = 2
    while True:
        xmin, ymin, xmax, ymax = box
        xmin = int(xmin - step_px)
        ymin = int(ymin - (step_px * ratio[1]/ratio[0]))
        xmax = int(xmax + step_px)
        ymax = int(ymax + (step_px * ratio[1]/ratio[0]))
        if (
            (xmin <= target_xmin) or
            (xmin <= 0 or ymin <= 0 or xmax >= img_w or ymax >= img_h)
        ):
            xmin, ymin = max(xmin, 0), max(ymin, 0)
            xmax, ymax = min(xmax, img_w), min(ymax, img_h)
            break
        box = [xmin, ymin, xmax, ymax]
    crop = img[ymin:ymax, xmin:xmax]
    crop = image_resize(crop, width=width)
    return crop, box


def image_resize(
    image: np.ndarray,
    width: int = None,
    height: int = None,
    inter: int = cv2.INTER_AREA
) -> np.ndarray:
    dim = None
    (h, w) = image.shape[:2]
    if width is None and height is None:
        return image
    if width is None:
        r = height / float(h)
        dim = (int(w * r), height)
    else:
        r = width / float(w)
        dim = (width, int(h * r))
    resized = cv2.resize(image, dim, interpolation = inter)
    return resized


def encode_image_to_bytes(image: np.ndarray, format: str = ".jpg") -> bytes:
    """Encode OpenCV image to binary. Raises on failure."""
    if image is None or image.size == 0:
        raise ValueError("encode_image_to_bytes: empty image")
    ok, buf = cv2.imencode(format, image)
    if not ok:
        raise ValueError("encode_image_to_bytes: cv2.imencode failed")
    return buf.tobytes()

def _bbox_to_xyxy(bbox: BBoxLike) -> Tuple[int, int, int, int]:
    """
    Accepts:
      - dict: {x, y, w, h}  (float or int)
      - list/tuple: [x1, y1, x2, y2]
    Returns int (x1, y1, x2, y2).
    """
    if isinstance(bbox, dict):
        x = float(bbox.get("x", 0.0))
        y = float(bbox.get("y", 0.0))
        w = float(bbox.get("w", 0.0))
        h = float(bbox.get("h", 0.0))
        x1, y1, x2, y2 = x, y, x + w, y + h
    elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        x1, y1, x2, y2 = map(float, bbox)
    else:
        raise ValueError(f"bbox must be dict(x,y,w,h) or [x1,y1,x2,y2], got: {type(bbox)}")

    # sanitize NaNs and negatives
    vals = [x1, y1, x2, y2]
    vals = [0.0 if (v is None or math.isnan(v) or math.isinf(v)) else v for v in vals]
    x1, y1, x2, y2 = vals
    # ensure ordering
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    # cast to ints (floor for start, ceil for end)
    return int(math.floor(x1)), int(math.floor(y1)), int(math.ceil(x2)), int(math.ceil(y2))

def _clamp_xyxy(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> Tuple[int, int, int, int]:
    x1 = max(0, min(x1, w - 1))
    y1 = max(0, min(y1, h - 1))
    x2 = max(0, min(x2, w))
    y2 = max(0, min(y2, h))
    # ensure non-empty after clamp
    if x2 <= x1: x2 = min(w, x1 + 1)
    if y2 <= y1: y2 = min(h, y1 + 1)
    return x1, y1, x2, y2

def _resize_long_side(img: np.ndarray, target_long: int) -> np.ndarray:
    if target_long is None:
        return img
    if target_long <= 0:
        return img
    h, w = img.shape[:2]
    long_side = max(h, w)
    if long_side <= target_long:
        return img
    scale = target_long / float(long_side)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

def get_bin_images(
    frame: np.ndarray,
    face_crop: np.ndarray,
    frame_width: int,
    frame_height: int,
    format: str = ".jpg",
) -> tuple[bytes, bytes]:
    """
    Returns (full_frame_bytes, normalized_crop_bytes)
    - Crops from the *original* frame, then optionally resizes the full frame
    """
    # 1) resize the *full frame* if bigger than target
    if isinstance(frame_width, int) and isinstance(frame_height, int) and frame_width > 0 and frame_height > 0:
        if frame.shape[1] > frame_width or frame.shape[0] > frame_height:
            frame = cv2.resize(frame, (int(frame_width), int(frame_height)), interpolation=cv2.INTER_AREA)
    # 2) encode to bytes
    img_bin = encode_image_to_bytes(frame, format=format)
    cf_bin = encode_image_to_bytes(face_crop, format=format)
    return img_bin, cf_bin


def crop_face_from_frame(
    frame: np.ndarray,
    bbox,
    max_cropped_face_resolution: int,
    margin: float = 0.1,
) -> np.ndarray:
    """
    Crop face region from frame with optional margin expansion.

    Args:
        frame: Input image (H, W, 3)
        bbox: Bounding box in xyxy or compatible format
        max_cropped_face_resolution: Resize long side to this size (if given)
        margin: Fractional margin to expand width/height (e.g. 0.25 = 25%)

    Returns:
        Cropped (and possibly resized) face image.
    """
    if frame is None or frame.size == 0:
        raise ValueError("crop_face_from_frame: empty frame")

    H, W = frame.shape[:2]
    x1, y1, x2, y2 = _bbox_to_xyxy(bbox)
    x1, y1, x2, y2 = _clamp_xyxy(x1, y1, x2, y2, W, H)

    # --- Add margin ---
    if margin and margin > 0:
        w = x2 - x1
        h = y2 - y1
        dw = int(w * margin)
        dh = int(h * margin)
        # Expand while keeping within bounds
        x1 = max(0, x1 - dw)
        y1 = max(0, y1 - dh)
        x2 = min(W, x2 + dw)
        y2 = min(H, y2 + dh)

    cf = frame[y1:y2, x1:x2]
    if cf is None or cf.size == 0:
        cf = np.zeros((1, 1, 3), dtype=np.uint8)

    if max_cropped_face_resolution is not None:
        cf = _resize_long_side(cf, int(max_cropped_face_resolution))
    return cf
