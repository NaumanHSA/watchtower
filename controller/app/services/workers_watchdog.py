import asyncio
import logging
import httpx
from ..db import connect
from ..repositories.workers_repo import WorkersRepo
from ..repositories.streams_repo import StreamsRepo
from ..services.notifications import manager
from ..services.events import evt
from ..enums import StreamEvent, StreamSeverity, StreamStatus

logger = logging.getLogger(__name__)


async def workers_watchdog_job(interval: int = 30, retries: int = 3):
    """
    Job that Periodically check if workers are alive by pinging their health endpoint.
    If a worker is unreachable after retries, mark its streams as dangling and delete the worker.
    """
    while True:
        try:
            db = await connect()
            wrepo = WorkersRepo(db)
            srepo = StreamsRepo(db)
            workers = await wrepo.list()  # get all registered workers
            if workers:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    for w in workers:
                        url = w.get("worker_health_url") or w.get("worker_url")
                        if not url: continue
                        alive = False
                        for attempt in range(1, retries + 1):
                            try:
                                resp = await client.get(url)
                                if resp.status_code == 200:
                                    alive = True
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.5 * attempt)

                        if not alive:
                            worker_id = w["worker_id"]
                            worker_url = w.get("worker_url", "unknown")
                            logger.warning(f"[Watchdog] Worker unreachable - worker_id={worker_id}, url={worker_url}, attempts={retries}")
                            
                            # 1. Notify clients
                            await manager.broadcast(evt(
                                name=StreamEvent.WORKER_UNREACHABLE,
                                severity=StreamSeverity.WARNING,
                                params={"worker_id": worker_id, "worker_url": worker_url},
                            ))
                            
                            # 2. Mark all streams from this worker as dangling
                            streams = await srepo.list(worker_id=worker_id)
                            if streams:
                                stream_ids = [s["stream_id"] for s in streams]
                                logger.info(f"[Watchdog] Marking {len(streams)} streams as Dangling - stream_ids={stream_ids}")
                                for stream_id in stream_ids:
                                    await srepo.update(stream_id, {"worker_id": None, "webrtc_url": None, "status": StreamStatus.DANGLING.value})

                            # 3. Delete worker itself
                            logger.info(f"[Watchdog] Removing unresponsive worker - worker_id={worker_id}")
                            await wrepo.delete(worker_id)
                            logger.info(f"[Watchdog] Successfully removed worker - worker_id={worker_id}")
                            
                            # 4. Notify clients
                            for s in streams:
                                await manager.broadcast(evt(
                                    name=StreamEvent.STREAM_DANGLING,
                                    severity=StreamSeverity.NOTICE,
                                    source="workers.watchdog",
                                    params={"stream_id": s["stream_id"], "worker_id": worker_id},
                                ))
                            await manager.broadcast(evt(
                                name=StreamEvent.WORKER_DELETED,
                                severity=StreamSeverity.NOTICE,
                                source="workers.watchdog",
                                params={"worker_id": worker_id},
                            ))
        except Exception as e:
            logger.error(f"[Watchdog] Unexpected error: {str(e)}", exc_info=True)
        await asyncio.sleep(interval)
