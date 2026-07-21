from __future__ import annotations
from typing import Any, Dict, Optional
from collections import deque
import json
import os
import hashlib
from datetime import datetime

import numpy as np
import cv2
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, Field, ValidationError
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])
_LAST_EVENTS: deque[Dict[str, Any]] = deque(maxlen=50)


class DetectorParamsModel(BaseModel):
    cam_id: str
    end_of_track: bool
    liveness_score: Optional[float] = None
    quality: float
    track_duration_seconds: float
    track: Dict[str, Any] = Field(...)


def _sha1_of_bytes(blob: bytes) -> str:
    h = hashlib.sha1()
    h.update(blob)
    return h.hexdigest()


def _bbox_to_both(bbox_val: Any) -> tuple[dict, list]:
    """
    Return (xywh_dict, xyxy_list) with floats coerced.
    Accepts:
      - [{"x":..,"y":..,"w":..,"h":..}]  (your older sender)
      - [[x1,y1,x2,y2]]                  (current sender)
    """
    try:
        if not isinstance(bbox_val, list) or not bbox_val:
            raise ValueError("bbox must be a non-empty JSON array")
        bb = bbox_val[0]
        if isinstance(bb, dict):
            x = float(bb["x"])
            y = float(bb["y"])
            w = float(bb["w"])
            h = float(bb["h"])
            xywh = {"x": x, "y": y, "w": w, "h": h}
            xyxy = [x, y, x + w, y + h]
        elif isinstance(bb, (list, tuple)) and len(bb) == 4:
            x1, y1, x2, y2 = [float(v) for v in bb]
            xyxy = [x1, y1, x2, y2]
            xywh = {"x": x1, "y": y1, "w": (x2 - x1), "h": (y2 - y1)}
        else:
            raise ValueError(
                "bbox element must be dict{x,y,w,h} or list[x1,y1,x2,y2]")
        return xywh, xyxy
    except KeyError as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid bbox: missing key {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid bbox: {e}")


def _im_dims(data: bytes) -> tuple[int, int]:
    """Return (h, w) or (0,0) if decode fails."""
    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return (0, 0)
        h, w = img.shape[:2]
        return (int(h), int(w))
    except Exception:
        return (0, 0)


@router.post("/internal/detection")
async def ingest_detection(
    bbox: str = Form(..., description="JSON array of one bbox (xywh or xyxy)"),
    cam_id: str = Form(...),
    timestamp: str = Form(...),
    detectorParams: str = Form(...),
    bs_type: str = Form(...),
    photo: UploadFile = File(...),
    normalized: UploadFile = File(...),
):
    """
    Receives detection results as broadcast by the engine; validates core fields and
    returns a concise summary including image sizes/hashes for verification.
    """

    # Parse bbox (supports both formats)
    try:
        bbox_val = json.loads(bbox)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid bbox JSON: {e}")
    bbox_xywh, bbox_xyxy = _bbox_to_both(bbox_val)

    # Parse detectorParams
    try:
        dp_dict = json.loads(detectorParams)
        dp = DetectorParamsModel.model_validate(dp_dict)
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid detectorParams: {e}")

    # cam_id consistency (soft-fail previously; here we enforce for correctness)
    if str(dp.cam_id) != str(cam_id):
        raise HTTPException(
            status_code=400, detail="cam_id mismatch between form and detectorParams")

    # Prefer explicit timestamp; fallback to detectorParams.track.last_timestamp
    ts = timestamp
    if not ts:
        ts = str(dp.track.get("last_timestamp", ""))

    # Figure out which snapshot the engine most likely used (best/last/first)
    face = (dp.track.get("face") if isinstance(dp.track, dict) else None) or {}
    snap_used = None
    for k in ("best", "last", "first"):
        if isinstance(face.get(k), dict):
            snap_used = k
            break
    snapshot_note = "No face snapshot found at face.best/last/first" if not snap_used else f"snapshot={snap_used}"

    # Read image bytes and compute quick integrity info
    photo_bytes = await photo.read()
    norm_bytes = await normalized.read()
    photo_sum = _sha1_of_bytes(photo_bytes)
    norm_sum = _sha1_of_bytes(norm_bytes)
    photo_h, photo_w = _im_dims(photo_bytes)
    norm_h, norm_w = _im_dims(norm_bytes)

    # # save photo
    # with open("/tmp/ingest_photo.jpg", "wb") as f:
    #     f.write(photo_bytes)

    # Optional persist for manual inspection
    if os.getenv("INGEST_SAVE_IMAGES", "0") == "1":
        safe_cam = "".join(c for c in cam_id if c.isalnum() or c in "-_")
        ts_safe = (ts or datetime.utcnow().strftime(
            "%Y%m%dT%H%M%S%fZ")).replace(":", "").replace(".", "")
        base = f"/tmp/ingest_{safe_cam}_{ts_safe}"
        try:
            with open(base + "_photo.jpg", "wb") as f:
                f.write(photo_bytes)
            with open(base + "_normalized.jpg", "wb") as f:
                f.write(norm_bytes)
        except Exception:
            # don’t fail ingestion if disk write fails
            pass

    # Build response summary
    event_summary = {
        "ok": True,
        "received": {
            "cam_id": cam_id,
            "timestamp": ts,
            "bs_type": bs_type,
            "bbox": {
                "xywh": bbox_xywh,
                "xyxy": bbox_xyxy,
                "area": float(max(0.0, bbox_xywh["w"]) * max(0.0, bbox_xywh["h"])),
            },
            "detectorParams_core": {
                "end_of_track": dp.end_of_track,
                "quality": dp.quality,
                "track_duration_seconds": dp.track_duration_seconds,
            },
            "snapshot_note": snapshot_note,
            "photo": {
                "filename": photo.filename,
                "content_type": photo.content_type,
                "size_bytes": len(photo_bytes),
                "sha1": photo_sum,
                "dims": {"h": photo_h, "w": photo_w},
            },
            "normalized": {
                "filename": normalized.filename,
                "content_type": normalized.content_type,
                "size_bytes": len(norm_bytes),
                "sha1": norm_sum,
                "dims": {"h": norm_h, "w": norm_w},
            },
        },
    }

    # logger.info(f"Ingested detection: {json.dumps(event_summary, indent=4)}")
    _LAST_EVENTS.appendleft(event_summary)
    return event_summary


@router.get("/detection/last")
def last_events(limit: int = 10):
    limit = max(1, min(50, int(limit)))
    return list(list(_LAST_EVENTS)[0:limit])


# {
#     'ok': True, 
#     'received': {
#         'cam_id': 'e32f80d2-ed9d-4336-8023-e176904ffa2e', 
#         'timestamp': '2025-10-02T12:41:46.199594Z', 
#         'bs_type': 'overall', 
#         'bbox': {
#             'xywh': {
#                 'x': 314.0, 
#                 'y': 121.0, 
#                 'w': 326.0, 
#                 'h': 287.0
#             }, 
#             'xyxy': [314.0, 121.0, 640.0, 408.0], 
#             'area': 93562.0
#         }, 
#         'detectorParams_core': {
#             'end_of_track': False, 
#             'quality': 0.9989, 
#             'track_duration_seconds': 0.1317
#         },
#         'snapshot_note': 'snapshot=best', 
#         'photo': {
#             'filename': 'photo.jpg', 
#             'content_type': 'image/jpeg', 
#             'size_bytes': 77469, 
#             'sha1': '84a99269be31d99194fe8b8fa1ce510f5f25e3ed', 
#             'dims': {
#                 'h': 480, 
#                 'w': 640
#             }
#         },
#         'normalized': {
#             'filename': 'normalized.jpg', 
#             'content_type': 'image/jpeg', 
#             'size_bytes': 11268, 
#             'sha1': 'ddbfa9024c567526c41d5cdf00901266a460cbd1', 
#             'dims': {
#                 'h': 197, 
#                 'w': 224
#             }
#         }
#     }
# }
