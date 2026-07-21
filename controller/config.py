from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl, Field
from typing import List, Optional


class Config(BaseSettings):
    # ---- App meta ----
    APP_NAME: str = Field(alias="APP_NAME", default="Video Worker Controller")
    APP_VERSION: str = Field(alias="APP_VERSION", default="1.0.0")
    HOST: str = Field(alias="HOST", default="0.0.0.0")
    PORT: int = Field(alias="PORT", default=7000)
    LOG_LEVEL: str = Field(alias="LOG_LEVEL", default="INFO")
    RECOVERY_INTERVAL: int = Field(alias="RECOVERY_INTERVAL", default=20)
    WATCHDOG_INTERVAL: int = Field(alias="WATCHDOG_INTERVAL", default=20)

    # ---- Mongo ----
    MONGO_URI: str = Field(alias="MONGO_URI")
    MONGO_DB: str = Field(alias="MONGO_DB", default="controller")

    # ---- API keys ----
    WORKER_API_KEY: str = Field(alias="WORKER_API_KEY")

    # ------ Auth ------
    TOKEN_VERIFICATION_URL: str = Field(alias="TOKEN_VERIFICATION_URL")

    # ---- Networking ----
    REQUEST_TIMEOUT_SECONDS: int = Field(alias="REQUEST_TIMEOUT_SECONDS", default=10)
    REQUEST_MAX_RETRIES: int = Field(alias="REQUEST_MAX_RETRIES", default=2)

    # ---- CORS ----
    CORS_ALLOW_ORIGINS: List[str] = Field(alias="CORS_ALLOW_ORIGINS", default=["*"])

    # ---- WebSocket Notifications ----
    WS_PING_INTERVAL_SECONDS: int = Field(alias="WS_PING_INTERVAL_SECONDS", default=20)
    WS_SEND_QUEUE_SIZE: int = Field(alias="WS_SEND_QUEUE_SIZE", default=100)
    # If you later run multiple controller replicas, set REDIS_URL to enable cross-instance broadcasting
    REDIS_URL: Optional[str] = Field(alias="REDIS_URL", default=None)

    class Config:
        env_file = ".env"
        case_sensitive = True

config = Config()