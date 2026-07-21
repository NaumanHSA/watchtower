from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, Float, Index
from sqlalchemy.sql import func
from .db import Base

class Stream(Base):
    __tablename__ = "streams"
    stream_id = Column(String, primary_key=True, index=True)
    source_url = Column(Text, nullable=False)
    webrtc_url = Column(String, nullable=True)
    results_interval = Column(Float, nullable=False)
    face_detection_confidence = Column(Float, nullable=False)
    face_detector_resolution = Column(Integer, nullable=False)
    max_allowed_detections = Column(Integer, nullable=False)
    max_frame_resolution = Column(Integer, nullable=False)
    max_cropped_face_resolution = Column(Integer, nullable=False)
    cropped_face_margin = Column(Float, nullable=False)
    frame_to_return = Column(String, nullable=False)
    streaming_fps = Column(Integer, nullable=False)
    camera_reconnect_interval = Column(Integer, nullable=False)
    camera_reconnect_attempts = Column(Integer, nullable=False)

    status = Column(String, nullable=False, default="starting")  # starting|running|stopped|error
    pid = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    stopped_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

Index("idx_streams_status", Stream.status)