"""Dashboard API models and conversions from MongoDB documents."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator

from watchtower_contracts import (
    Capacity,
    DeliveryStats,
    DesiredState,
    StreamConfig,
    StreamStatus,
    WorkerLoad,
    WorkerState,
)

ALLOWED_SOURCE_SCHEMES = ("rtsp", "rtsps", "rtmp", "rtmps", "http", "https", "srt")


def mask_url(url: str) -> str:
    """Hide the password in user:pass@host URLs."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit(parts._replace(netloc=f"{parts.username}:***@{host}"))


def _check_source_url(value: str) -> str:
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in ALLOWED_SOURCE_SCHEMES or not parts.hostname:
        raise ValueError(f"source_url must be a {', '.join(ALLOWED_SOURCE_SCHEMES)} URL with a host")
    return value


def _check_metadata(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Metadata is stored as-is in MongoDB, where keys may not start with '$' or contain '.'."""
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key.startswith("$") or "." in key:
                    raise ValueError(f"metadata keys may not start with '$' or contain '.': {key!r}")
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return value


# ---------------------------------------------------------------- streams

class StreamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_url: str = Field(max_length=2048)
    location: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)
    config: StreamConfig = Field(default_factory=StreamConfig)
    start: bool = True

    _check_source = field_validator("source_url")(_check_source_url)
    _check_meta = field_validator("metadata")(_check_metadata)


class StreamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    location: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] | None = None
    config: dict[str, Any] | None = Field(default=None, description="Partial: only the keys given change")

    @field_validator("source_url")
    @classmethod
    def _source(cls, value: str | None) -> str | None:
        return None if value is None else _check_source_url(value)

    _check_meta = field_validator("metadata")(_check_metadata)


class ObservedOut(BaseModel):
    status: str
    fps: float | None = None
    error: str | None = None
    restarts: int = 0
    at: datetime | None = None


class StreamOut(BaseModel):
    stream_id: str
    name: str
    location: str | None
    source_url: str = Field(description="Password masked")
    metadata: dict[str, Any]
    config: StreamConfig
    desired_state: DesiredState
    status: StreamStatus
    worker_id: str | None
    generation: int
    observed: ObservedOut | None
    attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> StreamOut:
        return cls(
            stream_id=doc["stream_id"],
            name=doc["name"],
            location=doc.get("location"),
            source_url=mask_url(doc["source_url"]),
            metadata=doc.get("metadata") or {},
            config=StreamConfig.model_validate(doc.get("config") or {}),
            desired_state=doc["desired_state"],
            status=doc["status"],
            worker_id=doc.get("worker_id"),
            generation=doc.get("generation", 0),
            observed=ObservedOut.model_validate(doc["observed"]) if doc.get("observed") else None,
            attempts=doc.get("attempts", 0),
            last_error=doc.get("last_error"),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )


class StreamView(BaseModel):
    stream_id: str
    status: StreamStatus
    webrtc_url: str | None = Field(description="MediaMTX player page; append ?token=<viewer token>")
    whep_url: str | None


# ---------------------------------------------------------------- workers

class WorkerOut(BaseModel):
    worker_id: str
    name: str
    state: WorkerState
    draining: bool
    version: str
    capacity: Capacity
    assigned_streams: int
    load: WorkerLoad
    delivery: DeliveryStats | None
    webrtc_base_url: str | None
    host_info: dict[str, Any]
    last_seen: datetime
    registered_at: datetime

    @classmethod
    def from_doc(cls, doc: dict[str, Any], assigned: int) -> WorkerOut:
        return cls(
            worker_id=doc["worker_id"],
            name=doc.get("name") or doc["worker_id"],
            state=doc.get("state", WorkerState.OFFLINE),
            draining=bool(doc.get("draining") or doc.get("draining_reported")),
            version=doc.get("version", "unknown"),
            capacity=Capacity.model_validate(doc.get("capacity") or {}),
            assigned_streams=assigned,
            load=WorkerLoad.model_validate(doc.get("load") or {}),
            delivery=DeliveryStats.model_validate(doc["delivery"]) if doc.get("delivery") else None,
            webrtc_base_url=doc.get("webrtc_base_url"),
            host_info=doc.get("host_info") or {},
            last_seen=doc["last_seen"],
            registered_at=doc["registered_at"],
        )


class DrainRequest(BaseModel):
    draining: bool = True


class Stats(BaseModel):
    workers: dict[str, int]
    streams: dict[str, int]
    capacity: dict[str, int]
