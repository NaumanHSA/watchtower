from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audit import Auditor
from .auth import build_provider
from .db import Mongo
from .events import Notifier
from .limits import MaxBodySizeMiddleware
from .ratelimit import FailureLimiter
from .routers import streams, system, workers
from .scheduler import Scheduler
from .settings import Settings, get_settings

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# The dashboard talks to this controller and, for live video, to workers' MediaMTX (any origin).
DASHBOARD_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                 "media-src 'self' blob:; connect-src 'self' ws: wss: http: https:; "
                 "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        mongo = Mongo(settings.mongo_uri, settings.mongo_db)
        await mongo.ensure_indexes()
        provider = build_provider(settings)
        notifier = Notifier(settings.ws_send_queue_size, settings.ws_ping_interval_seconds, settings.redis_url)
        await notifier.start()
        scheduler = Scheduler(mongo, settings, notifier, notifier.instance_id)
        app.state.settings = settings
        app.state.mongo = mongo
        app.state.provider = provider
        app.state.notifier = notifier
        app.state.scheduler = scheduler
        app.state.limiter = FailureLimiter(settings.auth_failure_limit, settings.auth_failure_window_seconds)
        app.state.auditor = Auditor(mongo.db, settings.audit_retention_days)
        scheduler_task = asyncio.create_task(scheduler.run()) if settings.scheduler_enabled else None
        log.info("controller %s started (auth=%s, instance=%s)", __version__, settings.auth_provider,
                 notifier.instance_id)
        try:
            yield
        finally:
            if scheduler_task:
                scheduler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scheduler_task
            with contextlib.suppress(Exception):
                await scheduler.release_lease()  # let another replica take over immediately
            await notifier.stop()
            await provider.aclose()
            await mongo.close()

    app = FastAPI(title="Watchtower Controller", version=__version__, lifespan=lifespan,
                  description="Distributed camera analytics control plane. All endpoints except "
                              "/livez and /readyz need `Authorization: Bearer <token>`.")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["*"], allow_headers=["*"])
    app.include_router(system.router)
    app.include_router(workers.router)
    app.include_router(streams.router)

    @app.middleware("http")
    async def dashboard_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = DASHBOARD_CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_bytes)

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse("/ui/")

    return app
