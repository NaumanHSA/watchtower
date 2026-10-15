from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Controller configuration. Every field can be set as an environment variable of
    the same name in upper case (e.g. MONGO_URI) or in a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- server ----
    host: str = "0.0.0.0"
    port: int = 7000
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    cors_allow_origins: str = ""  # comma-separated; empty = same-origin only
    # Proxies whose X-Forwarded-For is believed (comma-separated, or "*"). Only list your own proxy:
    # client addresses feed the sign-in rate limit and the audit log.
    forwarded_allow_ips: str = "127.0.0.1"

    # ---- storage ----
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "watchtower"

    # ---- auth ----
    # http:   verify tokens with a token server (TOKEN_SERVER_URL), e.g. token-server/ in this repo
    # static: tokens listed in STATIC_TOKENS / STATIC_TOKENS_FILE as "role:token" entries
    # oidc:   JWTs from an OpenID Connect identity provider (OIDC_ISSUER)
    # none:   no authentication; refuses to start unless ALLOW_NO_AUTH=true (local development only)
    # Several can be chained, tried in order: e.g. "oidc,static" (people via SSO, workers via static tokens).
    auth_provider: str = "http"
    token_server_url: str | None = None
    token_cache_seconds: int = 60
    static_tokens: str = ""
    static_tokens_file: Path | None = None
    result_public_key_file: Path | None = None  # results key for static/none, or when the token server has none
    allow_no_auth: bool = False

    # ---- OpenID Connect (AUTH_PROVIDER includes oidc) ----
    oidc_issuer: str | None = None  # e.g. https://keycloak.example.com/realms/watchtower
    oidc_audience: str | None = None  # expected `aud`; strongly recommended
    oidc_jwks_url: str | None = None  # default: discovered from the issuer
    oidc_role_claim: str = "roles"  # dot path, e.g. realm_access.roles (Keycloak)
    oidc_role_map: str = ""  # "idp-role:watchtower-role,..."; empty = claim values are role names
    oidc_subject_claims: str = "preferred_username,email,sub"
    oidc_algorithms: str = "RS256,ES256,PS256"
    oidc_client_id: str | None = None  # public client for the dashboard's "Sign in with SSO" button
    oidc_scope: str = "openid profile email"

    # ---- scheduling ----
    scheduler_enabled: bool = True  # false: this replica never places streams (it still serves the API)
    sync_interval_ms: int = 5000
    lease_seconds: int = 60  # a worker that misses syncs this long loses its streams
    offline_grace_seconds: int = 15
    scheduler_interval_seconds: float = 2.0
    placement_timeout_seconds: int = 60  # placed stream never reported by its worker -> move it
    max_start_attempts: int = 5
    retry_backoff_seconds: int = 10  # doubles per attempt, capped at 10 minutes

    # ---- results ----
    results_url: str | None = None  # default destination for streams that don't set one

    # ---- notifications ----
    redis_url: str | None = None  # set when running more than one controller replica
    ws_send_queue_size: int = 100
    ws_ping_interval_seconds: int = 20

    request_timeout_seconds: float = 10.0

    # ---- abuse protection and accountability ----
    auth_failure_limit: int = 30  # failed sign-ins per client address per window; 0 disables
    auth_failure_window_seconds: int = 300
    audit_retention_days: int = 365  # 0 keeps the audit log forever
    metrics_public: bool = False  # /metrics without a token (only when the port is internal)
    max_request_bytes: int = 1_048_576  # larger bodies get 413; 0 disables

    @model_validator(mode="after")
    def _check_auth(self) -> Settings:
        providers = self.providers
        unknown = set(providers) - {"http", "static", "oidc", "none"}
        if unknown or not providers:
            raise ValueError("AUTH_PROVIDER must be http, static, oidc or none, or a comma-separated chain; "
                             f"got {self.auth_provider!r}")
        if "none" in providers and len(providers) > 1:
            raise ValueError("AUTH_PROVIDER=none can't be combined with other providers")
        if "http" in providers and not self.token_server_url:
            raise ValueError("AUTH_PROVIDER=http needs TOKEN_SERVER_URL")
        if "static" in providers and not (self.static_tokens or self.static_tokens_file):
            raise ValueError("AUTH_PROVIDER=static needs STATIC_TOKENS or STATIC_TOKENS_FILE")
        if "oidc" in providers and not self.oidc_issuer:
            raise ValueError("AUTH_PROVIDER=oidc needs OIDC_ISSUER")
        if providers == ["none"] and not self.allow_no_auth:
            raise ValueError("AUTH_PROVIDER=none disables all authentication; set ALLOW_NO_AUTH=true to confirm")
        return self

    @property
    def providers(self) -> list[str]:
        return [p.strip().lower() for p in self.auth_provider.split(",") if p.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def worker_online_seconds(self) -> float:
        """A worker that synced within this window counts as online."""
        return 3 * self.sync_interval_ms / 1000 + 1

    @property
    def worker_offline_seconds(self) -> float:
        return self.lease_seconds + self.offline_grace_seconds


@lru_cache
def get_settings() -> Settings:
    return Settings()
