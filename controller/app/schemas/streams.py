from pydantic import BaseModel, AnyHttpUrl, Field, AnyUrl
from typing import Optional, Dict, Any
from datetime import datetime
from ..enums import StreamStatus

class AssignStreamIn(BaseModel):
    source_url: str
    stream_name: str
    stream_location: str
    worker_id: Optional[str] = None
    stream_metadata: Dict[str, Any] = {}
    stream_id: Optional[str] = None

class AssignStreamOut(BaseModel):
    stream_id: str
    worker_id: str
    status: StreamStatus
    webrtc_url: Optional[AnyUrl] = None

class StreamOut(BaseModel):
    stream_id: str
    stream_name: str
    stream_location: str
    source_url: str   # rtsp URL
    worker_id: Optional[str] = None
    status: StreamStatus
    webrtc_url: Optional[AnyUrl] = None   # webrtc URL
    stream_metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

class UpdateStreamIn(BaseModel):
    stream_id: str
    stream_name: Optional[str] = None
    stream_location: Optional[str] = None
    stream_metadata: Optional[Dict[str, Any]] = None

class MoveStreamIn(BaseModel):
    new_worker_id: str

class WorkerStreamActionOut(BaseModel):
    ok: bool
    details: Dict[str, Any] = {}
