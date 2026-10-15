from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TokenServerSettings(BaseSettings):
    """Every field can be set as TOKEN_SERVER_<NAME> (e.g. TOKEN_SERVER_ADMIN_KEY)."""

    model_config = SettingsConfigDict(env_prefix="TOKEN_SERVER_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 9000
    data_dir: Path = Path("data")
    admin_key: str | None = Field(default=None, description="Enables /v1/admin/* (Bearer). Unset = CLI only")
    master_key: str | None = Field(default=None, description="Fernet key that encrypts private keys at rest")
    bootstrap: bool = Field(default=True, description="On first start, issue an admin and a worker token")
    ingest_enabled: bool = Field(default=False, description="Also act as a results receiver (/v1/ingest)")
    forward_url: str | None = Field(default=None, description="Forward decrypted results to this URL")
    save_images_dir: Path | None = None
    log_level: str = "INFO"
