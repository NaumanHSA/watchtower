# app/engine/face_tracker.py
from __future__ import annotations
from typing import Dict, Any, Optional, Set, Iterator, Tuple
from datetime import datetime

# assumes you already have these models:
from app.engine.models import FaceTracksState, FaceTrack


class FaceTracker:
    def __init__(
        self,
        date_format: str,
        *,
        missing_tolerance: int = 0,          # frames allowed missing before inferring end
        infer_end_from_missing: bool = True,
    ):
        self.state = FaceTracksState()
        self.date_format = date_format
        self.missing_tolerance = max(0, int(missing_tolerance))
        self.infer_end_from_missing = bool(infer_end_from_missing)
        self._missing_counts: Dict[str, int] = {}

    def update_from_active(
        self,
        active_dict: Dict[str, Dict[str, Any]],
        frame,
        frame_flag: str,
        now_str: Optional[str] = None,
        max_cropped_face_resolution: int = 0,
        cropped_face_margin: float = 0.1,
    ) -> None:
        if now_str is None:
            now_str = datetime.now().strftime(self.date_format)

        seen: Set[str] = set()
        for face_id, td in active_dict.items():
            fid = str(face_id)
            seen.add(fid)
            bbox = td["bbox"]
            confidence = float(td["confidence"])
            track = self.state.get_or_create(fid)
            track.update_with_detection(
                xyxy=[float(x) for x in bbox],
                confidence=confidence,
                frame=frame,
                timestamp=now_str,
                frame_flag=frame_flag,
                max_cropped_face_resolution=max_cropped_face_resolution,
                cropped_face_margin=cropped_face_margin,
            )
            self._missing_counts[fid] = 0

        for fid in set(self.state.ids()) - seen:
            self._missing_counts[fid] = self._missing_counts.get(fid, 0) + 1

    def iter_due_or_ended(
        self,
        *,
        active_ids_set: Set[str],
        results_interval_ms: float,
        model_ended_ids: Optional[Set[str]] = None,
        now_str: Optional[str] = None,
    ) -> Iterator[Tuple[str, "FaceTrack", bool]]:
        if now_str is None:
            now_str = datetime.now().strftime(self.date_format)
        model_ended_ids = {str(x) for x in (model_ended_ids or set())}
        active_ids_set = {str(x) for x in active_ids_set}

        for face_id in list(self.state.ids()):
            track = self.state.tracks[face_id]

            explicit_end = face_id in model_ended_ids
            inferred_end = False
            if self.infer_end_from_missing and face_id not in active_ids_set:
                missing = self._missing_counts.get(face_id, 0)
                inferred_end = missing > self.missing_tolerance

            end_of_track = explicit_end or inferred_end

            # Initial emit: never emitted before → force emit
            never_emitted = (track.last_emit_timestamp is None)

            # Periodic emit: only if interval elapsed
            ms_since_emit = track.ms_since_last_emit(self.date_format, now_override=now_str)
            due_by_interval = (ms_since_emit >= float(results_interval_ms))

            if end_of_track or never_emitted or due_by_interval:
                yield face_id, track, bool(end_of_track)

    def remove(self, face_id: str) -> None:
        fid = str(face_id)
        self.state.remove(fid)
        self._missing_counts.pop(fid, None)

    def ids(self) -> Set[str]:
        return set(self.state.ids())
