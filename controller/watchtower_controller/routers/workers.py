from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.asynchronous.database import AsyncDatabase

from watchtower_contracts import (
    PROTOCOL_VERSION,
    DesiredState,
    DesiredStream,
    ObservedStatus,
    RegisterRequest,
    RegisterResponse,
    ResultKey,
    Role,
    StreamConfig,
    StreamStatus,
    SyncRequest,
    SyncResponse,
    WorkerState,
)

from ..audit import Auditor
from ..auth import Principal
from ..deps import (
    WorkerAuth,
    authenticate,
    authenticate_worker,
    bearer_token,
    client_address,
    get_auditor,
    get_db,
    get_notifier,
    get_provider,
    get_scheduler,
    get_settings,
    hash_secret,
    require,
)
from ..events import Notifier
from ..scheduler import Scheduler
from ..schemas import DrainRequest, WorkerOut
from ..settings import Settings

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])

OBSERVED_TO_STATUS = {
    ObservedStatus.STARTING: StreamStatus.STARTING,
    ObservedStatus.RUNNING: StreamStatus.RUNNING,
    ObservedStatus.RECONNECTING: StreamStatus.RECONNECTING,
    ObservedStatus.ERROR: StreamStatus.ERROR,
}


def _result_key(principal: Principal | None) -> dict | None:
    if principal is None or not principal.public_key_pem:
        return None
    return {"kid": principal.kid, "public_key_pem": principal.public_key_pem}


# ------------------------------------------------------------------ worker-facing

@router.post("/register", response_model=RegisterResponse)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
    notifier: Notifier = Depends(get_notifier),
    scheduler: Scheduler = Depends(get_scheduler),
    provider=Depends(get_provider),
    auditor: Auditor = Depends(get_auditor),
) -> RegisterResponse:
    """Called by a worker on first start (or when its secret was lost). Requires a token
    with the `worker` role (or `admin`). Returns a per-worker secret for /sync."""
    principal = await authenticate(provider, bearer_token(request), Role.WORKER,
                                   limiter=request.app.state.limiter, client=client_address(request))
    if body.protocol > PROTOCOL_VERSION:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Worker speaks protocol {body.protocol}; this controller supports {PROTOCOL_VERSION}")
    worker_id = body.worker_id or f"w-{secrets.token_hex(6)}"
    secret = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    await db.workers.update_one(
        {"worker_id": worker_id},
        {
            "$set": {
                "name": body.name,
                "version": body.version,
                "capacity": body.capacity.model_dump(),
                "webrtc_base_url": body.webrtc_base_url,
                "host_info": body.host_info,
                "secret_hash": hash_secret(secret),
                "token_fingerprint": principal.token_fingerprint,
                "token_subject": principal.subject,
                "result_key": _result_key(principal),
                "state": WorkerState.ONLINE,
                "last_seen": now,
                "updated_at": now,
            },
            "$setOnInsert": {"worker_id": worker_id, "registered_at": now, "draining": False},
        },
        upsert=True,
    )
    await auditor.record(principal, "worker.register", worker_id, name=body.name, version=body.version)
    await notifier.publish("worker.registered", "notice", worker_id=worker_id, name=body.name)
    scheduler.wake()
    return RegisterResponse(worker_id=worker_id, worker_secret=secret,
                            sync_interval_ms=settings.sync_interval_ms, lease_seconds=settings.lease_seconds)


@router.post("/{worker_id}/sync", response_model=SyncResponse)
async def sync(
    body: SyncRequest,
    auth: WorkerAuth = Depends(authenticate_worker),
    db: AsyncDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
    notifier: Notifier = Depends(get_notifier),
    scheduler: Scheduler = Depends(get_scheduler),
) -> SyncResponse:
    """The worker reports what it runs and gets back what it should run."""
    worker = auth.worker
    worker_id = worker["worker_id"]
    now = datetime.now(UTC)

    update = {
        "state": WorkerState.ONLINE,
        "last_seen": now,
        "updated_at": now,
        "version": body.version,
        "capacity": body.capacity.model_dump(),
        "load": body.load.model_dump(),
        "draining_reported": body.draining,
        "delivery": body.delivery.model_dump() if body.delivery else None,
    }
    if auth.principal is not None:  # token re-verified: pick up key rotation
        update["result_key"] = _result_key(auth.principal)
        update["token_fingerprint"] = auth.principal.token_fingerprint
    await db.workers.update_one({"worker_id": worker_id}, {"$set": update})
    if worker.get("state") != WorkerState.ONLINE:
        await notifier.publish("worker.online", "notice", worker_id=worker_id)
        scheduler.wake()
    if body.draining and not worker.get("draining_reported"):
        scheduler.wake()

    for observed in body.streams:
        new_status = OBSERVED_TO_STATUS[observed.status]
        changes: dict = {
            "status": new_status,
            "observed": {**observed.model_dump(exclude={"stream_id", "generation"}), "at": now},
            "updated_at": now,
        }
        if new_status == StreamStatus.RUNNING:
            changes.update(attempts=0, last_error=None, next_retry_at=None)
        before = await db.streams.find_one_and_update(
            # generation guards against late reports for an assignment that moved on
            {"stream_id": observed.stream_id, "worker_id": worker_id, "generation": observed.generation,
             "desired_state": DesiredState.RUNNING},
            {"$set": changes},
            projection={"status": 1},
        )
        if before and before["status"] != new_status:
            severity = "error" if new_status == StreamStatus.ERROR else "info"
            await notifier.publish("stream.status", severity, stream_id=observed.stream_id, worker_id=worker_id,
                                   status=new_status, error=observed.error)
            if new_status == StreamStatus.ERROR:
                scheduler.wake()

    desired = [
        DesiredStream(stream_id=s["stream_id"], generation=s["generation"], name=s["name"],
                      source_url=s["source_url"], config=StreamConfig.model_validate(s.get("config") or {}))
        async for s in db.streams.find({"worker_id": worker_id, "desired_state": DesiredState.RUNNING})
    ]
    key = update.get("result_key", worker.get("result_key"))
    return SyncResponse(
        desired=desired,
        result_key=ResultKey.model_validate(key) if key else None,
        results_url=settings.results_url,
        sync_interval_ms=settings.sync_interval_ms,
        lease_seconds=settings.lease_seconds,
    )


# ------------------------------------------------------------------ dashboard

async def _assigned_counts(db: AsyncDatabase) -> dict[str, int]:
    counts: dict[str, int] = {}
    async for row in await db.streams.aggregate([
        {"$match": {"worker_id": {"$ne": None}, "desired_state": DesiredState.RUNNING}},
        {"$group": {"_id": "$worker_id", "n": {"$sum": 1}}},
    ]):
        counts[row["_id"]] = row["n"]
    return counts


@router.get("", response_model=list[WorkerOut], dependencies=[Depends(require(Role.VIEWER))])
async def list_workers(state: WorkerState | None = None, db: AsyncDatabase = Depends(get_db)) -> list[WorkerOut]:
    counts = await _assigned_counts(db)
    query = {"state": state} if state else {}
    return [WorkerOut.from_doc(w, counts.get(w["worker_id"], 0))
            async for w in db.workers.find(query).sort("registered_at", 1)]


@router.get("/{worker_id}", response_model=WorkerOut, dependencies=[Depends(require(Role.VIEWER))])
async def get_worker(worker_id: str, db: AsyncDatabase = Depends(get_db)) -> WorkerOut:
    worker = await db.workers.find_one({"worker_id": worker_id})
    if not worker:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    counts = await _assigned_counts(db)
    return WorkerOut.from_doc(worker, counts.get(worker_id, 0))


@router.post("/{worker_id}/drain", response_model=WorkerOut)
async def drain_worker(
    worker_id: str,
    body: DrainRequest,
    principal: Principal = Depends(require(Role.OPERATOR)),
    auditor: Auditor = Depends(get_auditor),
    db: AsyncDatabase = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
    scheduler: Scheduler = Depends(get_scheduler),
) -> WorkerOut:
    """Draining moves the worker's streams elsewhere and keeps new ones off it."""
    worker = await db.workers.find_one_and_update(
        {"worker_id": worker_id}, {"$set": {"draining": body.draining}}, return_document=True
    )
    if not worker:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    await auditor.record(principal, "worker.drain" if body.draining else "worker.undrain", worker_id)
    await notifier.publish("worker.draining" if body.draining else "worker.undrained", "notice", worker_id=worker_id)
    scheduler.wake()
    counts = await _assigned_counts(db)
    return WorkerOut.from_doc(worker, counts.get(worker_id, 0))


@router.delete("/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_worker(
    worker_id: str,
    force: bool = False,
    principal: Principal = Depends(require(Role.ADMIN)),
    auditor: Auditor = Depends(get_auditor),
    db: AsyncDatabase = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
    scheduler: Scheduler = Depends(get_scheduler),
) -> None:
    """Forget a worker. Its streams move elsewhere. An online worker can register again
    with a valid token, so revoke its token first to keep it out."""
    worker = await db.workers.find_one({"worker_id": worker_id})
    if not worker:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    if worker.get("state") == WorkerState.ONLINE and not force:
        raise HTTPException(status.HTTP_409_CONFLICT, "Worker is online; drain it first or pass force=true")
    await db.workers.delete_one({"worker_id": worker_id})
    await auditor.record(principal, "worker.delete", worker_id, force=force)
    await notifier.publish("worker.deleted", "notice", worker_id=worker_id)
    scheduler.wake()
