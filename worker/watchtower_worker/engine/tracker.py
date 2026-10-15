"""Minimal IoU tracker: no motion model, built for a few faces at 5-15 analysed frames per second."""
from __future__ import annotations

from dataclasses import dataclass, field


def iou(a: list[float], b: list[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: int
    bbox: list[float]
    confidence: float
    hits: int = 1
    missed: int = 0
    confirmed: bool = False


@dataclass
class TrackerUpdate:
    visible: list[Track] = field(default_factory=list)  # confirmed tracks matched in this frame
    ended: list[int] = field(default_factory=list)  # confirmed tracks that just ended


class IoUTracker:
    def __init__(self, confirm_frames: int = 5, max_missed: int = 8, iou_threshold: float = 0.3):
        self.confirm_frames = max(1, confirm_frames)
        self.max_missed = max(1, max_missed)
        self.iou_threshold = iou_threshold
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def update(self, detections: list[list[float]]) -> TrackerUpdate:
        """detections: [[x1, y1, x2, y2, confidence], ...]"""
        pairs = sorted(
            ((iou(t.bbox, d), tid, di) for tid, t in self.tracks.items() for di, d in enumerate(detections)),
            reverse=True,
        )
        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()
        for score, tid, di in pairs:  # globally greedy: best overlaps first
            if score < self.iou_threshold:
                break
            if tid in matched_tracks or di in matched_dets:
                continue
            matched_tracks.add(tid)
            matched_dets.add(di)
            track = self.tracks[tid]
            track.bbox = list(detections[di][:4])
            track.confidence = float(detections[di][4])
            track.hits += 1
            track.missed = 0
            if track.hits >= self.confirm_frames:
                track.confirmed = True

        result = TrackerUpdate()
        for tid in list(self.tracks):
            if tid in matched_tracks:
                continue
            track = self.tracks[tid]
            track.missed += 1
            if not track.confirmed:
                del self.tracks[tid]  # a tentative face that disappears was probably noise
            elif track.missed > self.max_missed:
                del self.tracks[tid]
                result.ended.append(tid)

        for di, det in enumerate(detections):
            if di in matched_dets:
                continue
            track = Track(self._next_id, list(det[:4]), float(det[4]), confirmed=self.confirm_frames == 1)
            self.tracks[track.track_id] = track
            self._next_id += 1

        result.visible = [t for t in self.tracks.values() if t.confirmed and t.missed == 0]
        return result
