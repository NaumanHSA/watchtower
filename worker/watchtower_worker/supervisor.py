"""Keeps one engine process per assigned stream, matching the controller's desired state."""
from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from watchtower_contracts import DeliveryMode, DesiredStream, ObservedStatus, ObservedStream

from .delivery import Route
from .engine.process import EXIT_STOPPED, EngineSpec, run_engine
from .mediamtx import MediaMTX

log = logging.getLogger(__name__)

RESTART_BACKOFF_SECONDS = (5, 15, 30, 60)


@dataclass
class Running:
    desired: DesiredStream
    spec: EngineSpec
    process: Any = None  # multiprocessing.Process (or a fake in tests)
    stop_event: Any = None
    status: ObservedStatus = ObservedStatus.STARTING
    fps: float | None = None
    error: str | None = None
    restarts: int = 0
    restart_at: float | None = None  # monotonic; set while waiting to restart after a crash
    gave_up: bool = False
    live_view_error: str | None = None  # MediaMTX path missing; analysis still runs

    def observed(self) -> ObservedStream:
        error = self.error or (f"live view unavailable: {self.live_view_error}" if self.live_view_error else None)
        return ObservedStream(stream_id=self.desired.stream_id, generation=self.desired.generation,
                              status=self.status, fps=self.fps, error=error, restarts=self.restarts)


class Supervisor:
    def __init__(
        self,
        *,
        worker_id: str,
        mediamtx: MediaMTX | None,
        engine_input: str,
        detector_backend: str,
        default_delivery_mode: DeliveryMode,
        max_restarts: int,
        results_queue: Any,
        set_route: Callable[[str, Route], None],
        log_level: int = logging.INFO,
        engine_target: Callable[..., None] = run_engine,
        mp_context: Any = None,
    ):
        self.worker_id = worker_id
        self.mediamtx = mediamtx
        self.engine_input = engine_input
        self.detector_backend = detector_backend
        self.default_delivery_mode = default_delivery_mode
        self.max_restarts = max_restarts
        self.results_queue = results_queue
        self.set_route = set_route
        self.log_level = log_level
        self.engine_target = engine_target
        self.ctx = mp_context or mp.get_context("spawn")
        self.status_queue = self.ctx.Queue(maxsize=10_000)
        self.streams: dict[str, Running] = {}
        self._fallback_results_url: str | None = None
        self._mediamtx_down = False

    # ------------------------------------------------------------------ reconcile

    async def reconcile(self, desired: list[DesiredStream], results_url: str | None) -> None:
        self._fallback_results_url = results_url
        wanted = {d.stream_id: d for d in desired}
        for stream_id, running in list(self.streams.items()):
            target = wanted.get(stream_id)
            if target is None or target.generation != running.desired.generation:
                keep_path = target is not None and target.source_url == running.desired.source_url
                await self._stop(stream_id, remove_path=not keep_path)
        for stream_id, target in wanted.items():
            self._update_route(target)
            if stream_id not in self.streams:
                await self._start(target)
        if self.mediamtx:
            await self._sync_mediamtx(set(wanted))

    async def stop_all(self) -> None:
        for stream_id in list(self.streams):
            await self._stop(stream_id, remove_path=True)

    def observed(self) -> list[ObservedStream]:
        return [r.observed() for r in self.streams.values()]

    # ------------------------------------------------------------------ polling

    def poll(self) -> None:
        """Apply status messages from engines and handle crashed engines. Call often."""
        while True:
            try:
                _, stream_id, generation, status, fps, error = self.status_queue.get_nowait()
            except queue.Empty:
                break
            running = self.streams.get(stream_id)
            if running is None or running.desired.generation != generation:
                continue  # a report from an engine we already replaced
            running.status = ObservedStatus(status)
            if fps is not None or running.status != ObservedStatus.RUNNING:
                running.fps = fps
            running.error = error

        now = time.monotonic()
        for running in self.streams.values():
            if running.gave_up:
                continue
            if running.restart_at is not None:
                if now >= running.restart_at:
                    running.restart_at = None
                    self._spawn(running)
                continue
            process = running.process
            if process is None or process.is_alive():
                continue
            code = process.exitcode
            if code == EXIT_STOPPED and running.stop_event.is_set():
                continue
            running.restarts += 1
            if running.restarts > self.max_restarts:
                running.gave_up = True
                running.status = ObservedStatus.ERROR
                running.error = running.error or f"engine exited with code {code}"
                log.error("stream %s: giving up after %d restarts (%s)", running.desired.stream_id,
                          running.restarts - 1, running.error)
                continue
            delay = RESTART_BACKOFF_SECONDS[min(running.restarts - 1, len(RESTART_BACKOFF_SECONDS) - 1)]
            running.restart_at = now + delay
            running.status = ObservedStatus.RECONNECTING
            log.warning("stream %s: engine exited (code %s, %s); restarting in %ss", running.desired.stream_id,
                        code, running.error, delay)

    # ------------------------------------------------------------------ internals

    def _update_route(self, desired: DesiredStream) -> None:
        cfg = desired.config
        self.set_route(desired.stream_id, Route(url=cfg.results_url or self._fallback_results_url,
                                                mode=cfg.delivery_mode or self.default_delivery_mode))

    async def _start(self, desired: DesiredStream) -> None:
        live_view_error = None
        input_url = desired.source_url
        if self.mediamtx:
            ok, live_view_error = await self.mediamtx.add_path(desired.stream_id, desired.source_url)
            if not ok:
                log.warning("stream %s: MediaMTX path failed (%s); engine reads the camera directly",
                            desired.stream_id, live_view_error)
            elif self.engine_input == "mediamtx":
                input_url = self.mediamtx.restream_url(desired.stream_id)  # one camera connection for all
        spec = EngineSpec(
            stream_id=desired.stream_id, stream_name=desired.name, generation=desired.generation,
            worker_id=self.worker_id, input_url=input_url, config=desired.config.model_dump(mode="json"),
            detector_backend=self.detector_backend,
        )
        running = Running(desired=desired, spec=spec, live_view_error=live_view_error)
        self.streams[desired.stream_id] = running
        self._spawn(running)
        log.info("stream %s: started generation %d", desired.stream_id, desired.generation)

    def _spawn(self, running: Running) -> None:
        running.stop_event = self.ctx.Event()
        running.process = self.ctx.Process(
            target=self.engine_target,
            args=(running.spec, running.stop_event, self.status_queue, self.results_queue, self.log_level),
            name=f"engine-{running.spec.stream_id[:8]}",
            daemon=True,
        )
        running.process.start()

    async def _stop(self, stream_id: str, remove_path: bool) -> None:
        running = self.streams.pop(stream_id, None)
        if running is None:
            return
        process = running.process
        if process is not None and process.is_alive():
            running.stop_event.set()
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.kill()
        if remove_path and self.mediamtx:
            await self.mediamtx.delete_path(stream_id)
        log.info("stream %s: stopped", stream_id)

    async def _sync_mediamtx(self, wanted: set[str]) -> None:
        """MediaMTX keeps API-added paths in memory only. Re-add what a restart lost and
        remove paths of streams this worker no longer runs."""
        paths = await self.mediamtx.list_paths()
        if paths is None:
            if not self._mediamtx_down:
                log.warning("MediaMTX API unreachable: live view is down, analysis continues")
                self._mediamtx_down = True
            return
        if self._mediamtx_down:
            log.info("MediaMTX API reachable again")
            self._mediamtx_down = False
        present = set(paths)
        for stream_id, running in self.streams.items():
            if stream_id in present:
                running.live_view_error = None
                continue
            ok, error = await self.mediamtx.add_path(stream_id, running.desired.source_url)
            running.live_view_error = None if ok else error
            if ok:
                log.info("stream %s: live view path restored", stream_id)
        for name in present - wanted:
            if self.mediamtx.is_managed(name):
                await self.mediamtx.delete_path(name)
