from __future__ import annotations
from typing import Optional, Any, Union
from pydantic import BaseModel, Field, field_validator, ConfigDict
from urllib.parse import urlparse
from config import Config, config
from ..db.models import Stream

_MIN_RESULTS_INTERVAL_MS = 100
_MAX_RESULTS_INTERVAL_MS = 120_000

class EngineArgs(BaseModel):
    """
    Typed + forgiving inputs for Engine. Safe to pickle for multiprocessing.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True)

    # Required
    stream_url: Union[str, int] = Field(..., description="RTSP/HTTP/FILE or similar source URL")

    # Optional / runtime-tolerant
    broadcast_url: Optional[str] = Field(None, description="Where to POST results; http/https or None")
    results_interval: int = Field(2000, ge=_MIN_RESULTS_INTERVAL_MS, le=_MAX_RESULTS_INTERVAL_MS, description="Emit interval in ms; min 100ms")
    face_detection_confidence: float = Field(0.85, ge=0.0, le=1.0, description="YuNet confidence threshold [0,1]")
    face_detector_resolution: int = Field(640, ge=80, le=4096, description="Input width to detector")
    max_allowed_detections: int = Field(50, ge=1, le=1000, description="Top-K detections per frame")
    max_frame_resolution: Optional[int] = Field(None, ge=160, le=16384, description="If set, clamp frame width to this")
    max_cropped_face_resolution: Optional[int] = Field(512, ge=64, le=4096, description="Longest side of cropped face")
    frame_to_return: str = Field("best", description="Which snapshot to attach: first|best|last")
    streaming_fps: Optional[int] = Field(None, ge=1, le=240, description="If None, use camera fps")
    camera_reconnect_interval: int = Field(5, ge=1, le=3600, description="Seconds between reconnect attempts")
    camera_reconnect_attempts: int = Field(5, ge=1, le=1000, description="Max reconnect attempts before stop")
    cropped_face_margin: float = Field(0.1, ge=0.0, le=1.0, description="Margin around face in cropped face")
    date_format: str = Field("%Y-%m-%dT%H:%M:%S.%fZ")

    # --------- Validators (forgiving) ---------
    @field_validator("stream_url")
    @classmethod
    def _non_empty_stream_url(cls, v: Union[str, int]) -> Union[str, int]:
        if isinstance(v, int):
            return v
        if isinstance(v, str) and not v.strip():
            raise ValueError("stream_url must be a non-empty string")
        return v.strip()

    @field_validator("broadcast_url", mode="before")
    @classmethod
    def _coerce_http_url_or_none(cls, v: Any):
        # Accept None, "", "None", False as None
        if v in (None, "", False) or (isinstance(v, str) and v.strip().lower() == "none"):
            return None
        if isinstance(v, str):
            u = v.strip()
            p = urlparse(u)
            if p.scheme in {"http", "https"} and p.netloc:
                return u
            raise ValueError("broadcast_url must be http(s)")
        return v

    @field_validator("results_interval", mode="before")
    @classmethod
    def _coerce_results_interval_ms(cls, v: Any):
        # Accept floats/strings; coerce to int ms and clamp
        try:
            iv = int(float(v))
        except Exception:
            iv = 1000
        if iv < _MIN_RESULTS_INTERVAL_MS:
            iv = _MIN_RESULTS_INTERVAL_MS
        if iv > _MAX_RESULTS_INTERVAL_MS:
            iv = _MAX_RESULTS_INTERVAL_MS
        return iv

    @field_validator("frame_to_return", mode="before")
    @classmethod
    def _normalize_flag(cls, v: Any) -> str:
        if not isinstance(v, str):
            return "best"
        s = v.strip().lower()
        return s if s in {"first", "best", "last"} else "best"

    @field_validator("date_format")
    @classmethod
    def _validate_datefmt(cls, v: str) -> str:
        import datetime as _dt
        try:
            _dt.datetime.now().strftime(v)
            return v
        except Exception:
            return "%Y-%m-%dT%H:%M:%S.%fZ"

    # Helper to build from a DB row
    @classmethod
    def from_stream_row(cls, row: Stream, config: Config) -> "EngineArgs":
        return cls(
            stream_url=row.source_url,
            broadcast_url=getattr(config, "BROADCAST_URL", None),
            results_interval=row.results_interval,
            face_detection_confidence=row.face_detection_confidence,
            face_detector_resolution=row.face_detector_resolution,
            max_allowed_detections=row.max_allowed_detections,
            max_frame_resolution=row.max_frame_resolution,
            max_cropped_face_resolution=row.max_cropped_face_resolution,
            frame_to_return=row.frame_to_return,
            streaming_fps=row.streaming_fps,
            camera_reconnect_interval=row.camera_reconnect_interval,
            camera_reconnect_attempts=row.camera_reconnect_attempts,
            date_format=getattr(config, "DATE_FORMAT", "%Y-%m-%dT%H:%M:%S.%fZ"),
        )
