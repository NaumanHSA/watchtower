from fastapi import APIRouter, Depends
from ..db import connect
from ..security import require_worker_api_key, require_watchtower_token

router = APIRouter(prefix="/dashboard/stats", tags=["stats"])

@router.get("", dependencies=[Depends(require_worker_api_key), Depends(require_watchtower_token)])
async def stats(_=Depends(require_worker_api_key)):
    db = await connect()
    workers = await db.workers.count_documents({})
    workers_online = await db.workers.count_documents({"status": "online"})
    streams_running = await db.streams.count_documents({"status": "running"})
    streams_total = await db.streams.count_documents({})
    return {
        "workers": {"total": workers, "online": workers_online},
        "streams": {"total": streams_total, "running": streams_running}
    }
