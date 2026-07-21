from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import Optional
from config import config

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None

async def connect() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is not None:
        return _db
    _client = AsyncIOMotorClient(config.MONGO_URI, uuidRepresentation="standard")
    _db = _client[config.MONGO_DB]
    await _ensure_indexes(_db)
    print("MongoDB connected successfully")
    return _db

async def close():
    global _client, _db
    if _client:
        _client.close()
    _client = None
    _db = None

async def _ensure_indexes(db: AsyncIOMotorDatabase):
    await db.workers.create_index("worker_id", unique=True, name="u_worker_id")
    await db.workers.create_index("status", name="i_status")
    await db.workers.create_index("last_seen", name="i_last_seen")

    await db.streams.create_index("stream_id", unique=True, name="u_stream_id")
    await db.streams.create_index("worker_id", name="i_worker")
    await db.streams.create_index("status", name="i_status")
    await db.streams.create_index([("created_at", -1)], name="i_created_at_desc")

    await db.idempotency.create_index(
        [("key", 1)], unique=True, name="u_idempotency_key"
    )
    print("Indexes created successfully")
