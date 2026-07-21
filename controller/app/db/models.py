from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime

class WorkerDoc(BaseModel):
    worker_id: str
    controller_url: str
    base_url: str  # worker's API base (e.g., http://worker-host:9000)
    status: str = "online"
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    assigned_stream_count: int = 0
    last_seen: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class StreamDoc(BaseModel):
    stream_id: str
    source_url: str
    worker_id: str
    status: str
    webrtc_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
