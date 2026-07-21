from pydantic import BaseModel, AnyHttpUrl, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from ..enums import WorkerStatus

# make model for worker capabilities
class WorkerCapabilities(BaseModel):
    max_allowed_streams: Optional[int] = None
    
class WorkerRegisterIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=128)
    worker_url: AnyHttpUrl
    web_rtc_url: AnyHttpUrl
    controller_url: AnyHttpUrl
    capabilities: WorkerCapabilities = WorkerCapabilities()
    host_info: Dict[str, Any] = {}

class WorkerOut(BaseModel):
    worker_id: str
    worker_url: AnyHttpUrl
    worker_health_url: AnyHttpUrl
    controller_url: AnyHttpUrl
    status: WorkerStatus
    capabilities: WorkerCapabilities
    assigned_stream_count: int
    last_seen: datetime
    created_at: datetime
    host_info: Dict[str, Any]

class WorkerPatchHeartbeat(BaseModel):
    worker_id: str
    status: Optional[WorkerStatus] = None
    streams: Optional[List[Dict[str, Any]]] = None

class WorkerActionOut(BaseModel):
    ok: bool
    code: Optional[int] = None
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    worker_id: Optional[str] = None    
    public_key_pem: Optional[str] = None
