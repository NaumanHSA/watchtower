from pydantic import BaseModel
from typing import Optional, Any, Dict

class AssignStreamIn(BaseModel):
    stream_id: str
    source_url: str
    worker_id: str

class StreamOut(BaseModel):
    stream_id: str
    source_url: str
    webrtc_url: Optional[str] = None
    status: str
    pid: Optional[int] = None

    class Config:
        from_attributes = True

class StreamDetailOut(StreamOut):
    worker_id: str
    results_interval: float
    face_detection_confidence: float
    face_detector_resolution: int
    max_allowed_detections: int
    max_frame_resolution: int
    max_cropped_face_resolution: int
    frame_to_return: str
    streaming_fps: int
    camera_reconnect_interval: int
    camera_reconnect_attempts: int
