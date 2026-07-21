from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import List, Dict, Any
from datetime import datetime, timezone
import logging
from ..db import connect
from ..repositories.workers_repo import WorkersRepo
from ..repositories.streams_repo import StreamsRepo
from ..schemas.workers import WorkerRegisterIn, WorkerOut, WorkerPatchHeartbeat, WorkerActionOut
from ..schemas.common import Message
from ..security import require_worker_api_key, require_watchtower_token
from ..enums import StreamStatus
from ..services.notifications import manager
from ..services.events import evt
from ..enums import StreamEvent, StreamSeverity
import requests
import urllib.parse

router = APIRouter(tags=["worker"])
logger = logging.getLogger(__name__)

# worker will call this endpoint on initialization to let controller know of its existance
@router.post("/internal/worker/go_live", response_model=WorkerActionOut, dependencies=[Depends(require_worker_api_key)])
async def go_live(payload: WorkerRegisterIn, request: Request, auths: tuple[str] = Depends(require_watchtower_token)):
    db = await connect()
    watchtower_token, public_key_pem = auths
    repo = WorkersRepo(db)

    worker_url_base = str(payload.worker_url)
    webrtc_url_base  = str(payload.web_rtc_url)
    # print the params
    logger.info(f"\n=== WORKER ANNOUNCEMENT OCCURRED from url: {worker_url_base[:10]}... with:")
    logger.info(f"worker_id: {payload.worker_id[:6]}...")
    logger.info(f"webrtc_url_base: {webrtc_url_base[:10]}...")
    logger.info("=========================================\n")
    
    # check by worker url if the worker is already registered
    doc = await repo.get_by_url(worker_url_base)
    if doc:
        return WorkerActionOut(
            ok=False, 
            code=status.HTTP_409_CONFLICT, 
            message="Worker has been registered already with this URL", 
            worker_id=doc["worker_id"], 
            public_key_pem=doc["public_key_pem"]
        )

    doc = await repo.upsert_worker({
        "worker_id": payload.worker_id,
        "watchtower_token": watchtower_token,
        "public_key_pem": public_key_pem,
        "worker_url": worker_url_base,
        "worker_health_url": urllib.parse.urljoin(worker_url_base, "/health"),
        "webrtc_url": webrtc_url_base,
        "controller_url": str(payload.controller_url),
        "status": "online",
        "capabilities": payload.capabilities,
        "host_info": payload.host_info,
        "assigned_stream_count": 0,
    })
    # after upsert_worker
    await manager.broadcast(evt(
        StreamEvent.WORKER_REGISTERED,
        severity=StreamSeverity.NOTICE,
        source="workers.go_live",
    ))
    return WorkerActionOut(ok=True, code=status.HTTP_200_OK, message="Worker registered successfully", worker_id=doc["worker_id"], public_key_pem=doc["public_key_pem"])

# worker will periodically call this endpoint to let controller know of its status
@router.patch("/internal/worker/heartbeat/{worker_id}", response_model=WorkerActionOut, dependencies=[Depends(require_worker_api_key)])
async def heartbeat(request: Request, worker_id: str, patch: WorkerPatchHeartbeat):
    db = await connect()
    wrepo = WorkersRepo(db)
    srepo = StreamsRepo(db)

    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing watchtower token")

    current = await wrepo.get(worker_id, watchtower_token=watchtower_token)
    if not current:
        raise HTTPException(status_code=404, detail="Controller is saying that it doesn't know about this worker")

    # 1. Update worker last_seen + status
    payload_dict = patch.model_dump(exclude_none=True)
    payload_dict.pop("streams", None)
    await wrepo.heartbeat(worker_id, payload_dict, watchtower_token=watchtower_token)

    # 2. Get streams assigned to this worker
    db_streams = await srepo.list(worker_id=worker_id)
    db_running_stream_ids = {s["stream_id"] for s in db_streams if s["status"] == StreamStatus.RUNNING.value}

    # 3. Reported from worker
    reported = patch.model_dump().get("streams", [])
    reported_ids = {s["id"] for s in reported}

    # 4. Streams missing from report → mark as dangling
    missing = db_running_stream_ids - reported_ids
    for stream_id in missing:
        now_iso = datetime.now(timezone.utc).isoformat()
        patch_dangling: Dict[str, Any] = {
            "worker_id": None,
            "webrtc_url": None,
            "status": StreamStatus.DANGLING.value,
            "updated_at": now_iso,
        }
        await srepo.update(stream_id, patch_dangling)

    # 5. Update number of streams assigned to worker
    await wrepo.update(worker_id, {"assigned_stream_count": len(reported)}, watchtower_token=watchtower_token)

    # 6. Streams reported → update status if necessary
    for r in reported:
        sid = r["id"]
        if sid in db_running_stream_ids:
            await srepo.update(sid, {"status": r.get("status", StreamStatus.RUNNING.value)})

    # 7. Notify clients about missing streams marked as dangling:
    for sid in missing:
        await manager.broadcast(evt(
            name=StreamEvent.STREAM_DANGLING,
            severity=StreamSeverity.NOTICE,
            source="workers.heartbeat",
            params={"stream_id": sid, "worker_id": worker_id},
        ))
    return WorkerActionOut(ok=True, message="Heartbeat processed", details={"missing_streams": list(missing)})

# admin will call this endpoint to list all workers
@router.get("/dashboard/workers", response_model=List[WorkerOut], dependencies=[Depends(require_worker_api_key)])
async def list_workers(request: Request, status: str | None = None):
    db = await connect()
    repo = WorkersRepo(db)
    # get token from the headers
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing watchtower token")
    # return list of workers with the same token
    return await repo.list(status=status, watchtower_token=watchtower_token)

@router.get("/dashboard/workers/{worker_id}", response_model=WorkerOut, dependencies=[Depends(require_worker_api_key)])
async def get_worker(request: Request, worker_id: str):
    # get token from the headers
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing watchtower token")
    db = await connect()
    repo = WorkersRepo(db)
    doc = await repo.get(worker_id, watchtower_token=watchtower_token)
    if not doc:
        raise HTTPException(status_code=404, detail="Worker not found")
    return doc

@router.delete("/dashboard/workers/{worker_id}", response_model=WorkerActionOut, dependencies=[Depends(require_worker_api_key)])
async def delete_worker(request: Request, worker_id: str, force: bool = False):
    """
    Deletes worker. If worker still has assigned streams, you should reassign them
    first from the dashboard. 'force=true' will allow deletion anyway, but streams
    will become orphaned and must be handled manually.
    """
    # get token from the headers
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing watchtower token")

    db = await connect()
    wrepo = WorkersRepo(db)
    doc = await wrepo.get(worker_id, watchtower_token=watchtower_token)
    if not doc:
        raise HTTPException(status_code=404, detail="Worker not found")

    if doc["assigned_stream_count"] > 0 and not force:
        raise HTTPException(status_code=409, detail="Worker has active streams; reassign or use force=true")
    
    # first check if there's any assigned streams, if so, change their status to DANGLING
    srepo = StreamsRepo(db)
    await srepo.update_many({"worker_id": worker_id}, {"$set": {"status": StreamStatus.DANGLING.value}})

    # then delete the worker
    deleted = await wrepo.delete(worker_id, watchtower_token=watchtower_token)
    if not deleted:
        raise HTTPException(status_code=500, detail="Delete failed")
    return WorkerActionOut(ok=True, message="Worker deleted")
