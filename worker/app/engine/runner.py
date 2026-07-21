from __future__ import annotations
import multiprocessing as mp
import time
import cv2
import logging
import os
from typing import Dict, Optional, Tuple, Iterable
from sqlalchemy.orm import Session

from config import config
from ..db import crud as db_crud
from ..db.models import Stream
from .engine import Engine
from .args import EngineArgs


def _run_engine_entrypoint(
    args: EngineArgs,
    stream_id: str,
    stop_event: mp.Event,
    log_level: int = logging.INFO,
):
    """
    Subprocess entrypoint. Kept as a top-level target for pickleability.
    """
    # lightweight logging setup for child proc
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(f"engine[{stream_id}]")

    # OpenCV threading knobs (optional but you had them)
    try:
        cv2.setNumThreads(config.OPENCV_THREADS)
        cv2.setUseOptimized(config.OPENCV_OPTIMIZATION)
    except Exception:
        pass

    engine = Engine(args=args, stream_id=stream_id, stop_event=stop_event, logger=logger)
    engine.run()


class StreamEngineManager:
    """
    Manages Engine subprocesses keyed by stream_id.

    - start(stream_id): spawn Engine in a daemon Process
    - stop(stream_id): signal and join with timeout (kill if needed)
    - restart(stream_id): stop then start
    - ensure_running(stream_id): start if not already running
    - is_alive(stream_id): check process liveness
    - stop_all(): graceful shutdown of all processes

    DB side effects:
    - set_stream_started(db, stream_id, pid) on start
    - set_stream_stopped(db, stream_id) on stop
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        join_timeout: float = 5.0,
        log_level: int = logging.INFO,
    ):
        self._logger = logger or logging.getLogger("StreamEngineManager")
        self._join_timeout = join_timeout
        self._log_level = log_level
        # in-memory registry: stream_id -> (Process, Event)
        self._registry: Dict[str, Tuple[mp.Process, mp.Event]] = {}

    # ------------------------ Public API ------------------------

    def start(self, db: Session, stream_id: str) -> Tuple[mp.Process, mp.Event]:
        """
        Start a new Engine process for the given stream_id.
        Raises on invalid/missing stream, or if spawn fails.
        """
        if stream_id in self._registry and self.is_alive(stream_id):
            self._logger.info(f"[{stream_id}] already running (pid={self._registry[stream_id][0].pid}).")
            return self._registry[stream_id]

        row: Stream = db_crud.get_stream(db, stream_id)
        if not row:
            raise RuntimeError(f"Stream {stream_id} not found")

        # Lightweight health check on the source URL (like your previous code)
        self._validate_source_url(row.source_url)

        # Build typed, validated args
        args = EngineArgs.from_stream_row(row, config)

        # Create shared stop event and spawn
        stop_event = mp.Event()
        proc = mp.Process(
            target=_run_engine_entrypoint,
            args=(args, stream_id, stop_event, self._log_level),
            daemon=True,
        )
        proc.start()
        time.sleep(1.0)  # brief settle

        if proc.exitcode not in (None, 0):
            self._logger.error(f"[{stream_id}] process crashed immediately (exitcode={proc.exitcode})")
            raise RuntimeError(f"Process {stream_id} crashed immediately (exitcode={proc.exitcode})")

        self._registry[stream_id] = (proc, stop_event)
        db_crud.set_stream_started(db, stream_id, proc.pid)
        self._logger.info(f"[{stream_id}] process started (pid={proc.pid})")
        return proc, stop_event

    def stop(self, db: Session, stream_id: str, force: bool = True) -> None:
        """
        Gracefully stop a running process. If it won’t exit within join_timeout,
        optionally force-kill.
        """
        proc, ev = self._registry.get(stream_id, (None, None))
        if not proc:
            # Might still have a PID in DB; try to mark stopped anyway
            db_crud.set_stream_stopped(db, stream_id)
            self._logger.info(f"[{stream_id}] not running.")
            return

        if proc.is_alive():
            self._logger.info(f"[{stream_id}] stopping (pid={proc.pid})...")
            try:
                ev.set()  # signal Engine.graceful_shutdown()
            except Exception:
                pass
            proc.join(timeout=self._join_timeout)

        if proc.is_alive():
            if force:
                self._logger.warning(f"[{stream_id}] did not stop within {self._join_timeout}s; terminating...")
                try:
                    proc.terminate()
                except Exception:
                    pass
                proc.join(timeout=2.0)
                if proc.is_alive():
                    self._logger.error(f"[{stream_id}] still alive after terminate(); killing...")
                    try:
                        os.kill(proc.pid, 9)  # last resort
                    except Exception:
                        pass
            else:
                self._logger.warning(f"[{stream_id}] still running after timeout; leaving it alive per force=False.")

        # Update DB and registry
        db_crud.set_stream_stopped(db, stream_id)
        self._registry.pop(stream_id, None)
        self._logger.info(f"[{stream_id}] stopped.")

    def restart(self, db: Session, stream_id: str) -> Tuple[mp.Process, mp.Event]:
        self.stop(db, stream_id)
        return self.start(db, stream_id)

    def ensure_running(self, db: Session, stream_id: str) -> Tuple[mp.Process, mp.Event]:
        if not self.is_alive(stream_id):
            return self.start(db, stream_id)
        return self._registry[stream_id]

    def is_alive(self, stream_id: str) -> bool:
        proc, _ = self._registry.get(stream_id, (None, None))
        return bool(proc and proc.is_alive())

    def list_running(self) -> Iterable[str]:
        """
        Return stream_ids that are known running (per registry).
        """
        for sid, (proc, _) in list(self._registry.items()):
            if proc.is_alive():
                yield sid
            else:
                # cleanup stale
                self._registry.pop(sid, None)

    def get(self, stream_id: str) -> Optional[Tuple[mp.Process, mp.Event]]:
        return self._registry.get(stream_id)

    def stop_all(self, db: Session, force: bool = True) -> None:
        for sid in list(self._registry.keys()):
            try:
                self.stop(db, sid, force=force)
            except Exception:
                self._logger.exception(f"[{sid}] stop_all encountered an error; continuing...")

    # ------------------------ Internals ------------------------
    def _validate_source_url(self, source_url: str) -> None:
        """
        Quick liveness test before spawning to fail fast on bad sources.
        Replicates your cap.isOpened() check but contained here.
        """
        cap = cv2.VideoCapture(source_url)
        try:
            if not cap.isOpened():
                raise RuntimeError("Unable to open source_url; invalid stream")
        finally:
            cap.release()
