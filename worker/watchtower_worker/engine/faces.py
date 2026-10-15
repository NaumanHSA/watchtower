"""Per-face state between detections, and the decision of when to emit a result."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from watchtower_contracts import Snapshot, StreamConfig

from .images import crop_face, jpeg_b64
from .tracker import Track


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Shot:
    timestamp: str
    bbox: list[float]
    confidence: float
    frame: np.ndarray | None  # kept only on the shot the snapshot policy sends (one frame per face)


@dataclass
class Face:
    track_id: int
    started: float  # monotonic
    first: Shot
    best: Shot
    last: Shot
    confidence_sum: float = 0.0
    detections: int = 0
    last_emit: float | None = None  # monotonic

    @classmethod
    def start(cls, track: Track, frame: np.ndarray, timestamp: str, policy: Snapshot, now: float) -> Face:
        def shot(kind: Snapshot) -> Shot:
            return Shot(timestamp, track.bbox, track.confidence, frame if policy == kind else None)

        face = cls(track.track_id, now, shot(Snapshot.FIRST), shot(Snapshot.BEST), shot(Snapshot.LAST))
        face.confidence_sum, face.detections = track.confidence, 1
        return face

    def observe(self, track: Track, frame: np.ndarray, timestamp: str, policy: Snapshot) -> None:
        # Frames are fresh arrays per capture, so keeping a reference (not a copy) is safe.
        self.last = Shot(timestamp, track.bbox, track.confidence, frame if policy == Snapshot.LAST else None)
        if track.confidence > self.best.confidence:
            self.best = Shot(timestamp, track.bbox, track.confidence, frame if policy == Snapshot.BEST else None)
        self.confidence_sum += track.confidence
        self.detections += 1

    @property
    def quality(self) -> float:
        return round(self.confidence_sum / self.detections, 4) if self.detections else 0.0


@dataclass
class Emission:
    face: Face
    end_of_track: bool


@dataclass
class FaceBook:
    config: StreamConfig
    faces: dict[int, Face] = field(default_factory=dict)

    def update(self, visible: list[Track], ended: list[int], frame: np.ndarray) -> list[Emission]:
        """Feed one analysed frame; returns the results due now."""
        now = time.monotonic()
        ts = iso_now()
        policy = self.config.snapshot
        for track in visible:
            face = self.faces.get(track.track_id)
            if face is None:
                self.faces[track.track_id] = Face.start(track, frame, ts, policy, now)
            else:
                face.observe(track, frame, ts, policy)

        interval = self.config.results_interval_ms / 1000
        due = [
            Emission(self.faces[t.track_id], end_of_track=False)
            for t in visible
            if self.faces[t.track_id].last_emit is None or now - self.faces[t.track_id].last_emit >= interval
        ]
        for emission in due:
            emission.face.last_emit = now
        for track_id in ended:
            face = self.faces.pop(track_id, None)
            if face is not None:
                due.append(Emission(face, end_of_track=True))
        return due

    def build_event(self, emission: Emission, *, stream_id: str, stream_name: str, worker_id: str,
                    generation: int) -> dict[str, Any]:
        face = emission.face
        cfg = self.config
        shot = {Snapshot.FIRST: face.first, Snapshot.BEST: face.best, Snapshot.LAST: face.last}[cfg.snapshot]
        x1, y1, x2, y2 = (int(v) for v in shot.bbox)
        crop = crop_face(shot.frame, shot.bbox, cfg.face_crop_size, cfg.face_crop_margin)
        return {
            "eventId": uuid.uuid4().hex,
            "camId": stream_id,
            "streamId": stream_id,
            "streamName": stream_name,
            "workerId": worker_id,
            "generation": generation,
            "trackId": str(face.track_id),
            "timestamp": shot.timestamp,
            "capturedAt": iso_now(),
            "bsType": "overall",
            "bbox": {
                "xywh": {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1},
                "xyxy": [x1, y1, x2, y2],
                "area": (x2 - x1) * (y2 - y1),
            },
            "detectorParamsCore": {
                "endOfTrack": emission.end_of_track,
                "quality": face.quality,
                "trackDurationSeconds": round(time.monotonic() - face.started, 3),
            },
            "snapshotNote": cfg.snapshot.value,
            "photo": jpeg_b64(shot.frame) if cfg.include_frame else None,
            "normalized": jpeg_b64(crop),
        }
