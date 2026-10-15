from __future__ import annotations

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, WebSocket, status
from pymongo.asynchronous.database import AsyncDatabase

from watchtower_contracts import Role

from .audit import Auditor
from .auth import AuthError, AuthUnavailable, Principal, TokenProvider
from .events import Notifier
from .ratelimit import FailureLimiter
from .scheduler import Scheduler
from .settings import Settings

WORKER_TOKEN_HEADER = "X-Watchtower-Token"  # noqa: S105 - a header name, not a secret


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> AsyncDatabase:
    return request.app.state.mongo.db


def get_notifier(request: Request) -> Notifier:
    return request.app.state.notifier


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


def get_provider(request: Request) -> TokenProvider:
    return request.app.state.provider


def get_auditor(request: Request) -> Auditor:
    return request.app.state.auditor


def client_address(request: Request | WebSocket) -> str | None:
    return request.client.host if request.client else None


def bearer_token(request: Request | WebSocket) -> str | None:
    """Authorization: Bearer <token>, or the legacy watchtower-token header."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.headers.get("watchtower-token") or None


def _check_not_blocked(limiter: FailureLimiter | None, client: str | None) -> None:
    wait = limiter.retry_after(client) if limiter else None
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed sign-in attempts; try again later",
                            headers={"Retry-After": str(wait)})


async def authenticate(provider: TokenProvider, token: str | None, minimum: Role, *,
                       limiter: FailureLimiter | None = None, client: str | None = None) -> Principal:
    _check_not_blocked(limiter, client)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        principal = await provider.verify(token)
    except AuthError as e:
        if limiter:
            limiter.record_failure(client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e) or "Invalid token",
                            headers={"WWW-Authenticate": "Bearer"}) from e
    except AuthUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Token verification unavailable") from e
    if not principal.has_role(minimum):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires the {minimum} role")
    return principal


def require(minimum: Role) -> Callable[..., Awaitable[Principal]]:
    async def dependency(request: Request, provider: TokenProvider = Depends(get_provider)) -> Principal:
        return await authenticate(provider, bearer_token(request), minimum,
                                  limiter=request.app.state.limiter, client=client_address(request))

    return dependency


@dataclass
class WorkerAuth:
    worker: dict[str, Any]
    principal: Principal | None  # None when the token server was unreachable


async def authenticate_worker(
    worker_id: str,
    request: Request,
    db: AsyncDatabase = Depends(get_db),
    provider: TokenProvider = Depends(get_provider),
    settings: Settings = Depends(get_settings),
) -> WorkerAuth:
    """401 = unknown worker or wrong secret (register again); 403 = token rejected (stop streams)."""
    limiter: FailureLimiter = request.app.state.limiter
    client = client_address(request)
    _check_not_blocked(limiter, client)
    secret = bearer_token(request)
    worker = await db.workers.find_one({"worker_id": worker_id})
    if not secret or not worker or not hmac.compare_digest(hash_secret(secret), worker.get("secret_hash", "")):
        limiter.record_failure(client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown worker or wrong secret; register again")

    token = request.headers.get(WORKER_TOKEN_HEADER)
    if not token:
        if settings.providers == ["none"]:
            return WorkerAuth(worker, await provider.verify(""))
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing {WORKER_TOKEN_HEADER} header")
    try:
        principal = await provider.verify(token)
    except AuthError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Worker token rejected") from e
    except AuthUnavailable:
        # Don't take the fleet down with the token server: keep the last known key.
        return WorkerAuth(worker, None)
    if not principal.has_role(Role.WORKER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token may not run workers")
    return WorkerAuth(worker, principal)
