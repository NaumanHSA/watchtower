"""Helpers for worker tests. Functions used as engine targets must stay top-level (spawn)."""
from __future__ import annotations

import sys
import time

import numpy as np


def frame(width: int = 640, height: int = 480) -> np.ndarray:
    return np.full((height, width, 3), 80, dtype=np.uint8)


class FakeDetector:
    """Returns a scripted list of detections per call (repeats the last one)."""

    def __init__(self, script: list[list[list[float]]]):
        self.script = script
        self.calls = 0

    def __call__(self, _frame) -> list[list[float]]:
        out = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return out


class FakeSource:
    """Stands in for FrameSource: hands out a new frame on every read."""

    def __init__(self, frames: int | None = None, fail_after: int | None = None):
        from watchtower_worker.engine.source import SourceState

        self.state = SourceState.CONNECTED
        self.error = None
        self.frames = frames
        self.fail_after = fail_after
        self.seq = 0
        self._failed = SourceState.FAILED

    def start(self):
        return self

    def stop(self):
        pass

    def read(self, last_seq: int, timeout: float = 0.5):
        if self.fail_after is not None and self.seq >= self.fail_after:
            self.state, self.error = self._failed, "camera gone"
            return None, last_seq
        if self.frames is not None and self.seq >= self.frames:
            time.sleep(0.01)
            return None, last_seq
        self.seq += 1
        return frame(), self.seq


def well_behaved_engine(spec, stop_event, status_queue, results_queue, log_level=0):
    """Reports running, then waits to be stopped (exit 0)."""
    status_queue.put(("status", spec.stream_id, spec.generation, "running", 8.0, None))
    stop_event.wait(30)
    sys.exit(0)


def crashing_engine(spec, stop_event, status_queue, results_queue, log_level=0):
    status_queue.put(("status", spec.stream_id, spec.generation, "error", None, "boom"))
    sys.exit(3)
