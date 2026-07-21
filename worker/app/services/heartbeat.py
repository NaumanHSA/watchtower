from config import config
from app.db import get_db
from app.db import crud as db_crud
from app.utils import worker_callback_url_auto
import httpx
import asyncio
import sys
import logging
import urllib.parse

logger = logging.getLogger()


async def heartbeat_call(max_retries: int = 3, backoff: float = 1.0) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                headers = {
                    "Content-Type": "application/json",
                    "watchtower-token": config.WATCHTOWER_TOKEN,
                    "worker-api-key": config.WORKER_API_KEY,
                }
                # collect running streams from DB
                streams = []
                db = get_db()
                try:
                    rows = db_crud.list_streams(db)
                    for row in rows:
                        streams.append({
                            "id": row.stream_id,
                            "source_url": row.source_url,
                            "status": row.status
                        })
                finally:
                    db.close()

                r = await c.patch(
                    f"{config.CONTROLLER_URL.strip('/')}/internal/worker/heartbeat/{config.resolved_worker_id}", 
                    headers=headers,
                    json={
                        "worker_id": config.resolved_worker_id,
                        "status": "online",
                        "streams": streams,
                    }
                )
                if 200 <= r.status_code < 300:
                    return True
        except Exception as e:
            logger.error(f"[heartbeat] attempt {attempt} failed: {e}")
        # exponential backoff
        logger.info(f"[heartbeat] attempt {attempt} failed. Retrying in {backoff * attempt} seconds...")
        await asyncio.sleep(backoff * attempt)
    return False


async def announce_heartbeat(interval: int = 5, max_retries: int = 3, backoff: float = 1.0):
    while True:
        await asyncio.sleep(interval)
        ok = await heartbeat_call(max_retries, backoff)
        if not ok:
            logger.error("[worker] Controller unreachable. Shutting down via internal endpoint...")
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    await c.post(
                        f"http://127.0.0.1:{config.HOST_PORT}/internal/shutdown/{config.INTERNAL_SHUTDOWN_KEY}",
                    )
            except Exception as e:
                logger.error(f"[worker] Failed to call shutdown endpoint: {e}")
                # fallback hard-exit if internal endpoint unreachable
                sys.exit(1)
