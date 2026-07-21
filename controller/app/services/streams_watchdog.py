import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from ..db import connect
from ..repositories.workers_repo import WorkersRepo
from ..repositories.streams_repo import StreamsRepo
from ..services.notifications import manager
from ..services.events import evt
from ..enums import StreamEvent, StreamSeverity, StreamStatus

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {
    StreamStatus.RUNNING.value,
    StreamStatus.ASSIGNING.value,
}

def _should_mark_dangling(s: Dict[str, Any], existing_workers: Dict[str, Dict[str, Any]]) -> bool:
    wid = s.get("worker_id")
    status = (s.get("status") or "").lower()
    # Case 1: worker_id present but that worker doesn't exist
    if wid and wid not in existing_workers:
        return True
    # Case 2: no worker_id, but stream still looks "active" (status or webrtc_url)
    if not wid and (status in ACTIVE_STATUSES):
        return True
    return False

async def streams_watchdog_job(interval: int = 30):
    """
    Periodically scan streams and mark as DANGLING when:
      - worker_id is set but the worker isn't registered, OR
      - worker_id is null while the stream still appears active (status running/assigning).
    When dangling:
      - clear worker_id, webrtc_url
      - set status=DANGLING
      - bump updated_at
      - broadcast STREAM_DANGLING
    """
    while True:
        try:
            db = await connect()
            srepo = StreamsRepo(db)
            wrepo = WorkersRepo(db)

            streams: List[Dict[str, Any]] = await srepo.list()

            if not streams:
                await asyncio.sleep(interval)
                continue

            # Build worker cache
            worker_ids = {s["worker_id"] for s in streams if s.get("worker_id")}
            existing_workers: Dict[str, Dict[str, Any]] = {}
            for wid in worker_ids:
                try:
                    w = await wrepo.get(wid)
                    if w:
                        existing_workers[wid] = w
                except Exception:
                    # treat lookup errors as "not found"
                    pass

            # Decide which streams to dangle
            candidates = [s for s in streams if _should_mark_dangling(s, existing_workers)]
            if not candidates:
                await asyncio.sleep(interval)
                continue

            logger.info(f"[StreamsWatchdog] Found {len(candidates)} streams to mark as DANGLING")

            now_iso = datetime.now(timezone.utc).isoformat()
            for s in candidates:
                sid = s["stream_id"]
                prev_status = s.get("status")
                prev_worker = s.get("worker_id")
                # idempotency: skip if already dangling and already cleared
                if (prev_status == StreamStatus.DANGLING.value) and not s.get("worker_id") and not s.get("webrtc_url"):
                    continue

                patch: Dict[str, Any] = {
                    "worker_id": None,
                    "webrtc_url": None,
                    "status": StreamStatus.DANGLING.value,
                    "updated_at": now_iso,
                }
                try:
                    await srepo.update(sid, patch)
                    await manager.broadcast(evt(
                        name=StreamEvent.STREAM_DANGLING,
                        severity=StreamSeverity.NOTICE,
                        source="streams.watchdog",
                        params={
                            "stream_id": sid,
                            "previous_worker_id": prev_worker,
                            "previous_status": prev_status,
                        },
                    ))
                    logger.info(f"[StreamsWatchdog] Stream {sid} → DANGLING (prev_worker={prev_worker}, prev_status={prev_status})")
                except Exception as e:
                    logger.error(f"[StreamsWatchdog] Failed to mark stream {sid} as dangling: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"[StreamsWatchdog] Unexpected error: {e}", exc_info=True)

        await asyncio.sleep(interval)
