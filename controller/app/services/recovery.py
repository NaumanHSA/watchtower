import asyncio
import logging
from ..db import connect
from ..repositories.workers_repo import WorkersRepo
from ..repositories.streams_repo import StreamsRepo
from ..enums import StreamStatus
from ..services.assignments import AssignmentService
from ..schemas.streams import AssignStreamIn
from ..services.notifications import manager
from ..services.events import evt
from ..enums import StreamEvent, StreamSeverity

logger = logging.getLogger(__name__)
async def recovery_job(interval: int = 30):
    """
    Background loop that monitors dangling/error streams and reassigns them.
    Runs every `interval` seconds.
    """
    while True:
        try:
            db = await connect()
            srepo = StreamsRepo(db)
            wrepo = WorkersRepo(db)
            service = AssignmentService(wrepo, srepo)
            # 1. Collect dangling/error streams
            streams = await srepo.list(status={"$in": [StreamStatus.DANGLING.value, StreamStatus.FAILED.value]})
            
            if streams:
                stream_ids = [stream['stream_id'] for stream in streams]
                logger.info(f"Found {len(streams)} streams to recover: {stream_ids}")
                
                # 2. Collect available workers
                workers = await wrepo.list(status="online")
                available = []
                for w in workers:
                    max_allowed = w.get("capabilities", {}).get("max_allowed_streams", 0)
                    assigned = w.get("assigned_stream_count", 0)
                    if assigned < max_allowed:
                        available.append(w)

                # 3. Try to reassign streams
                for stream in streams:
                    if not available:
                        logger.warning(f"No available workers for stream {stream['stream_id']}")
                        break
                        
                    # choose the one with the least assigned streams
                    chosen = min(
                        available,
                        key=lambda w: w["assigned_stream_count"] / w["capabilities"]["max_allowed_streams"]
                    )
                    stream_id = stream['stream_id']
                    worker_id = chosen['worker_id']
                    try:
                        logger.info(f"Reassigning stream {stream_id} to worker {worker_id}")
                        payload = AssignStreamIn(
                            stream_id=stream_id,
                            stream_name=stream["stream_name"],
                            stream_location=stream["stream_location"],
                            source_url=stream["source_url"],
                            stream_metadata=stream["stream_metadata"],
                            worker_id=worker_id,
                        )
                        await service.assign_stream(worker_id=worker_id, payload=payload)
                        logger.info(f"Stream {stream_id} reassigned to worker {worker_id}")
                        # notify clients
                        await manager.broadcast(evt(
                            name=StreamEvent.STREAM_ASSIGNED,
                            severity=StreamSeverity.NOTICE,
                            source="workers.recovery",
                            params={"stream_id": stream_id, "worker_id": worker_id},
                        ))
                    except Exception as e:
                        error_msg = str(e)
                        logger.error(f"Reassignment failed for stream {stream_id} to worker {worker_id}: {error_msg}")
                        await srepo.update(stream_id, {"status": StreamStatus.FAILED.value})
        except Exception as e:
            logger.error(f"Recovery loop error: {e}")
        await asyncio.sleep(interval)

