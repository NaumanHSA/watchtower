"""Stream placement. Runs on exactly one controller replica at a time (Mongo lease).

Only the scheduler assigns streams to workers, so placement never races. Everything
else (API calls, worker syncs) writes desired or observed state, and the scheduler
reconciles it on its next tick.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from watchtower_contracts import DesiredState, StreamStatus, WorkerState

from .db import Mongo
from .events import Notifier
from .settings import Settings

log = logging.getLogger(__name__)

LEASE_ID = "scheduler"
MAX_BACKOFF = timedelta(minutes=10)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    def __init__(self, mongo: Mongo, settings: Settings, notifier: Notifier, instance_id: str):
        self.db = mongo.db
        self.settings = settings
        self.notifier = notifier
        self.instance_id = instance_id
        self.is_leader = False
        self.leader_since: datetime | None = None
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Run the next tick now instead of waiting for the interval."""
        self._wake.set()

    async def run(self) -> None:
        interval = self.settings.scheduler_interval_seconds
        while True:
            try:
                if await self._hold_lease():
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except TimeoutError:
                pass
            self._wake.clear()

    async def release_lease(self) -> None:
        if self.is_leader:
            await self.db.leases.delete_one({"_id": LEASE_ID, "owner": self.instance_id})
            self.is_leader = False

    async def _hold_lease(self) -> bool:
        now = utcnow()
        ttl = timedelta(seconds=max(10.0, 5 * self.settings.scheduler_interval_seconds))
        try:
            await self.db.leases.find_one_and_update(
                {"_id": LEASE_ID, "$or": [{"owner": self.instance_id}, {"expires_at": {"$lt": now}}]},
                {"$set": {"owner": self.instance_id, "expires_at": now + ttl}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:  # another replica holds a live lease
            if self.is_leader:
                log.warning("lost the scheduler lease")
            self.is_leader = False
            return False
        if not self.is_leader:
            log.info("this replica is now the scheduler")
            self.is_leader = True
            self.leader_since = now
        return True

    # ------------------------------------------------------------------ tick

    async def tick(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        if self.leader_since is None:
            self.leader_since = now
        workers = {w["worker_id"]: w async for w in self.db.workers.find({})}
        await self._update_worker_states(workers, now)
        await self._release_from_unavailable_workers(workers, now)
        await self._release_stuck_streams(workers, now)
        await self._place_pending(workers, now)

    async def _update_worker_states(self, workers: dict[str, dict[str, Any]], now: datetime) -> None:
        # Right after this replica takes over, every last_seen may be stale because no
        # controller was serving syncs. Give workers one lease to check in first.
        in_grace = (now - self.leader_since).total_seconds() < self.settings.worker_offline_seconds
        for worker in workers.values():
            age = (now - worker["last_seen"]).total_seconds()
            if age <= self.settings.worker_online_seconds:
                state = WorkerState.ONLINE
            elif age <= self.settings.worker_offline_seconds or in_grace:
                state = WorkerState.SUSPECT
            else:
                state = WorkerState.OFFLINE
            if state == worker.get("state"):
                continue
            # Conditional on last_seen: a sync that landed meanwhile wins.
            result = await self.db.workers.update_one(
                {"worker_id": worker["worker_id"], "last_seen": worker["last_seen"]},
                {"$set": {"state": state, "updated_at": now}},
            )
            if result.modified_count:
                worker["state"] = state
                severity = "warning" if state == WorkerState.OFFLINE else "notice"
                log.info("worker %s is %s (last seen %.0fs ago)", worker["worker_id"], state, age)
                await self.notifier.publish(f"worker.{state}", severity, worker_id=worker["worker_id"])

    async def _release_from_unavailable_workers(self, workers: dict[str, dict[str, Any]], now: datetime) -> None:
        gone = {
            wid: ("worker offline" if w.get("state") == WorkerState.OFFLINE else "worker draining")
            for wid, w in workers.items()
            if w.get("state") == WorkerState.OFFLINE or w.get("draining") or w.get("draining_reported")
        }
        query = {"worker_id": {"$ne": None}, "desired_state": DesiredState.RUNNING}
        async for stream in self.db.streams.find(query):
            wid = stream["worker_id"]
            if wid in gone:
                await self._release(stream, now, reason=gone[wid])
            elif wid not in workers:
                await self._release(stream, now, reason="worker deleted")

    async def _release_stuck_streams(self, workers: dict[str, dict[str, Any]], now: datetime) -> None:
        # The worker gave up on the stream: try another worker after a backoff.
        async for stream in self.db.streams.find(
            {"worker_id": {"$ne": None}, "desired_state": DesiredState.RUNNING, "status": StreamStatus.ERROR}
        ):
            error = (stream.get("observed") or {}).get("error") or "worker reported an error"
            await self._release(stream, now, reason="error on worker", attempt_error=error)

        # Placed, but the (online) worker never reported it: move it.
        cutoff = now - timedelta(seconds=self.settings.placement_timeout_seconds)
        async for stream in self.db.streams.find(
            {"worker_id": {"$ne": None}, "desired_state": DesiredState.RUNNING, "observed": None,
             "placed_at": {"$lt": cutoff}}
        ):
            worker = workers.get(stream["worker_id"])
            if worker and worker.get("state") == WorkerState.ONLINE:
                await self._release(stream, now, reason="start timeout",
                                    attempt_error="worker did not start the stream in time")

    async def _release(self, stream: dict[str, Any], now: datetime, reason: str,
                       attempt_error: str | None = None) -> None:
        update: dict[str, Any] = {
            "worker_id": None,
            "status": StreamStatus.PENDING,
            "observed": None,
            "last_worker_id": stream["worker_id"],
            "updated_at": now,
        }
        failed = False
        if attempt_error is not None:
            attempts = stream.get("attempts", 0) + 1
            backoff = min(timedelta(seconds=self.settings.retry_backoff_seconds * 2 ** (attempts - 1)), MAX_BACKOFF)
            update.update(attempts=attempts, last_error=attempt_error, next_retry_at=now + backoff)
            if attempts >= self.settings.max_start_attempts:
                update["status"] = StreamStatus.FAILED
                failed = True
        result = await self.db.streams.update_one(
            {"stream_id": stream["stream_id"], "worker_id": stream["worker_id"], "generation": stream["generation"]},
            {"$set": update, "$inc": {"generation": 1}},
        )
        if not result.modified_count:
            return  # changed under us (API call or sync); the next tick sees the new state
        log.info("released stream %s from %s: %s", stream["stream_id"], stream["worker_id"], reason)
        if failed:
            await self.notifier.publish("stream.failed", "error", stream_id=stream["stream_id"],
                                        error=attempt_error, attempts=update["attempts"])
        else:
            await self.notifier.publish("stream.released", "notice", stream_id=stream["stream_id"],
                                        worker_id=stream["worker_id"], reason=reason)

    async def _place_pending(self, workers: dict[str, dict[str, Any]], now: datetime) -> None:
        candidates = {
            wid: w for wid, w in workers.items()
            if w.get("state") == WorkerState.ONLINE and not w.get("draining") and not w.get("draining_reported")
            and (w.get("capacity") or {}).get("max_streams", 0) > 0
        }
        pending_query = {
            "desired_state": DesiredState.RUNNING,
            "worker_id": None,
            "status": {"$ne": StreamStatus.FAILED},
            "$or": [{"next_retry_at": None}, {"next_retry_at": {"$lte": now}}],
        }
        if not candidates:
            return
        load = {wid: 0 for wid in candidates}
        async for row in await self.db.streams.aggregate([
            {"$match": {"worker_id": {"$in": list(candidates)}, "desired_state": DesiredState.RUNNING}},
            {"$group": {"_id": "$worker_id", "n": {"$sum": 1}}},
        ]):
            load[row["_id"]] = row["n"]

        async for stream in self.db.streams.find(pending_query).sort("created_at", 1).limit(500):
            worker_id = self._choose(candidates, load, avoid=stream.get("last_worker_id"))
            if worker_id is None:
                return  # fleet is full
            result = await self.db.streams.update_one(
                {"stream_id": stream["stream_id"], "worker_id": None, "desired_state": DesiredState.RUNNING,
                 "generation": stream["generation"]},
                {"$set": {"worker_id": worker_id, "status": StreamStatus.STARTING, "observed": None,
                          "placed_at": now, "updated_at": now},
                 "$inc": {"generation": 1}},
            )
            if result.modified_count:
                load[worker_id] += 1
                log.info("placed stream %s on worker %s", stream["stream_id"], worker_id)
                await self.notifier.publish("stream.placed", stream_id=stream["stream_id"], worker_id=worker_id)

    @staticmethod
    def _choose(candidates: dict[str, dict[str, Any]], load: dict[str, int], avoid: str | None) -> str | None:
        free = [wid for wid, w in candidates.items() if load[wid] < w["capacity"]["max_streams"]]
        if len(free) > 1 and avoid in free:
            free.remove(avoid)  # retry somewhere else when we can
        if not free:
            return None
        return min(free, key=lambda wid: (load[wid] / candidates[wid]["capacity"]["max_streams"], load[wid], wid))
