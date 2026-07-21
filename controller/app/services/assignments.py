import urllib.parse
import uuid
import logging
import traceback
from typing import Dict, Any, Optional
from fastapi import HTTPException, status
from ..repositories.workers_repo import WorkersRepo
from ..repositories.streams_repo import StreamsRepo
from ..services.worker_adapter import WorkerAdapter
from ..schemas.streams import UpdateStreamIn, AssignStreamIn
from ..enums import StreamStatus

logger = logging.getLogger(__name__)


class AssignmentService:
    def __init__(self, workers: WorkersRepo, streams: StreamsRepo):
        self.workers = workers
        self.streams = streams

    async def assign_stream(self, worker_id: str, payload: AssignStreamIn, watchtower_token: Optional[str] = None) -> Dict[str, Any]:
        worker = await self.workers.get(worker_id)
        if not worker:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
        if worker["status"] == "offline":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Worker offline")

        # 🚨 Check for existing stream by source_url
        existing = await self.streams.get_by_source(payload.source_url)

        # check if stream is already assigned to some worker and is running
        if existing and existing["status"] == StreamStatus.RUNNING.value:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stream already exists and is running...")

        # if it's a new stream, first create it
        if not existing:
            # Pre-create stream record
            stream_id = str(uuid.uuid4())
            await self.streams.create({
                "stream_id": stream_id,
                "watchtower_token": watchtower_token,
                "stream_name": payload.stream_name,
                "stream_location": payload.stream_location,
                "source_url": str(payload.source_url),   # ensure string
                "worker_id": worker_id,
                "status": StreamStatus.ASSIGNING.value,  # store enum as str
                "stream_metadata": payload.stream_metadata or {},
            })
        else:
            stream_id = existing["stream_id"]

        # Call worker
        adapter = WorkerAdapter(worker["worker_url"])
        try:
            res = await adapter.start_stream(
                stream_id=stream_id,
                source_url=str(payload.source_url),
                worker_id=worker_id,
            )
            # webrtc_url = res.get("webrtc_url")
            webrtc_url = urllib.parse.urljoin(worker["webrtc_url"], stream_id)
            await self.streams.update(stream_id, {"worker_id": worker_id, "status": StreamStatus.RUNNING.value, "webrtc_url": webrtc_url, "stream_metadata": {}})
            await self.workers.inc_streams(worker_id, +1)
            return {
                "stream_id": stream_id,
                "worker_id": worker_id,
                "status": StreamStatus.RUNNING,
                "webrtc_url": webrtc_url,
            }
        except Exception as e:
            # if first time, delete the created stream, don't put it in dangling/failed
            if not existing:
                await self.streams.delete(stream_id, watchtower_token=watchtower_token)
            else:
                # update stream status to error
                await self.streams.update(
                    stream_id,
                    {"status": StreamStatus.FAILED.value, "stream_metadata": {"failed": str(e)}},
                    watchtower_token=watchtower_token
                )
            raise HTTPException(status_code=502, detail=f"Worker assignment failed: {e}")

    # 🚨 This method is used by the controller to remove streams from workers
    # It deletes the stream record from the database, as well as stops the stream on the worker
    async def remove_stream(self, stream_id: str, watchtower_token: Optional[str] = None) -> Dict[str, Any]:
        stream = await self.streams.get(stream_id, watchtower_token=watchtower_token)
        if not stream:
            raise HTTPException(status_code=404, detail="Stream not found")
        worker = await self.workers.get(stream["worker_id"])
        if not worker:
            # Worker missing: delete stream record
            await self.streams.delete(stream_id, watchtower_token=watchtower_token)
            return {"ok": True, "details": {"note": "Worker missing; stream record has been deleted"}}
        adapter = WorkerAdapter(worker["worker_url"])
        try:
            await adapter.stop_stream(stream_id)
            await self.streams.delete(stream_id, watchtower_token=watchtower_token)
        except Exception as e:
            # still proceed to delete so controller state is consistent
            await self.streams.update(stream_id, {
                    "status": StreamStatus.STOPPED.value, 
                    "worker_id": None,
                    "webrtc_url": None,
                    "stream_metadata": {"stop_error": str(e)}
                }, 
                watchtower_token=watchtower_token
            )
        await self.workers.inc_streams(worker["worker_id"], -1)
        return {"ok": True}

    async def stop_stream(self, stream_id: str, watchtower_token: Optional[str] = None) -> Dict[str, Any]:
        stream = await self.streams.get(stream_id, watchtower_token=watchtower_token)
        if not stream:
            raise HTTPException(status_code=404, detail="Stream not found")
        # check if stream is already stopped
        if stream["status"] in [StreamStatus.STOPPED.value, StreamStatus.FAILED.value, StreamStatus.DANGLING.value]:
            return {"ok": True, "details": {"note": "Stream is already stopped"}}
        worker = await self.workers.get(stream["worker_id"])
        if worker:
            adapter = WorkerAdapter(worker["worker_url"])
            try:
                await adapter.stop_stream(stream_id)
            except Exception as e:
                logger.error(f"Failed to stop stream {stream_id} on worker {worker['worker_url']}: {e}")
                # raise HTTPException(status_code=502, detail=f"Failed to stop stream {stream_id}: {e}")        
        await self.streams.update(stream_id, {
            "status": StreamStatus.STOPPED.value,
            "worker_id": None,
            "webrtc_url": None,
        })
        await self.workers.inc_streams(worker["worker_id"], -1)
        return {"ok": True}

    # 🚨 This method is used by the controller to start streams on workers
    async def start_stream(self, stream_id: str, watchtower_token: Optional[str] = None) -> Dict[str, Any]:
        stream = await self.streams.get(stream_id, watchtower_token=watchtower_token)
        if not stream:
            raise HTTPException(status_code=404, detail="Stream not found")
        # check if stream is already running
        if stream["status"] == StreamStatus.RUNNING.value:
            raise HTTPException(status_code=409, detail="Stream is already running")
        # get all workers from the db, and check for any available worker
        workers = await self.workers.list(status="online")
        # Filter workers that have room
        available = []
        for w in workers:
            max_allowed = w.get("capabilities", {}).get("max_allowed_streams")
            assigned = w.get("assigned_stream_count", 0)
            if max_allowed is not None and assigned < max_allowed:
                available.append(w)
        if not available:
            raise HTTPException(status_code=503, detail="No workers available to handle new stream")
        # Pick the first available worker (simple strategy)
        worker = available[0]
        payload = AssignStreamIn(
            stream_id=stream_id,
            stream_location=stream["stream_location"],
            stream_name=stream["stream_name"],
            source_url=str(stream["source_url"]),
            stream_metadata=stream.get("metadata", {}),
            worker_id=worker["worker_id"],
        )
        return await self.assign_stream(worker["worker_id"], payload)
    
    # 🚨 This method is used by the controller to update streams on workers
    async def update_stream(self, stream_id: str, payload: UpdateStreamIn, watchtower_token: Optional[str] = None) -> Dict[str, Any]:
        stream = await self.streams.get(stream_id, watchtower_token=watchtower_token)
        if not stream:
            raise HTTPException(status_code=404, detail="Stream not found")

        # check if stream is already running
        if stream["status"] == StreamStatus.RUNNING.value:
            raise HTTPException(status_code=409, detail="Stream is already running")

        # get all workers from the db, and check for any available worker
        workers = await self.workers.list(status="online")
        # Filter workers that have room
        available = []
        for w in workers:
            max_allowed = w.get("capabilities", {}).get("max_allowed_streams")
            assigned = w.get("assigned_stream_count", 0)
            if max_allowed is not None and assigned < max_allowed:
                available.append(w)

        if not available:
            raise HTTPException(status_code=503, detail="No workers available to handle new stream")
        # Pick the first available worker (simple strategy)
        worker = available[0]
        payload = AssignStreamIn(
            stream_id=stream_id,
            stream_location=stream["stream_location"],
            stream_name=stream["stream_name"],
            source_url=str(stream["source_url"]),
            stream_metadata=stream.get("metadata", {}),
            worker_id=worker["worker_id"],
        )
        return await self.assign_stream(worker["worker_id"], payload)
