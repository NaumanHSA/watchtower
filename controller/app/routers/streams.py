from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Dict, Any
from ..db import connect
from ..repositories.streams_repo import StreamsRepo
from ..repositories.workers_repo import WorkersRepo
from ..services.assignments import AssignmentService
from ..schemas.streams import AssignStreamIn, AssignStreamOut, StreamOut, UpdateStreamIn, WorkerStreamActionOut
from ..security import require_worker_api_key, require_watchtower_token
from ..utils import enforce_idempotency

router = APIRouter(tags=["stream"])

@router.get("/dashboard/stream", response_model=List[StreamOut], dependencies=[Depends(require_worker_api_key)])
async def list_streams(request: Request, worker_id: str | None = None, status: str | None = None):
    # get token from request
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = await connect()
    repo = StreamsRepo(db)
    return await repo.list(worker_id=worker_id, status=status, watchtower_token=watchtower_token)

@router.get("/dashboard/stream/{stream_id}", response_model=StreamOut, dependencies=[Depends(require_worker_api_key)])
async def get_stream(request: Request, stream_id: str):
    # get token from request
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = await connect()
    repo = StreamsRepo(db)
    return await repo.get(stream_id, watchtower_token=watchtower_token)

# Assign stream to worker. If worker_id is not specified, choose the first available worker with room otherwise use the specified worker_id
@router.post("/dashboard/stream/assign", response_model=AssignStreamOut, dependencies=[Depends(require_worker_api_key), Depends(enforce_idempotency)])
async def assign_stream(request: Request, payload: AssignStreamIn):
    # get token from request
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = await connect()
    wrepo = WorkersRepo(db)
    srepo = StreamsRepo(db)
    
    worker_id = payload.worker_id
    if not worker_id:
        workers: List[Dict[str, Any]] = await wrepo.list(status="online")
        # Filter workers that have room
        eligible = []
        for w in workers:
            max_allowed = w.get("capabilities", {}).get("max_allowed_streams")
            assigned = w.get("assigned_stream_count", 0)
            if max_allowed is not None and assigned < max_allowed:
                eligible.append(w)

        if not eligible:
            raise HTTPException(status_code=503, detail="No workers available to handle new stream")
        # Pick the first eligible worker (simple strategy)
        worker_id = eligible[0]["worker_id"]
        
    service = AssignmentService(wrepo, srepo)
    return await service.assign_stream(worker_id, payload, watchtower_token=watchtower_token)

# Delete stream
@router.delete("/dashboard/stream/{stream_id}", response_model=WorkerStreamActionOut, dependencies=[Depends(require_worker_api_key), Depends(enforce_idempotency)])
async def delete_stream(request: Request, stream_id: str):
    # get token from request
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = await connect()
    service = AssignmentService(WorkersRepo(db), StreamsRepo(db))
    res = await service.remove_stream(stream_id)
    return {"ok": True, "details": res}

# Stop stream
@router.post("/dashboard/stream/{stream_id}/stop", response_model=WorkerStreamActionOut, dependencies=[Depends(require_worker_api_key), Depends(enforce_idempotency)])
async def stop_stream(request: Request, stream_id: str):
    # get token from request
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = await connect()
    service = AssignmentService(WorkersRepo(db), StreamsRepo(db))
    try:
        res = await service.stop_stream(stream_id, watchtower_token=watchtower_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "details": res}

# Start stream
@router.post("/dashboard/stream/{stream_id}/start", response_model=WorkerStreamActionOut, dependencies=[Depends(require_worker_api_key), Depends(enforce_idempotency)])
async def start_stream(request: Request, stream_id: str):
    # get token from request
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = await connect()
    service = AssignmentService(WorkersRepo(db), StreamsRepo(db))
    try:
        res = await service.start_stream(stream_id, watchtower_token=watchtower_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "details": res}

# Update stream
@router.patch("/dashboard/stream/{stream_id}", response_model=WorkerStreamActionOut, dependencies=[Depends(require_worker_api_key), Depends(enforce_idempotency)])
async def update_stream(request: Request, stream_id: str, payload: UpdateStreamIn):
    # get token from request
    watchtower_token = request.headers.get("watchtower-token")
    if not watchtower_token:
        raise HTTPException(status_code=401, detail="Missing token")
    db = await connect()
    service = AssignmentService(WorkersRepo(db), StreamsRepo(db))
    try:
        res = await service.update_stream(stream_id, payload, watchtower_token=watchtower_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "details": res}