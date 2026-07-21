from typing import Optional, List, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timezone
from pydantic import BaseModel
from enum import Enum
from urllib.parse import urlparse
from pydantic.networks import AnyUrl


def _normalize_for_mongo(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in doc.items():
        if isinstance(v, BaseModel):
            out[k] = v.model_dump()  # flatten nested Pydantic models
        elif isinstance(v, Enum):
            out[k] = v.value         # store just the value
        elif isinstance(v, AnyUrl):
            out[k] = str(v)          # convert AnyHttpUrl to string
        elif isinstance(v, dict):
            out[k] = _normalize_for_mongo(v)  # recursive normalize
        elif isinstance(v, list):
            out[k] = [
                _normalize_for_mongo(x) if isinstance(x, dict) else (
                    x.value if isinstance(x, Enum) else (
                        str(x) if isinstance(x, AnyUrl) else x
                    )
                )
                for x in v
            ]
        else:
            out[k] = v
    return out

class WorkersRepo:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db.workers
        self.streams_col = db.streams

    async def upsert_worker(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["last_seen"] = datetime.now(timezone.utc)
        created_at = doc.get("created_at", datetime.now(timezone.utc))
        set_fields = {k: v for k, v in doc.items() if k != "created_at"}
        set_fields = _normalize_for_mongo(set_fields)

        await self.col.update_one(
            {"worker_id": doc["worker_id"]},
            {"$set": set_fields, "$setOnInsert": {"created_at": created_at}},
            upsert=True
        )
        return await self.col.find_one({"worker_id": doc["worker_id"]}, {"_id": 0})

    async def heartbeat(self, worker_id: str, patch: Dict[str, Any], watchtower_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        patch["last_seen"] = datetime.now(timezone.utc)
        patch = _normalize_for_mongo(patch)
        filter = {"worker_id": worker_id}
        if watchtower_token: filter["watchtower_token"] = watchtower_token
        res = await self.col.find_one_and_update(
            filter,
            {"$set": patch},
            return_document=True,
            projection={"_id": 0}
        )
        return res

    async def inc_streams(self, worker_id: str, delta: int, watchtower_token: Optional[str] = None):
        filter = {"worker_id": worker_id}
        if watchtower_token: filter["watchtower_token"] = watchtower_token
        await self.col.update_one(filter, {"$inc": {"assigned_stream_count": delta}})

    async def get(self, worker_id: str, watchtower_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        filter = {"worker_id": worker_id}
        if watchtower_token: filter["watchtower_token"] = watchtower_token
        return await self.col.find_one(filter, {"_id": 0})

    async def get_by_url(self, worker_url: str, watchtower_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        filter = {"worker_url": worker_url}
        if watchtower_token: filter["watchtower_token"] = watchtower_token
        return await self.col.find_one(filter, {"_id": 0})
    
    async def update(self, worker_id: str, patch: Dict[str, Any], watchtower_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        patch = _normalize_for_mongo(patch)
        filter = {"worker_id": worker_id}
        if watchtower_token: filter["watchtower_token"] = watchtower_token
        res = await self.col.find_one_and_update(
            filter,
            {"$set": patch},
            return_document=True,
            projection={"_id": 0}
        )
        return res

    async def delete(self, worker_id: str, watchtower_token: Optional[str] = None) -> int:
        # then delete the worker
        filter = {"worker_id": worker_id}
        if watchtower_token: filter["watchtower_token"] = watchtower_token
        res = await self.col.delete_one(filter)
        return res.deleted_count

    async def list(self, status: Optional[str] = None, watchtower_token: Optional[str] = None) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {}
        if status: filt["status"] = status
        if watchtower_token: filt["watchtower_token"] = watchtower_token
        cursor = self.col.find(filt, {"_id": 0}).sort("created_at", -1)
        return [doc async for doc in cursor]
