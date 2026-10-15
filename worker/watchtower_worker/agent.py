"""The worker's control loop: sync with the controller, then make reality match."""
from __future__ import annotations

import asyncio
import logging
import platform
import random
import time
from typing import Any

import psutil

from watchtower_contracts import Capacity, RegisterRequest, SyncRequest, WorkerLoad

from . import __version__
from .controller_client import ControllerClient, Forbidden, Unauthorized, Unavailable
from .delivery import DeliveryService
from .identity import Identity
from .mediamtx import MediaMTX
from .settings import WorkerSettings
from .supervisor import Supervisor

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30.0
REVOKED_RETRY_SECONDS = 60.0


class Agent:
    def __init__(self, settings: WorkerSettings, *, client: ControllerClient, delivery: DeliveryService,
                 results_queue: Any, mediamtx: MediaMTX | None = None, supervisor: Supervisor | None = None,
                 mp_context: Any = None):
        self.settings = settings
        self.client = client
        self.delivery = delivery
        self.mediamtx = mediamtx
        self.supervisor = supervisor or Supervisor(
            worker_id=settings.worker_id or "unregistered",
            mediamtx=mediamtx,
            engine_input=settings.engine_input,
            detector_backend=settings.detector_backend,
            default_delivery_mode=settings.delivery_mode,
            max_restarts=settings.max_engine_restarts,
            results_queue=results_queue,
            set_route=delivery.set_route,
            log_level=logging.getLogger().level,
            mp_context=mp_context,
        )
        identity = Identity.load(settings.state_dir)
        self._worker_id_hint = settings.worker_id or (identity.worker_id if identity else None)
        # An identity issued by a different controller is useless there; register again.
        self.identity = identity if identity and identity.controller_url == settings.controller_url else None
        if self.identity:
            self.supervisor.worker_id = self.identity.worker_id
        self.sync_interval = 5.0
        self.lease_seconds = 60.0
        self.last_sync_ok: float | None = None
        self.consecutive_failures = 0
        self.sync_failures_total = 0
        self.revoked = False
        self.draining = False
        self._stopped_for_lease = False
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ loop

    async def run(self) -> None:
        poller = asyncio.create_task(self._poll_engines())
        try:
            while not self._stopping.is_set():
                try:
                    delay = await self.step()
                except Exception:  # a bug in one round must not take the worker down
                    log.exception("sync round failed unexpectedly; retrying")
                    delay = MAX_BACKOFF_SECONDS
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            poller.cancel()
            await self.supervisor.stop_all()

    async def step(self) -> float:
        """One sync round; returns seconds until the next one."""
        try:
            if self.identity is None:
                await self._register()
            assert self.identity is not None
            response = await self.client.sync(self.identity.worker_id, self.identity.secret, self._sync_request())
        except Unauthorized:
            log.warning("controller no longer recognises this worker; registering again")
            self.identity = None
            return 1.0
        except Forbidden as e:
            if not self.revoked:
                log.error("worker token rejected (%s); stopping all streams", e)
                self.revoked = True
                await self.supervisor.stop_all()
            return REVOKED_RETRY_SECONDS
        except Unavailable as e:
            self.consecutive_failures += 1
            self.sync_failures_total += 1
            await self._check_lease()
            delay = min(MAX_BACKOFF_SECONDS, 2 ** min(self.consecutive_failures, 5)) * random.uniform(0.5, 1.0)  # noqa: S311
            log.warning("sync failed (%s); retrying in %.1fs", e, delay)
            return delay

        if self.revoked:
            log.info("worker token accepted again")
        self.revoked = False
        self.consecutive_failures = 0
        self._stopped_for_lease = False
        self.last_sync_ok = time.monotonic()
        self.lease_seconds = response.lease_seconds
        self.sync_interval = response.sync_interval_ms / 1000
        self.delivery.set_key(response.result_key)
        if not self.draining:
            await self.supervisor.reconcile(response.desired, response.results_url or self.settings.results_url)
        return self.sync_interval * random.uniform(0.9, 1.1)  # noqa: S311 - jitter, not crypto

    async def drain_and_stop(self) -> None:
        """Graceful shutdown: tell the controller to move our streams, then stop them."""
        if self.draining:
            return
        log.info("draining: asking the controller to move this worker's streams")
        self.draining = True
        if self.identity is not None:
            try:
                await asyncio.wait_for(self.client.sync(self.identity.worker_id, self.identity.secret,
                                                        self._sync_request()), timeout=5)
            except Exception as e:  # shutting down regardless
                log.warning("could not report draining: %s", e)
        await self.supervisor.stop_all()
        self._stopping.set()

    # ------------------------------------------------------------------ helpers

    async def _register(self) -> None:
        request = RegisterRequest(
            worker_id=self._worker_id_hint,
            name=self.settings.worker_name,
            version=__version__,
            capacity=Capacity(max_streams=self.settings.max_streams),
            webrtc_base_url=self.settings.public_webrtc_url if self.settings.mediamtx_enabled else None,
            host_info={"hostname": platform.node(), "platform": platform.platform(),
                       "cpus": psutil.cpu_count(), "memory_mb": psutil.virtual_memory().total // 2**20},
        )
        try:
            response = await self.client.register(request)
        except Unauthorized as e:  # on register, 401 means the token itself is bad
            raise Forbidden(str(e)) from e
        self.identity = Identity(response.worker_id, response.worker_secret, self.settings.controller_url)
        self.identity.save(self.settings.state_dir)
        self._worker_id_hint = response.worker_id
        self.supervisor.worker_id = response.worker_id
        self.sync_interval = response.sync_interval_ms / 1000
        self.lease_seconds = response.lease_seconds
        log.info("registered as worker %s", response.worker_id)

    def _sync_request(self) -> SyncRequest:
        return SyncRequest(
            version=__version__,
            capacity=Capacity(max_streams=self.settings.max_streams),
            load=WorkerLoad(cpu_percent=psutil.cpu_percent(interval=None),
                            memory_percent=psutil.virtual_memory().percent),
            draining=self.draining,
            streams=[] if self.draining else self.supervisor.observed(),
            delivery=self.delivery.stats(),
        )

    async def _check_lease(self) -> None:
        if self.last_sync_ok is None or self._stopped_for_lease:
            return
        if time.monotonic() - self.last_sync_ok <= self.lease_seconds:
            return
        if self.settings.on_lease_expiry == "stop" and self.supervisor.streams:
            log.warning("lease expired without reaching the controller; stopping streams (ON_LEASE_EXPIRY=stop)")
            await self.supervisor.stop_all()
            self._stopped_for_lease = True

    async def _poll_engines(self) -> None:
        while True:
            try:
                self.supervisor.poll()
            except Exception:
                log.exception("engine poll failed")
            await asyncio.sleep(0.5)

    @property
    def ready(self) -> bool:
        if self.last_sync_ok is None or self.revoked:
            return False
        return time.monotonic() - self.last_sync_ok <= max(3 * self.sync_interval, 15)
