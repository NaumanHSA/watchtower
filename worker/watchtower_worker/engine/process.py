"""Engine subprocess: one per stream. Reads frames, detects and tracks faces, emits results.

Talks to the parent only through two queues:
  status_queue   ("status", stream_id, generation, status, fps, error)
  results_queue  {"stream_id", "generation", "event"}
Exit codes: 0 stopped on request, 2 source unavailable, 3 failed to start.
"""
from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass
from typing import Any

from watchtower_contracts import ObservedStatus, StreamConfig

EXIT_STOPPED = 0
EXIT_SOURCE_FAILED = 2
EXIT_START_FAILED = 3
FPS_REPORT_SECONDS = 5.0


@dataclass(frozen=True)
class EngineSpec:
    stream_id: str
    stream_name: str
    generation: int
    worker_id: str
    input_url: str
    config: dict[str, Any]  # StreamConfig.model_dump(mode="json"); plain data so it pickles anywhere
    detector_backend: str = "onnx"


class Engine:
    def __init__(self, spec: EngineSpec, stop_event, status_queue, results_queue, detector=None, source=None):
        self.spec = spec
        self.config = StreamConfig.model_validate(spec.config)
        self.stop_event = stop_event
        self.status_queue = status_queue
        self.results_queue = results_queue
        self._detector = detector
        self._source = source
        self.status: ObservedStatus | None = None
        self.log = logging.getLogger(f"engine.{spec.stream_id[:8]}")

    def report(self, status: ObservedStatus, fps: float | None = None, error: str | None = None) -> None:
        self.status = status
        try:
            self.status_queue.put_nowait(
                ("status", self.spec.stream_id, self.spec.generation, status.value, fps, error))
        except queue.Full:
            pass

    def run(self) -> int:
        from .faces import FaceBook
        from .images import resize_to_width
        from .source import FrameSource, SourceState
        from .tracker import IoUTracker

        cfg = self.config
        self.report(ObservedStatus.STARTING)
        try:
            detector = self._detector
            if detector is None:
                from .detector import build_detector

                detector = build_detector(self.spec.detector_backend, cfg.detector_resolution,
                                          cfg.detection_confidence, cfg.max_faces)
        except Exception as e:
            self.log.exception("detector failed to load")
            self.report(ObservedStatus.ERROR, error=f"detector failed to load: {e}")
            return EXIT_START_FAILED

        source = self._source or FrameSource(self.spec.input_url, reconnect_interval=cfg.reconnect_interval_s,
                                             reconnect_attempts=cfg.reconnect_attempts)
        source.start()
        tracker = IoUTracker(cfg.track_confirm_frames, cfg.track_max_missed_frames)
        book = FaceBook(cfg)
        min_gap = 1.0 / cfg.fps if cfg.fps else 0.0
        seq, next_slot = 0, 0.0
        frames, window_start = 0, time.monotonic()
        dropped_results = 0
        try:
            while not self.stop_event.is_set():
                if source.state == SourceState.FAILED:
                    self.report(ObservedStatus.ERROR, error=source.error or "source unavailable")
                    return EXIT_SOURCE_FAILED
                wait = next_slot - time.monotonic()
                if wait > 0:
                    self.stop_event.wait(wait)  # pace to the configured fps
                    continue
                frame, seq = source.read(seq, timeout=0.5)
                if frame is None:
                    if source.state == SourceState.RECONNECTING and self.status != ObservedStatus.RECONNECTING:
                        self.report(ObservedStatus.RECONNECTING, error=source.error)
                    continue
                next_slot = time.monotonic() + min_gap
                frame = resize_to_width(frame, cfg.max_frame_width)
                update = tracker.update(detector(frame))
                for emission in book.update(update.visible, update.ended, frame):
                    event = book.build_event(emission, stream_id=self.spec.stream_id,
                                             stream_name=self.spec.stream_name, worker_id=self.spec.worker_id,
                                             generation=self.spec.generation)
                    try:
                        self.results_queue.put_nowait({"stream_id": self.spec.stream_id,
                                                       "generation": self.spec.generation, "event": event})
                    except queue.Full:
                        dropped_results += 1
                frames += 1
                elapsed = time.monotonic() - window_start
                if self.status != ObservedStatus.RUNNING or elapsed >= FPS_REPORT_SECONDS:
                    self.report(ObservedStatus.RUNNING, fps=round(frames / elapsed, 2) if elapsed > 0.5 else None)
                    if elapsed >= FPS_REPORT_SECONDS:
                        frames, window_start = 0, time.monotonic()
                    if dropped_results:
                        self.log.warning("dropped %d results: delivery queue full", dropped_results)
                        dropped_results = 0
            return EXIT_STOPPED
        finally:
            source.stop()


def run_engine(spec: EngineSpec, stop_event, status_queue, results_queue, log_level: int = logging.INFO) -> None:
    """multiprocessing target (spawn): must stay a top-level function."""
    import sys

    import cv2

    cv2.setNumThreads(1)
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        code = Engine(spec, stop_event, status_queue, results_queue).run()
    except Exception as e:
        logging.getLogger("engine").exception("engine crashed")
        try:
            status_queue.put_nowait(("status", spec.stream_id, spec.generation, ObservedStatus.ERROR.value, None,
                                     f"engine crashed: {e}"))
        except Exception:  # noqa: S110 - the parent still sees the non-zero exit code
            pass
        code = EXIT_START_FAILED
    sys.exit(code)
