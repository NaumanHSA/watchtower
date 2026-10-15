"""Camera reader: a background thread keeps only the newest frame, so analysis never lags."""
from __future__ import annotations

import logging
import os
import threading
import time
from enum import StrEnum
from urllib.parse import urlsplit

import cv2
import numpy as np

# RTSP over TCP survives lossy networks far better than the UDP default.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

log = logging.getLogger(__name__)


class SourceState(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPED = "stopped"


def is_file_source(url: str) -> bool:
    scheme = urlsplit(url).scheme
    return scheme in ("", "file") or len(scheme) == 1  # a bare path, file://, or a Windows drive letter


class FrameSource:
    """Network sources are read as fast as they arrive. Video files are played at their
    native frame rate and loop, so a file behaves like a camera (handy for demos and tests)."""

    def __init__(self, url: str, *, reconnect_interval: float = 5, reconnect_attempts: int = 5,
                 open_timeout_ms: int = 10_000, read_timeout_ms: int = 10_000):
        self.url = url
        self.reconnect_interval = reconnect_interval
        self.reconnect_attempts = reconnect_attempts
        self.open_timeout_ms = open_timeout_ms
        self.read_timeout_ms = read_timeout_ms
        self.is_file = is_file_source(url)
        self.state = SourceState.CONNECTING
        self.error: str | None = None
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="frame-source", daemon=True)

    def start(self) -> FrameSource:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=self.read_timeout_ms / 1000 + 2)

    def read(self, last_seq: int, timeout: float = 0.5) -> tuple[np.ndarray | None, int]:
        """Newest frame newer than last_seq, waiting up to `timeout`; (None, last_seq) if none."""
        with self._cond:
            if self._seq <= last_seq and not self._stop.is_set():
                self._cond.wait(timeout)
            if self._seq > last_seq:
                return self._frame, self._seq
            return None, last_seq

    def _open(self) -> cv2.VideoCapture:
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                  self.read_timeout_ms]
        return cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            cap = self._open()
            if cap.isOpened():
                self.state, self.error = SourceState.CONNECTED, None
                got_frame = False
                frame_gap = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 25.0) if self.is_file else 0.0
                next_frame = time.monotonic()
                while not self._stop.is_set():
                    if frame_gap:
                        delay = next_frame - time.monotonic()
                        if delay > 0 and self._stop.wait(delay):
                            break
                        next_frame = max(next_frame + frame_gap, time.monotonic() - frame_gap)
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if not got_frame:
                        got_frame, failures = True, 0  # a working connection resets the budget
                    with self._cond:
                        self._frame, self._seq = frame, self._seq + 1
                        self._cond.notify_all()
                cap.release()
                if self._stop.is_set():
                    break
                if self.is_file and got_frame:
                    continue  # end of file: loop
                self.error = "stream ended or stalled"
            else:
                cap.release()
                self.error = "cannot open source"
            failures += 1
            if failures >= self.reconnect_attempts:
                self.state = SourceState.FAILED
                log.warning("giving up on source after %d attempts: %s", failures, self.error)
                with self._cond:
                    self._cond.notify_all()
                return
            self.state = SourceState.RECONNECTING
            self._stop.wait(self.reconnect_interval)
        self.state = SourceState.STOPPED
