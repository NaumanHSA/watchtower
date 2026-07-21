from __future__ import annotations
from typing import List, Dict, Optional, Tuple, Union
from datetime import datetime
from app.utils import crop_face_from_frame
import numpy as np

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, ConfigDict
    _PydV2 = True
except Exception:  # pragma: no cover
    from pydantic import BaseModel, Field
    _PydV2 = False


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class FaceSnapshot(BaseModel):
    timestamp: str
    bbox: BBox
    xyxy: list
    quality: float
    # uploader placeholders (set when emitting a record)
    full_frame: Optional[str] = None
    normalized: Optional[str] = None


class FaceHistory(BaseModel):
    bbox: List[BBox] = Field(default_factory=list)
    active_tracks: List[str] = Field(default_factory=list)


class FaceData(BaseModel):
    first: Optional[FaceSnapshot] = None
    last: Optional[FaceSnapshot] = None
    best: Optional[FaceSnapshot] = None
    history: FaceHistory = Field(default_factory=FaceHistory)


class FaceTrack(BaseModel):
    """
    One tracked face over time.
    """
    if _PydV2:
        model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    quality_sum: float = 0.0
    best_quality: float = 0.0
    last_emit_timestamp: Optional[str] = None

    # Do not serialize numpy frames
    frame: Optional[np.ndarray] = Field(default=None, exclude=True)
    face_crop: Optional[np.ndarray] = Field(default=None, exclude=True)
    face: FaceData = Field(default_factory=FaceData)
    did_frame_update: Optional[bool] = False

    # ------------ updates ------------
    def update_with_detection(
        self,
        xyxy: Tuple[float, float, float, float],
        confidence: float,
        frame: np.ndarray,
        timestamp: str,
        frame_flag: str,  # "first" | "last" | "best"
        max_cropped_face_resolution: int,
        cropped_face_margin: float = 0.1,
    ) -> None:
        self.did_frame_update = False
        x1, y1, x2, y2 = xyxy
        bb = BBox(x=x1, y=y1, w=abs(x2 - x1), h=abs(y2 - y1))
        self.face_crop = crop_face_from_frame(frame, xyxy, max_cropped_face_resolution, margin=cropped_face_margin)
        if self.first_timestamp is None:
            self.first_timestamp = timestamp
            self.face.first = FaceSnapshot(timestamp=timestamp, bbox=bb, xyxy=xyxy, quality=float(confidence))
            if frame_flag == "first":
                self.frame = frame
                self.did_frame_update = True

        self.last_timestamp = timestamp
        self.face.last = FaceSnapshot(timestamp=timestamp, bbox=bb, xyxy=xyxy, quality=float(confidence))
        if frame_flag == "last":
            self.frame = frame
            self.did_frame_update = True
        self.face.history.bbox.append(bb)
        self.face.history.active_tracks.append(self.id)
        self.quality_sum += float(confidence)
        
        if float(confidence) > float(self.best_quality or 0.0):
            self.best_quality = float(confidence)
            self.face.best = FaceSnapshot(timestamp=timestamp, bbox=bb, xyxy=xyxy, quality=float(confidence))
            if frame_flag == "best":
                self.frame = frame
                self.did_frame_update = True

    # ------------ metrics ------------
    def track_duration_ms(
        self,
        date_format: str,
        now_override: Optional[Union[str, datetime]] = None,
    ) -> float:
        if not self.first_timestamp:
            return 0.0
        dt0 = datetime.strptime(self.first_timestamp, date_format)
        if now_override is None:
            if not self.last_timestamp:
                return 0.0
            dt1 = datetime.strptime(self.last_timestamp, date_format)
        else:
            dt1 = now_override if isinstance(now_override, datetime) else datetime.strptime(now_override, date_format)
        return float((dt1 - dt0).total_seconds() * 1000.0)

    def ms_since_last_emit(
        self,
        date_format: str,
        now_override: Optional[Union[str, datetime]] = None,
    ) -> float:
        """Milliseconds since we last emitted; if never emitted, return +inf to force initial emit."""
        if self.last_emit_timestamp is None:
            return float("inf")
        dt_last = datetime.strptime(self.last_emit_timestamp, date_format)
        if now_override is None:
            if not self.last_timestamp:
                return 0.0
            dt_now = datetime.strptime(self.last_timestamp, date_format)
        else:
            dt_now = now_override if isinstance(now_override, datetime) else datetime.strptime(now_override, date_format)
        return float((dt_now - dt_last).total_seconds() * 1000.0)

    def mark_emitted(self, when_str: str):
        """Call this right after you submit/broadcast a record for this track."""
        self.last_emit_timestamp = when_str

    def reset_window(self, now_str: str):
        """Optional: if you want rolling-window stats per interval."""
        self.first_timestamp = now_str
        # If you keep sums/history, reset them here if desired.
        # e.g., self.quality_sum = 0.0; self.face["history"]["bbox"].clear(); ...

    def average_quality(self) -> float:
        n = len(self.face.history.active_tracks)
        return round(self.quality_sum / n, 4) if n else 0.0

    # ------------ output ------------
    def to_record(
        self,
        stream_id: str,
        end_of_track: bool,
        frame_flag: str,
        date_format: str,
    ) -> Dict:
        # add placeholders to the chosen snapshot
        snap = None
        if frame_flag == "best":
            snap = self.face.best
        elif frame_flag == "last":
            snap = self.face.last
        else:
            snap = self.face.first
        if snap is not None:
            snap.full_frame = "multipart:photo"
            snap.normalized = "multipart:normalized"
        payload = self.model_dump() if _PydV2 else self.dict()  # frame excluded
        return {
            "cam_id": str(stream_id),
            "end_of_track": bool(end_of_track),
            "liveness_score": None,
            "quality": float(self.average_quality()),
            "track_duration_seconds": float(round(self.track_duration_ms(date_format) / 1000.0, 4)),
            "track": payload,
        }


class FaceTracksState(BaseModel):
    tracks: Dict[str, FaceTrack] = Field(default_factory=dict)

    def get_or_create(self, face_id: str) -> FaceTrack:
        if face_id not in self.tracks:
            self.tracks[face_id] = FaceTrack(id=str(face_id))
        return self.tracks[face_id]

    def remove(self, face_id: str) -> None:
        self.tracks.pop(face_id, None)

    def ids(self):
        return list(self.tracks.keys())
