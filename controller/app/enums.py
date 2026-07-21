from enum import StrEnum

class WorkerStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DRAINING = "draining"  # accepting no new streams

class StreamStatus(StrEnum):
    ASSIGNING = "assigning"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    DANGLING = "dangling"

class StreamSeverity(StrEnum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class StreamEvent(StrEnum):
    # Workers
    WORKER_REGISTERED = "worker.registered"
    WORKER_HEARTBEAT  = "worker.heartbeat"
    WORKER_UNREACHABLE= "worker.unreachable"
    WORKER_DELETED    = "worker.deleted"

    # Streams
    STREAM_ASSIGNED   = "stream.assigned"
    STREAM_MOVED      = "stream.moved"
    STREAM_REMOVED    = "stream.removed"
    STREAM_STATUS     = "stream.status"
    STREAM_PREVIEW    = "stream.preview"
    STREAM_DANGLING   = "stream.dangling"
    STREAM_RECOVERED  = "stream.recovered"