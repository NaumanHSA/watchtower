from __future__ import annotations

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

IDEMPOTENCY_TTL_SECONDS = 24 * 3600


class Mongo:
    def __init__(self, uri: str, db_name: str):
        # tz_aware: every datetime read back is UTC-aware, matching what we write.
        self.client: AsyncMongoClient = AsyncMongoClient(uri, tz_aware=True, uuidRepresentation="standard")
        self.db: AsyncDatabase = self.client[db_name]

    async def ensure_indexes(self) -> None:
        db = self.db
        await db.workers.create_index("worker_id", unique=True)
        await db.workers.create_index("last_seen")
        await db.streams.create_index("stream_id", unique=True)
        await db.streams.create_index("source_url", unique=True)
        await db.streams.create_index([("worker_id", ASCENDING), ("desired_state", ASCENDING)])
        await db.streams.create_index([("desired_state", ASCENDING), ("worker_id", ASCENDING), ("status", ASCENDING)])
        await db.idempotency.create_index("key", unique=True)
        await db.idempotency.create_index("created_at", expireAfterSeconds=IDEMPOTENCY_TTL_SECONDS)
        await db.audit.create_index([("ts", -1)])
        await db.audit.create_index("expires_at", expireAfterSeconds=0)  # per-entry retention

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.close()
