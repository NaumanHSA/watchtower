import httpx
import asyncio
from fastapi import HTTPException
import logging
from typing import Any, Dict
from config import config

logger = logging.getLogger(__name__)

class WorkerAdapter:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def _client(self):
        return httpx.AsyncClient(
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={"X-API-Key": config.WORKER_API_KEY},  # workers can also check
        )

    async def start_stream(self, stream_id: str, source_url: str, worker_id: str) -> Dict[str, Any]:
        async with await self._client() as client:
            for attempt in range(config.REQUEST_MAX_RETRIES + 1):
                logger.info(f"Attempt {attempt} to start stream {stream_id}")
                try:
                    payload = {
                        "stream_id": stream_id,
                        "source_url": source_url,
                        "worker_id": worker_id,
                    }
                    resp = await client.post(f"{self.base_url}/streams/assign", json=payload)
                    if resp.is_error:
                        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "Unknown error"))
                    return resp.json()
                except HTTPException as e:
                    logger.error(f"Failed to start stream {stream_id} on worker {self.base_url}: {e}")
                    if attempt >= config.REQUEST_MAX_RETRIES:
                        raise e
                    await asyncio.sleep(attempt + 2)

    async def stop_stream(self, stream_id: str) -> Dict[str, Any]:
        async with await self._client() as client:
            for attempt in range(config.REQUEST_MAX_RETRIES + 1):
                try:
                    resp = await client.delete(f"{self.base_url}/streams/delete/{stream_id}")
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPError as e:
                    logger.error(f"Failed to stop stream {stream_id} on worker {self.base_url}: {e}")
                    if attempt >= config.REQUEST_MAX_RETRIES:
                        raise e
                    await asyncio.sleep(attempt + 2)
