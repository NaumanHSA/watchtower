from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import ValidationError
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from watchtower_contracts import DesiredState, Role, StreamConfig, StreamStatus

from ..audit import Auditor
from ..auth import Principal
from ..deps import get_auditor, get_db, get_notifier, get_scheduler, require
from ..events import Notifier
from ..scheduler import Scheduler
from ..schemas import StreamCreate, StreamOut, StreamUpdate, StreamView, mask_url

router = APIRouter(prefix="/api/v1/streams", tags=["streams"])


async def _get_or_404(db: AsyncDatabase, stream_id: str) -> dict[str, Any]:
    doc = await db.streams.find_one({"stream_id": stream_id})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    return doc


@router.get("", response_model=list[StreamOut], dependencies=[Depends(require(Role.VIEWER))])
async def list_streams(
    status_: StreamStatus | None = Query(default=None, alias="status"),
    worker_id: str | None = None,
    db: AsyncDatabase = Depends(get_db),
) -> list[StreamOut]:
    query: dict[str, Any] = {}
    if status_:
        query["status"] = status_
    if worker_id:
        query["worker_id"] = worker_id
    return [StreamOut.from_doc(d) async for d in db.streams.find(query).sort("created_at", 1)]


@router.get("/{stream_id}", response_model=StreamOut, dependencies=[Depends(require(Role.VIEWER))])
async def get_stream(stream_id: str, db: AsyncDatabase = Depends(get_db)) -> StreamOut:
    return StreamOut.from_doc(await _get_or_404(db, stream_id))


@router.post("", response_model=StreamOut, status_code=status.HTTP_201_CREATED)
async def create_stream(
    body: StreamCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=200),
    principal: Principal = Depends(require(Role.OPERATOR)),
    auditor: Auditor = Depends(get_auditor),
    db: AsyncDatabase = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
    scheduler: Scheduler = Depends(get_scheduler),
) -> StreamOut:
    """Add a camera. With `start` (default) it is placed on a worker within seconds.
    Send an `Idempotency-Key` header to make retries safe."""
    now = datetime.now(UTC)
    if idempotency_key:
        try:
            await db.idempotency.insert_one({"key": f"create-stream:{idempotency_key}", "created_at": now,
                                             "stream_id": None})
        except DuplicateKeyError:
            previous = await db.idempotency.find_one({"key": f"create-stream:{idempotency_key}"})
            if previous and previous.get("stream_id"):
                doc = await db.streams.find_one({"stream_id": previous["stream_id"]})
                if doc:
                    response.status_code = status.HTTP_200_OK
                    return StreamOut.from_doc(doc)
                raise HTTPException(status.HTTP_409_CONFLICT, "This Idempotency-Key created a stream that has "
                                    "since been deleted; use a new key to create another") from None
            raise HTTPException(status.HTTP_409_CONFLICT, "A request with this Idempotency-Key is in progress") \
                from None

    doc = {
        "stream_id": str(uuid.uuid4()),
        "name": body.name,
        "location": body.location,
        "source_url": body.source_url,
        "metadata": body.metadata,
        "config": body.config.model_dump(mode="json"),
        "desired_state": DesiredState.RUNNING if body.start else DesiredState.STOPPED,
        "status": StreamStatus.PENDING if body.start else StreamStatus.STOPPED,
        "worker_id": None,
        "generation": 0,
        "observed": None,
        "attempts": 0,
        "last_error": None,
        "next_retry_at": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.streams.insert_one(doc)
    except BaseException as e:
        if idempotency_key:  # free the key so the client can retry
            await db.idempotency.delete_one({"key": f"create-stream:{idempotency_key}"})
        if isinstance(e, DuplicateKeyError):
            raise HTTPException(status.HTTP_409_CONFLICT, "A stream with this source_url already exists") from None
        raise
    if idempotency_key:
        await db.idempotency.update_one({"key": f"create-stream:{idempotency_key}"},
                                        {"$set": {"stream_id": doc["stream_id"]}})
    await auditor.record(principal, "stream.create", doc["stream_id"], name=body.name,
                         source_url=mask_url(body.source_url), start=body.start)
    await notifier.publish("stream.created", stream_id=doc["stream_id"], name=body.name)
    scheduler.wake()
    return StreamOut.from_doc(doc)


@router.patch("/{stream_id}", response_model=StreamOut)
async def update_stream(
    stream_id: str,
    body: StreamUpdate,
    principal: Principal = Depends(require(Role.OPERATOR)),
    auditor: Auditor = Depends(get_auditor),
    db: AsyncDatabase = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
) -> StreamOut:
    """Partial update. Changing source_url or config restarts the stream on its worker."""
    current = await _get_or_404(db, stream_id)
    changes: dict[str, Any] = {}
    for field in ("name", "location", "metadata", "source_url"):
        value = getattr(body, field)
        if value is not None and value != current.get(field):
            changes[field] = value
    if body.config is not None:
        try:
            merged = StreamConfig.model_validate({**(current.get("config") or {}), **body.config})
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False)) from None
        merged_doc = merged.model_dump(mode="json")
        if merged_doc != current.get("config"):
            changes["config"] = merged_doc
    if not changes:
        return StreamOut.from_doc(current)

    update: dict[str, Any] = {"$set": {**changes, "updated_at": datetime.now(UTC)}}
    if "source_url" in changes or "config" in changes:
        update["$inc"] = {"generation": 1}  # a new generation makes the worker restart the engine
    try:
        doc = await db.streams.find_one_and_update({"stream_id": stream_id}, update,
                                                   return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A stream with this source_url already exists") from None
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    await auditor.record(principal, "stream.update", stream_id, fields=sorted(changes))
    await notifier.publish("stream.updated", stream_id=stream_id, fields=sorted(changes))
    return StreamOut.from_doc(doc)


@router.post("/{stream_id}/start", response_model=StreamOut)
async def start_stream(
    stream_id: str,
    principal: Principal = Depends(require(Role.OPERATOR)),
    auditor: Auditor = Depends(get_auditor),
    db: AsyncDatabase = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
    scheduler: Scheduler = Depends(get_scheduler),
) -> StreamOut:
    """Start a stopped stream, or retry a failed one (resets its attempts)."""
    current = await _get_or_404(db, stream_id)
    if current["desired_state"] == DesiredState.RUNNING and current["status"] != StreamStatus.FAILED:
        return StreamOut.from_doc(current)
    doc = await db.streams.find_one_and_update(
        {"stream_id": stream_id},
        {"$set": {"desired_state": DesiredState.RUNNING, "status": StreamStatus.PENDING, "worker_id": None,
                  "observed": None, "attempts": 0, "last_error": None, "next_retry_at": None,
                  "updated_at": datetime.now(UTC)},
         "$inc": {"generation": 1}},
        return_document=ReturnDocument.AFTER,
    )
    await auditor.record(principal, "stream.start", stream_id)
    await notifier.publish("stream.started", stream_id=stream_id)
    scheduler.wake()
    return StreamOut.from_doc(doc)


@router.post("/{stream_id}/stop", response_model=StreamOut)
async def stop_stream(
    stream_id: str,
    principal: Principal = Depends(require(Role.OPERATOR)),
    auditor: Auditor = Depends(get_auditor),
    db: AsyncDatabase = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
) -> StreamOut:
    """The worker stops the stream on its next sync."""
    current = await _get_or_404(db, stream_id)
    if current["desired_state"] == DesiredState.STOPPED:
        return StreamOut.from_doc(current)
    doc = await db.streams.find_one_and_update(
        {"stream_id": stream_id},
        {"$set": {"desired_state": DesiredState.STOPPED, "status": StreamStatus.STOPPED, "worker_id": None,
                  "observed": None, "updated_at": datetime.now(UTC)},
         "$inc": {"generation": 1}},
        return_document=ReturnDocument.AFTER,
    )
    await auditor.record(principal, "stream.stop", stream_id)
    await notifier.publish("stream.stopped", stream_id=stream_id)
    return StreamOut.from_doc(doc)


@router.delete("/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(
    stream_id: str,
    principal: Principal = Depends(require(Role.OPERATOR)),
    auditor: Auditor = Depends(get_auditor),
    db: AsyncDatabase = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
) -> None:
    result = await db.streams.delete_one({"stream_id": stream_id})
    if not result.deleted_count:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    await auditor.record(principal, "stream.delete", stream_id)
    await notifier.publish("stream.deleted", stream_id=stream_id)


@router.get("/{stream_id}/view", response_model=StreamView, dependencies=[Depends(require(Role.VIEWER))])
async def view_stream(stream_id: str, db: AsyncDatabase = Depends(get_db)) -> StreamView:
    """Where to watch the stream live (WebRTC via the worker's MediaMTX)."""
    stream = await _get_or_404(db, stream_id)
    base = None
    if stream.get("worker_id") and stream["status"] in (StreamStatus.RUNNING, StreamStatus.RECONNECTING):
        worker = await db.workers.find_one({"worker_id": stream["worker_id"]}, {"webrtc_base_url": 1})
        base = (worker or {}).get("webrtc_base_url")
    if not base:
        return StreamView(stream_id=stream_id, status=stream["status"], webrtc_url=None, whep_url=None)
    base = base.rstrip("/")
    return StreamView(stream_id=stream_id, status=stream["status"],
                      webrtc_url=f"{base}/{stream_id}/", whep_url=f"{base}/{stream_id}/whep")
