"""HTTP API of the reference token server."""
from __future__ import annotations

import base64
import hmac
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from watchtower_contracts import Role, TokenVerification
from watchtower_contracts.crypto import decrypt_json, is_envelope

from . import __version__
from .settings import TokenServerSettings
from .store import Store

log = logging.getLogger("watchtower_token_server")

# Roles whose tokens get the results public key (a worker encrypts with it).
KEY_ROLES = {Role.WORKER.value, Role.ADMIN.value}


def presented_token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.headers.get("watchtower-token") or None


class IssueRequest(BaseModel):
    role: Role
    subject: str = Field(min_length=1, max_length=200)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


def create_app(settings: TokenServerSettings, store: Store | None = None,
               forward_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    store = store or Store(settings.data_dir / "store.json", settings.master_key)
    app = FastAPI(title="Watchtower Token Server", version=__version__,
                  description="Reference implementation of the Watchtower token-server contract "
                              "(docs/token-server.md).")
    app.state.store = store

    @app.get("/livez", tags=["health"])
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------ verify (the contract)

    def verify(request: Request) -> TokenVerification:
        token = presented_token(request)
        if not token:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing bearer token")
        record = store.lookup(token)
        if record is None:
            return TokenVerification(isSuccess=False, message="Unknown token")
        if not record.active:
            return TokenVerification(isSuccess=False, message="Token revoked or expired")
        key = store.current_key() if record.role in KEY_ROLES else None
        return TokenVerification(isSuccess=True, role=record.role, subject=record.subject,
                                 pub_cov=key.public_pem if key else None, kid=key.kid if key else None)

    for path in ("/v1/token/verify", "/token/verify", "/v1/watchTower/GetWatchTowerPubKey"):
        app.add_api_route(path, verify, methods=["GET"], response_model=TokenVerification,
                          response_model_exclude_none=True, tags=["verify"], include_in_schema=path.startswith("/v1/t"))

    # ------------------------------------------------------------------ admin API

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        if not settings.admin_key:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin API disabled (set TOKEN_SERVER_ADMIN_KEY)")
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), settings.admin_key.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin key")

    admin = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])

    @admin.post("/tokens", status_code=201)
    async def issue(body: IssueRequest) -> dict[str, Any]:
        record, value = store.issue(body.role, body.subject, expires_in_days=body.expires_in_days)
        return {"id": record.id, "token": value, "role": record.role, "subject": record.subject,
                "expires_at": record.expires_at, "note": "The token is shown only once."}

    @admin.get("/tokens")
    async def list_tokens() -> list[dict[str, Any]]:
        return [{"id": t.id, "role": t.role, "subject": t.subject, "created_at": t.created_at,
                 "expires_at": t.expires_at, "revoked": t.revoked, "active": t.active} for t in store.tokens()]

    @admin.post("/tokens/{token_id}/revoke")
    async def revoke(token_id: str) -> dict[str, Any]:
        if not store.revoke(token_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such token")
        return {"id": token_id, "revoked": True}

    @admin.get("/keys")
    async def list_keys() -> list[dict[str, Any]]:
        return [{"kid": k.kid, "created_at": k.created_at, "public_pem": k.public_pem, "current": i == 0}
                for i, k in enumerate(store.keys())]

    @admin.post("/keys/rotate", status_code=201)
    async def rotate() -> dict[str, Any]:
        key = store.rotate_key()
        return {"kid": key.kid, "public_pem": key.public_pem,
                "note": "Workers pick up the new key within one token-cache period; old keys still decrypt."}

    @admin.get("/keys/{kid}/private")
    async def export_private(kid: str) -> dict[str, str]:
        """For configuring your own results receiver to decrypt envelopes."""
        key = next((k for k in store.keys() if k.kid == kid), None)
        if key is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such key")
        return {"kid": kid, "private_pem": store.private_pem(key)}

    app.include_router(admin)

    # ------------------------------------------------------------------ optional results receiver

    if settings.ingest_enabled:
        forward_client = httpx.AsyncClient(timeout=10, transport=forward_transport) if settings.forward_url else None

        async def ingest(request: Request) -> dict[str, Any]:
            """Receives worker results. Encrypted envelopes are decrypted with the key named in
            meta.kid; the plaintext is logged, optionally saved, and optionally forwarded."""
            try:
                body = await request.json()
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not JSON") from None
            kid = None
            if is_envelope(body):
                kid = (body.get("meta") or {}).get("kid")
                try:
                    body, used = decrypt_json(body, store.private_pems(kid))
                except Exception as e:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Cannot decrypt: {e}") from None
                kid = store.keys()[used].kid if kid is None else kid
            event = body.get("received") if isinstance(body, dict) else None
            _report(event, kid, settings.save_images_dir)
            if forward_client is not None:
                try:
                    r = await forward_client.post(settings.forward_url, json=body)
                    r.raise_for_status()
                except httpx.HTTPError as e:
                    # 502 makes a worker in reliable mode keep the result and retry.
                    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Forwarding failed: {e}") from None
            return {"ok": True, "mode": "encrypted" if kid else "plaintext", "kid": kid}

        for path in ("/v1/ingest", "/ingest", "/ingest/encrypted"):
            app.add_api_route(path, ingest, methods=["POST"], tags=["ingest"], include_in_schema=path == "/v1/ingest")

    return app


def _report(event: Any, kid: str | None, save_dir: Path | None) -> None:
    if not isinstance(event, dict):
        log.info("ingest: unexpected body")
        return
    core = event.get("detectorParamsCore") or {}
    log.info("result stream=%s track=%s end=%s quality=%s bbox=%s %s", event.get("streamId") or event.get("camId"),
             event.get("trackId"), core.get("endOfTrack"), core.get("quality"), (event.get("bbox") or {}).get("xyxy"),
             f"(kid {kid})" if kid else "(plaintext)")
    if save_dir is None:
        return
    stream = re.sub(r"[^A-Za-z0-9_.-]", "_", str(event.get("streamId") or event.get("camId") or "unknown"))
    out = save_dir / stream
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    for name in ("photo", "normalized"):
        if isinstance(event.get(name), str):
            try:
                (out / f"{stamp}_{name}.jpg").write_bytes(base64.b64decode(event[name]))
            except ValueError:
                log.warning("could not decode %s", name)
    meta = {k: v for k, v in event.items() if k not in ("photo", "normalized")}
    (out / f"{stamp}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
