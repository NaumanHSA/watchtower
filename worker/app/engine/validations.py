from __future__ import annotations
from typing import Any, Dict, Optional, Tuple, Union
import logging
import os
from datetime import datetime
from urllib.parse import urlparse

from config import config


Number = Union[int, float]


# ---------------------------
# Core coercion helpers
# ---------------------------

def _coerce_float(v: Any, default: float, logger: logging.Logger, name: str) -> float:
    try:
        return float(v)
    except Exception:
        logger.warning(f"[validate] {name}: expected float, got {type(v).__name__}. Using default={default}")
        return float(default)

def _coerce_int(v: Any, default: int, logger: logging.Logger, name: str) -> int:
    try:
        # accept strings like "30" or "30.0"
        return int(float(v))
    except Exception:
        logger.warning(f"[validate] {name}: expected int, got {type(v).__name__}. Using default={default}")
        return int(default)

def _clamp(v: Number, lo: Number, hi: Number) -> Number:
    return max(lo, min(hi, v))


# ---------------------------
# Field-level validators
# ---------------------------

def validate_frame_to_return(frame_to_return: Any, logger: logging.Logger) -> str:
    """
    Normalize frame flag to one of {'first','best','last'}.
    Accept case-insensitive input; default to 'best'.
    """
    options = {"first", "best", "last"}
    if isinstance(frame_to_return, str):
        ft = frame_to_return.strip().lower()
    else:
        ft = ""
    if ft not in options:
        logger.warning(f"param frame_to_return should be one of {sorted(options)}. Using default 'best'.")
        ft = "best"
    return ft

def validate_face_detection_confidence(conf: Any, logger: logging.Logger) -> float:
    """
    Coerce to float and clamp to [0,1]. If non-numeric -> config.FACE_DETECTION_CONFIDENCE.
    """
    c = _coerce_float(conf, default=config.FACE_DETECTION_CONFIDENCE, logger=logger, name="face_detection_confidence")
    if c < 0.0 or c > 1.0:
        old = c
        c = _clamp(c, 0.0, 1.0)
        logger.warning(f"param face_detection_confidence out of range [0,1] (got {old}). Clamped to {c}.")
    return c

def validate_results_interval_ms(v: Any, logger: logging.Logger) -> int:
    """
    Must be >= 100 ms (too low will thrash your network). Recommend 500–2000.
    """
    ms = _coerce_int(v, default=1000, logger=logger, name="results_interval")
    if ms < 100:
        logger.warning(f"param results_interval too low ({ms}ms). Using minimum 100ms.")
        ms = 100
    return ms

def validate_streaming_fps(v: Any, logger: logging.Logger) -> Optional[int]:
    """
    None or int in [1, 240]. If 0/negative, return None (let camera decide).
    """
    if v is None:
        return None
    fps = _coerce_int(v, default=0, logger=logger, name="streaming_fps")
    if fps <= 0:
        return None
    if fps > 240:
        logger.warning(f"param streaming_fps unusually high ({fps}); clamped to 240.")
        fps = 240
    return fps

def validate_reconnect_interval_seconds(v: Any, logger: logging.Logger) -> int:
    s = _coerce_int(v, default=5, logger=logger, name="camera_reconnect_interval")
    if s < 1:
        logger.warning(f"param camera_reconnect_interval too low ({s}s). Using minimum 1s.")
        s = 1
    if s > 3600:
        logger.warning(f"param camera_reconnect_interval too high ({s}s). Clamped to 3600s.")
        s = 3600
    return s

def validate_reconnect_attempts(v: Any, logger: logging.Logger) -> int:
    n = _coerce_int(v, default=5, logger=logger, name="camera_reconnect_attempts")
    if n < 1:
        logger.warning(f"param camera_reconnect_attempts must be >=1 (got {n}). Using 1.")
        n = 1
    if n > 1000:
        logger.warning(f"param camera_reconnect_attempts too large ({n}). Clamped to 1000.")
        n = 1000
    return n

def validate_max_allowed_detections(v: Any, logger: logging.Logger) -> int:
    n = _coerce_int(v, default=50, logger=logger, name="max_allowed_detections")
    if n < 1:
        logger.warning(f"param max_allowed_detections must be >=1 (got {n}). Using 1.")
        n = 1
    if n > 1000:
        logger.warning(f"param max_allowed_detections very high ({n}). Clamped to 1000 to protect perf.")
        n = 1000
    return n

def validate_max_frame_resolution(px: Any, logger: logging.Logger) -> Optional[int]:
    """
    None or int >= 160. If <160, set to 160 (guard against extreme downsizing).
    """
    if px is None:
        return None
    v = _coerce_int(px, default=None if config.MAX_FRAME_RESOLUTION is None else config.MAX_FRAME_RESOLUTION,
                    logger=logger, name="max_frame_resolution")
    if v is None:
        return None
    if v < 160:
        logger.warning(f"param max_frame_resolution too low ({v}px). Using minimum 160px.")
        v = 160
    if v > 16384:
        logger.warning(f"param max_frame_resolution too high ({v}px). Clamped to 16384px.")
        v = 16384
    return v

def validate_face_detector_resolution(px: Any, logger: logging.Logger) -> int:
    v = _coerce_int(px, default=320, logger=logger, name="face_detector_resolution")
    if v < 80:
        logger.warning(f"param face_detector_resolution too low ({v}). Using 80.")
        v = 80
    if v > 4096:
        logger.warning(f"param face_detector_resolution too high ({v}). Clamped to 4096.")
        v = 4096
    return v

def validate_broadcast_url(url: Any, logger: logging.Logger) -> Optional[str]:
    """
    Optional. If provided, must be http/https.
    """
    if url in (None, "", False):
        return None
    if not isinstance(url, str):
        logger.warning("param broadcast_url must be a string; ignoring.")
        return None
    u = url.strip()
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.warning(f"param broadcast_url must be a valid http(s) URL (got '{url}'); ignoring.")
        return None
    return u

def validate_stream_url(url: Union[str, int], logger: logging.Logger) -> Union[str, int]:
    """
    Required. Accepts schemes like rtsp/http/https/file etc. Only checks presence.
    """
    if isinstance(url, int):
        return url
    if isinstance(url, str) and not url.strip():
        raise ValueError("stream_url is required and must be a non-empty string")
    return url.strip()

def validate_cropped_face_margin(margin: Any, logger: logging.Logger) -> float:
    v = _coerce_float(margin, default=0.1, logger=logger, name="cropped_face_margin")
    if v < 0.0 or v > 1.0:
        logger.warning(f"param cropped_face_margin out of range [0,1] (got {v}). Clamped to 0.1.")
        v = 0.1
    return v

def validate_date_format(fmt: Any, default: str, logger: logging.Logger) -> str:
    """
    Defensive: ensure it formats 'now'. If invalid, fallback to default.
    """
    if not isinstance(fmt, str) or not fmt:
        return default
    try:
        _ = datetime.now().strftime(fmt)  # type: ignore[name-defined]
        return fmt
    except Exception:
        logger.warning(f"param date_format invalid ({fmt!r}). Using default {default!r}.")
        return default


# ---------------------------
# Aggregate args validator
# ---------------------------

def validate_engine_args(args: Dict[str, Any], logger: logging.Logger) -> Dict[str, Any]:
    """
    Validate & normalize the incoming args dict.
    Falls back to sensible defaults from config where appropriate.
    """
    out = dict(args)  # shallow copy

    # Required
    out["stream_url"] = validate_stream_url(out.get("stream_url"), logger)

    # Optionals / numerics
    out["broadcast_url"] = validate_broadcast_url(out.get("broadcast_url"), logger)
    out["results_interval"] = validate_results_interval_ms(out.get("results_interval", 1000), logger)
    out["streaming_fps"] = validate_streaming_fps(out.get("streaming_fps", None), logger)
    out["camera_reconnect_interval"] = validate_reconnect_interval_seconds(
        out.get("camera_reconnect_interval", 5), logger
    )
    out["camera_reconnect_attempts"] = validate_reconnect_attempts(
        out.get("camera_reconnect_attempts", 5), logger
    )
    out["face_detection_confidence"] = validate_face_detection_confidence(
        out.get("face_detection_confidence", config.FACE_DETECTION_CONFIDENCE), logger
    )
    out["max_allowed_detections"] = validate_max_allowed_detections(
        out.get("max_allowed_detections", 50), logger
    )
    out["frame_to_return"] = validate_frame_to_return(out.get("frame_to_return", "best"), logger)
    out["max_frame_resolution"] = validate_max_frame_resolution(
        out.get("max_frame_resolution", config.MAX_FRAME_RESOLUTION if hasattr(config, "MAX_FRAME_RESOLUTION") else None),
        logger
    )
    out["face_detector_resolution"] = validate_face_detector_resolution(
        out.get("face_detector_resolution", 320), logger
    )
    out["cropped_face_margin"] = validate_cropped_face_margin(
        out.get("cropped_face_margin", 0.1), logger
    )

    # Optional: allow override of date_format but keep your default if missing/invalid
    default_df = "%Y-%m-%dT%H:%M:%S.%fZ"
    out["date_format"] = validate_date_format(out.get("date_format", default_df), default_df, logger)

    return out


# ---------------------------
# Environment/Dependency checks
# ---------------------------
def warn_config_consistency(logger: logging.Logger) -> None:
    """
    Sanity-checks config values that can silently degrade perf.
    """
    try:
        if getattr(config, "YUNET_CHOICE", "onnx") not in {"cv", "onnx"}:
            logger.warning("config.YUNET_CHOICE should be 'cv' or 'onnx'. Defaulting to 'onnx' at runtime.")

        # Check model path presence (non-fatal)
        mp = getattr(config, "YUNET_MODEL_PATH", None)
        if mp and not os.path.exists(mp):
            logger.warning(f"YuNet model path not found: {mp}. Ensure the model is present or adjust config.YUNET_MODEL_PATH.")

        # Friendly nms / thresholds guardrails (if present)
        nms = getattr(config, "YUNET_NMS_THRESHOLD", None)
        if isinstance(nms, (int, float)) and not (0.0 < float(nms) <= 1.0):
            logger.warning(f"YUNET_NMS_THRESHOLD should be in (0,1]; got {nms}.")

        # DeepSORT patience/age sanity
        n_init = getattr(config, "DEEPSORT_N_INIT", None)
        max_age = getattr(config, "DEEPSORT_MAX_AGE", None)
        if isinstance(n_init, int) and isinstance(max_age, int) and n_init > max_age:
            logger.warning(f"DEEPSORT_N_INIT ({n_init}) > DEEPSORT_MAX_AGE ({max_age}). Consider lowering N_INIT or raising MAX_AGE.")
    except Exception:
        # Never let validation crash your app
        logger.debug("warn_config_consistency: skipped due to an internal error.", exc_info=True)
