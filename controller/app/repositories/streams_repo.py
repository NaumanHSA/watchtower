from pydantic import AnyUrl, BaseModel
from typing import Optional, List, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timezone
from enum import Enum


def normalize_for_mongo(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in doc.items():
        if isinstance(v, BaseModel):
            out[k] = v.model_dump()  # flatten nested Pydantic models
        elif isinstance(v, Enum):
            out[k] = v.value         # store just the value
        elif isinstance(v, AnyUrl):
            out[k] = str(v)          # convert AnyHttpUrl to string
        elif isinstance(v, dict):
            out[k] = normalize_for_mongo(v)  # recursive normalize
        elif isinstance(v, list):
            out[k] = [
                normalize_for_mongo(x) if isinstance(x, dict) else (
                    x.value if isinstance(x, Enum) else (
                        str(x) if isinstance(x, AnyUrl) else x
                    )
                )
                for x in v
            ]
        else:
            out[k] = v
    return out


class StreamsRepo:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db.streams

    async def get_by_source(self, source_url: str) -> Optional[Dict[str, Any]]:
        return await self.col.find_one({"source_url": str(source_url)}, {"_id": 0})

    async def create(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        doc.setdefault("created_at", now)
        doc["updated_at"] = now
        safe_doc = normalize_for_mongo(doc)
        await self.col.insert_one(safe_doc)
        return await self.col.find_one({"stream_id": doc["stream_id"]}, {"_id": 0})

    async def update(self, stream_id: str, patch: Dict[str, Any], watchtower_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        patch["updated_at"] = datetime.now(timezone.utc)
        safe_patch = normalize_for_mongo(patch)
        filt: Dict[str, Any] = {"stream_id": stream_id}
        if watchtower_token: filt["watchtower_token"] = watchtower_token
        return await self.col.find_one_and_update(
            filt,
            {"$set": safe_patch},
            projection={"_id": 0},
            return_document=True
        )

    async def update_many(self, filter: Dict[str, Any], patch: Dict[str, Any], watchtower_token: Optional[str] = None) -> int:
        patch["updated_at"] = datetime.now(timezone.utc)
        safe_patch = normalize_for_mongo(patch)
        filt: Dict[str, Any] = filter
        if watchtower_token: filt["watchtower_token"] = watchtower_token
        res = await self.col.update_many(filt, {"$set": safe_patch})
        return res.modified_count

    async def get(self, stream_id: str, watchtower_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        filt: Dict[str, Any] = {"stream_id": stream_id}
        if watchtower_token: filt["watchtower_token"] = watchtower_token
        return await self.col.find_one(filt, {"_id": 0})

    async def delete(self, stream_id: str, watchtower_token: Optional[str] = None) -> int:
        filt: Dict[str, Any] = {"stream_id": stream_id}
        if watchtower_token: filt["watchtower_token"] = watchtower_token
        res = await self.col.delete_one(filt)
        return res.deleted_count

    async def list(self, worker_id: Optional[str] = None, status: Optional[str] = None, watchtower_token: Optional[str] = None) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {}
        if worker_id: filt["worker_id"] = worker_id
        if status: filt["status"] = status
        if watchtower_token: filt["watchtower_token"] = watchtower_token
        cursor = self.col.find(filt, {"_id": 0}).sort("created_at", -1)
        return [doc async for doc in cursor]

