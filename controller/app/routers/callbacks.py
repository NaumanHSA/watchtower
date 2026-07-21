from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, AnyHttpUrl
from typing import Optional, Dict, Any
from ..db import connect
from ..repositories.streams_repo import StreamsRepo
from ..security import require_worker_api_key, require_watchtower_token
from ..enums import StreamStatus

router = APIRouter(prefix="/callbacks", tags=["callbacks"])

class StreamStatusUpdate(BaseModel):
    status: Optional[StreamStatus] = None
    webrtc_url: Optional[AnyHttpUrl] = None 
    metadata: Optional[Dict[str, Any]] = None

@router.post("/stream/{stream_id}/status", dependencies=[Depends(require_worker_api_key), Depends(require_watchtower_token)])
async def stream_status_callback(stream_id: str, payload: StreamStatusUpdate):
    db = await connect()
    repo = StreamsRepo(db)
    curr = await repo.get(stream_id)
    if not curr:
        raise HTTPException(status_code=404, detail="Stream not found")
    patch = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    await repo.update(stream_id, patch)
    return {"ok": True}
