from __future__ import annotations

import socket
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from watchtower_contracts import DeliveryMode


class WorkerSettings(BaseSettings):
    """Worker configuration. Every field can be set as an environment variable of the
    same name in upper case (e.g. CONTROLLER_URL) or in a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- identity / controller ----
    controller_url: str = "http://localhost:7000"
    watchtower_token: str = ""  # token with the `worker` role, from your token server
    worker_name: str = socket.gethostname()
    worker_id: str | None = None  # optional fixed id; otherwise the controller assigns one
    state_dir: Path = Path("/var/lib/watchtower")  # identity + result spool; mount a volume here
    max_streams: int = 2
    public_webrtc_url: str | None = None  # how viewers reach this worker's MediaMTX, e.g. http://10.0.0.5:8889
    controller_timeout_seconds: float = 10.0
    on_lease_expiry: Literal["keep_running", "stop"] = "keep_running"

    # ---- health / metrics (also serves the MediaMTX auth hook) ----
    health_host: str = "0.0.0.0"
    health_port: int = 8081

    # ---- MediaMTX (runs next to the worker, sharing its network namespace) ----
    mediamtx_enabled: bool = True
    mediamtx_api_url: str = "http://127.0.0.1:9997"
    mediamtx_rtsp_url: str = "rtsp://127.0.0.1:8554"
    mediamtx_username: str | None = None
    mediamtx_password: str | None = None
    mediamtx_bearer_token: str | None = None
    engine_input: Literal["mediamtx", "direct"] = "mediamtx"  # mediamtx: one camera connection for everything

    # ---- engine ----
    detector_backend: Literal["onnx", "opencv"] = "onnx"
    max_engine_restarts: int = 3

    # ---- results ----
    results_url: str | None = None  # used when neither the stream nor the controller sets one
    delivery_mode: DeliveryMode = DeliveryMode.REALTIME
    require_encryption: bool = False  # never send results in plaintext
    results_send_token: bool = False  # add the watchtower-token header (legacy receivers look keys up by it)
    realtime_queue_size: int = 100
    max_result_age_ms: int = 5000
    spool_max_mb: int = 512
    delivery_timeout_seconds: float = 5.0

    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    @field_validator("controller_url", "mediamtx_api_url")
    @classmethod
    def _required_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"must be an http(s) URL, got {value!r}")
        return value

    @field_validator("public_webrtc_url", "results_url")
    @classmethod
    def _optional_http_url(cls, value: str | None) -> str | None:
        return None if value in (None, "") else cls._required_http_url(value)


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
