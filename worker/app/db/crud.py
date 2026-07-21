from datetime import datetime, timezone
from sqlalchemy.orm import Session
from typing import Optional
from .models import Stream
from config import config

def create_stream(db: Session, stream_id: str, source_url: str) -> Stream:
    # Check if stream already exists for this worker+source_url
    existing = (
        db.query(Stream)
        .filter(Stream.source_url == source_url)
        .first()
    )
    if existing:
        return existing   # or raise Exception("Stream already exists")

    row = Stream(
        stream_id=stream_id,
        source_url=source_url,
        results_interval=config.RESULTS_INTERVAL,
        face_detection_confidence=config.FACE_DETECTION_CONFIDENCE,
        face_detector_resolution=config.FACE_DETECTOR_RESOLUTION,
        max_allowed_detections=config.MAX_ALLOWED_DETECTIONS,
        max_frame_resolution=config.MAX_FRAME_RESOLUTION,
        max_cropped_face_resolution=config.MAX_CROPPED_FACE_RESOLUTION,
        frame_to_return=config.FRAME_TO_RETURN,
        streaming_fps=config.STREAMING_FPS,
        camera_reconnect_interval=config.CAMERA_RECONNECT_INTERVAL,
        camera_reconnect_attempts=config.CAMERA_RECONNECT_ATTEMPTS,
        cropped_face_margin=config.CROPPED_FACE_MARGIN,
        status="starting",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def streams_for_worker_to_boot(db: Session):
    # restart anything that was running/starting previously
    return (
        db.query(Stream)
        .filter(Stream.status.in_(["starting", "running"]))
        .all()
    )

def set_stream_started(db: Session, stream_id: str, pid: int):
    row = db.query(Stream).get(stream_id)
    if row:
        row.status = "running"
        row.pid = pid
        row.started_at = datetime.now(timezone.utc)
        db.commit(); db.refresh(row)
    return row

def set_stream_error(db: Session, stream_id: str, message: str):
    row = db.query(Stream).get(stream_id)
    if row:
        row.status = "error"
        row.error_message = message
        db.commit(); db.refresh(row)
    return row

def set_stream_stopped(db: Session, stream_id: str):
    row = db.query(Stream).get(stream_id)
    if row:
        row.status = "stopped"
        row.stopped_at = datetime.now(timezone.utc)
        row.pid = None
        row.webcam_url = None
        db.commit(); db.refresh(row)
    return row

def get_stream(db: Session, stream_id: str) -> Optional[Stream]:
    return db.query(Stream).get(stream_id)

def list_streams(db: Session):
    return db.query(Stream).all()

def delete_stream_row(db: Session, stream_id: str):
    row = db.query(Stream).get(stream_id)
    if row:
        db.delete(row)
        db.commit()
        return True
    return False
