from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing as mp
import signal

import uvicorn

from .agent import Agent
from .controller_client import ControllerClient
from .delivery import DeliveryService, Route
from .health import create_health_app
from .logs import configure_logging
from .mediamtx import MediaMTX
from .settings import WorkerSettings, get_settings

log = logging.getLogger("watchtower_worker")


async def run(settings: WorkerSettings) -> None:
    ctx = mp.get_context("spawn")  # same behaviour on Linux, macOS and Windows
    results_queue = ctx.Queue(maxsize=1000)
    extra_headers = {"watchtower-token": settings.watchtower_token} if settings.results_send_token else {}
    delivery = DeliveryService(
        results_queue=results_queue,
        spool_path=settings.state_dir / "spool.sqlite3",
        spool_max_bytes=settings.spool_max_mb * 2**20,
        realtime_queue_size=settings.realtime_queue_size,
        max_age_seconds=settings.max_result_age_ms / 1000,
        timeout_seconds=settings.delivery_timeout_seconds,
        require_encryption=settings.require_encryption,
        default_route=Route(settings.results_url, settings.delivery_mode),
        extra_headers=extra_headers,
    )
    mediamtx = None
    if settings.mediamtx_enabled:
        mediamtx = MediaMTX(settings.mediamtx_api_url, settings.mediamtx_rtsp_url,
                            username=settings.mediamtx_username, password=settings.mediamtx_password,
                            bearer_token=settings.mediamtx_bearer_token)
    client = ControllerClient(settings.controller_url, settings.watchtower_token, settings.controller_timeout_seconds)
    agent = Agent(settings, client=client, delivery=delivery, results_queue=results_queue, mediamtx=mediamtx,
                  mp_context=ctx)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # not available on Windows
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(agent.drain_and_stop()))

    server = uvicorn.Server(uvicorn.Config(create_health_app(agent), host=settings.health_host,
                                           port=settings.health_port, log_config=None, lifespan="off"))
    server.install_signal_handlers = lambda: None  # the agent owns shutdown
    delivery.start()
    server_task = asyncio.create_task(server.serve())
    log.info("worker starting (controller=%s, mediamtx=%s)", settings.controller_url, settings.mediamtx_enabled)
    try:
        await agent.run()
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await server_task
        await asyncio.to_thread(delivery.stop)
        await client.aclose()
        if mediamtx:
            await mediamtx.aclose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    if not settings.watchtower_token:
        log.warning("WATCHTOWER_TOKEN is empty; registration only works if the controller runs AUTH_PROVIDER=none")
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
