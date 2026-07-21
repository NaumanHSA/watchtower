from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from contextlib import asynccontextmanager

from .logger import configure_logging
from .db import connect, close
from .routers import health_router, workers_router, streams_router, callbacks_router, stats_router
from .services import (
    recovery_job, 
    workers_watchdog_job,
    streams_watchdog_job,
    notifications_router, 
    manager
)
from config import config
from .logger import configure_logging

logger = configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init once per process
    # Open DB connection + create indexes
    await connect()
    await manager.start()
    try:
        logger.info(f"[main] Starting recovery job with interval {config.RECOVERY_INTERVAL} seconds")
        task1 = asyncio.create_task(recovery_job(interval=config.RECOVERY_INTERVAL))
        logger.info(f"[main] Starting watchdog job with interval {config.WATCHDOG_INTERVAL} seconds")
        task2 = asyncio.create_task(workers_watchdog_job(interval=config.WATCHDOG_INTERVAL, retries=3))
        task3 = asyncio.create_task(streams_watchdog_job(interval=config.WATCHDOG_INTERVAL))
        yield
    finally:
        # Graceful shutdown
        await manager.stop()
        await close()

def create_app() -> FastAPI:
    app = FastAPI(
        title=config.APP_NAME,
        version=config.APP_VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(health_router)
    app.include_router(workers_router)
    app.include_router(streams_router)
    app.include_router(callbacks_router)
    app.include_router(stats_router)
    app.include_router(notifications_router)

    # for testing
    # from .routers import broadcast_router
    # app.include_router(broadcast_router)
    return app


if __name__ == "__main__":
    app = create_app()

    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_config=None)

