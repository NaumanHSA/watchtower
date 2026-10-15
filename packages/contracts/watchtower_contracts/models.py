"""Wire models shared by the controller, workers, token servers and result receivers.

Every model ignores unknown fields and every addition must be optional, so peers on
different versions keep working. Incompatible changes bump PROTOCOL_VERSION.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROTOCOL_VERSION = 1

# Stream and worker ids end up in URLs and MediaMTX path names.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"

# Input sizes the bundled YuNet post-processing models exist for (4:3 aspect).
DETECTOR_RESOLUTIONS = (320, 416, 512, 640)


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    WORKER = "worker"  # may only enroll workers; no dashboard access


class WorkerState(StrEnum):
    ONLINE = "online"
    SUSPECT = "suspect"  # missed a few syncs; streams stay put
    OFFLINE = "offline"  # lease expired; streams were released


class DesiredState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class ObservedStatus(StrEnum):
    """What a worker reports about one of its streams."""

    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    ERROR = "error"  # the worker gave up; the controller may move the stream


class StreamStatus(StrEnum):
    """Status the controller shows for a stream."""

    PENDING = "pending"  # wants to run, not placed on a worker yet
    STARTING = "starting"  # placed, worker hasn't reported it running
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    STOPPED = "stopped"
    FAILED = "failed"  # gave up after max attempts; needs a manual start


class DeliveryMode(StrEnum):
    REALTIME = "realtime"  # never late: drops stale results when the receiver is slow
    RELIABLE = "reliable"  # never drops (within spool limits): may deliver late


class Snapshot(StrEnum):
    FIRST = "first"
    BEST = "best"
    LAST = "last"


def _http_url_or_none(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an http(s) URL")
    return value


class StreamConfig(_Model):
    """Per-stream processing settings. Defaults suit a typical 720p-1080p camera."""

    fps: int | None = Field(default=8, ge=1, le=60, description="Frames analysed per second; None = camera rate")
    detector_resolution: Literal[320, 416, 512, 640] = 320
    detection_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    max_faces: int = Field(default=20, ge=1, le=20, description="Per frame; the ONNX post-processor caps at 20")
    max_frame_width: int | None = Field(default=1280, ge=160, le=7680, description="Downscale wider frames")
    face_crop_size: int = Field(default=224, ge=64, le=1024, description="Long side of the face crop, px")
    face_crop_margin: float = Field(default=0.1, ge=0.0, le=1.0)
    include_frame: bool = Field(default=True, description="Send the full frame with each result")
    snapshot: Snapshot = Snapshot.BEST
    results_interval_ms: int = Field(default=1000, ge=100, le=120_000, description="Min gap between results per face")
    track_confirm_frames: int = Field(default=5, ge=1, le=100, description="Frames before a face counts as a track")
    track_max_missed_frames: int = Field(default=8, ge=1, le=300, description="Missed frames before a track ends")
    reconnect_interval_s: int = Field(default=5, ge=1, le=3600)
    reconnect_attempts: int = Field(default=5, ge=1, le=1000)
    delivery_mode: DeliveryMode | None = Field(default=None, description="None: the worker's default")
    results_url: str | None = Field(default=None, description="None: the controller's or worker's default")

    _check_results_url = field_validator("results_url")(_http_url_or_none)


class Capacity(_Model):
    max_streams: int = Field(default=2, ge=0, le=1000)


class WorkerLoad(_Model):
    cpu_percent: float | None = None
    memory_percent: float | None = None


class DeliveryStats(_Model):
    sent: int = 0
    dropped: int = 0
    failed: int = 0
    spooled: int = 0
    spool_bytes: int = 0


# ----------------------------------------------------------------- worker protocol

class RegisterRequest(_Model):
    worker_id: str | None = Field(default=None, pattern=ID_PATTERN, description="Reuse an existing identity")
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default="unknown", max_length=64)
    protocol: int = PROTOCOL_VERSION
    capacity: Capacity = Field(default_factory=Capacity)
    webrtc_base_url: str | None = Field(default=None, description="Public base URL of this worker's WebRTC server")
    host_info: dict[str, Any] = Field(default_factory=dict)

    _check_webrtc = field_validator("webrtc_base_url")(_http_url_or_none)


class RegisterResponse(_Model):
    worker_id: str
    worker_secret: str
    sync_interval_ms: int
    lease_seconds: int


class ObservedStream(_Model):
    stream_id: str
    generation: int
    status: ObservedStatus
    fps: float | None = None
    error: str | None = None
    restarts: int = 0


class SyncRequest(_Model):
    version: str = "unknown"
    capacity: Capacity = Field(default_factory=Capacity)
    load: WorkerLoad = Field(default_factory=WorkerLoad)
    draining: bool = False
    streams: list[ObservedStream] = Field(default_factory=list)
    delivery: DeliveryStats | None = None


class ResultKey(_Model):
    kid: str | None = None
    public_key_pem: str


class DesiredStream(_Model):
    stream_id: str
    generation: int
    name: str
    source_url: str
    config: StreamConfig = Field(default_factory=StreamConfig)


class SyncResponse(_Model):
    desired: list[DesiredStream] = Field(default_factory=list)
    result_key: ResultKey | None = None
    results_url: str | None = None
    sync_interval_ms: int
    lease_seconds: int


# ----------------------------------------------------------------- token servers

class TokenVerification(_Model):
    """Body returned by a token server's verify endpoint. See docs/token-server.md.

    Field names match the original Watchtower platform (`isSuccess`, `pub_cov`), so
    existing token servers work unchanged; `role`, `subject` and `kid` are optional
    additions.
    """

    isSuccess: bool  # noqa: N815 - wire name
    role: str | None = None
    subject: str | None = None
    pub_cov: str | None = Field(default=None, description="PEM public key results must be encrypted with")
    kid: str | None = None
    message: str | None = None


# ----------------------------------------------------------------- results

class DetectionBBox(_Model):
    xywh: dict[str, int]
    xyxy: list[int]
    area: int


class DetectionCore(_Model):
    endOfTrack: bool  # noqa: N815
    quality: float
    trackDurationSeconds: float  # noqa: N815


class DetectionEvent(_Model):
    """One face result, as POSTed to a results URL inside {"ok": true, "received": ...}.

    camId/bsType/bbox/detectorParamsCore/snapshotNote/photo/normalized keep the
    original Watchtower field names; the rest were added in protocol 1.
    """

    eventId: str  # noqa: N815
    camId: str  # noqa: N815 - same value as streamId
    streamId: str  # noqa: N815
    streamName: str | None = None  # noqa: N815
    workerId: str  # noqa: N815
    generation: int
    trackId: str  # noqa: N815
    timestamp: str  # snapshot time, ISO 8601 UTC
    capturedAt: str  # noqa: N815 - emission time, ISO 8601 UTC
    bsType: str = "overall"  # noqa: N815
    bbox: DetectionBBox
    detectorParamsCore: DetectionCore  # noqa: N815
    snapshotNote: str  # noqa: N815
    photo: str | None = Field(default=None, description="Base64 JPEG of the full frame")
    normalized: str = Field(description="Base64 JPEG of the face crop")
