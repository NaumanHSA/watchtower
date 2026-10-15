"""Health, stats, live events and the MediaMTX auth hook."""
from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from pymongo.asynchronous.database import AsyncDatabase

from watchtower_contracts import DesiredState, Role, StreamStatus, WorkerState

from ..auth import AuthError, AuthUnavailable, Principal
from ..deps import authenticate, bearer_token, client_address, get_db, get_provider, require
from ..schemas import Stats

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/livez", tags=["health"])
async def livez() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["health"])
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    mongo_ok = await request.app.state.mongo.ping()
    if not mongo_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if mongo_ok else "unavailable", "mongo": mongo_ok,
            "scheduler_leader": request.app.state.scheduler.is_leader}


@router.get("/api/v1/stats", response_model=Stats, tags=["stats"], dependencies=[Depends(require(Role.VIEWER))])
async def stats(db: AsyncDatabase = Depends(get_db)) -> Stats:
    return await compute_stats(db)


async def compute_stats(db: AsyncDatabase) -> Stats:
    workers = {s.value: 0 for s in WorkerState}
    capacity = {"total": 0, "online": 0}
    async for w in db.workers.find({}, {"state": 1, "capacity": 1}):
        state = w.get("state", WorkerState.OFFLINE)
        workers[state] = workers.get(state, 0) + 1
        slots = (w.get("capacity") or {}).get("max_streams", 0)
        capacity["total"] += slots
        if state == WorkerState.ONLINE:
            capacity["online"] += slots
    streams = {s.value: 0 for s in StreamStatus}
    async for row in await db.streams.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        streams[row["_id"]] = row["n"]
    capacity["used"] = await db.streams.count_documents(
        {"worker_id": {"$ne": None}, "desired_state": DesiredState.RUNNING})
    return Stats(workers=workers, streams=streams, capacity=capacity)


@router.get("/metrics", tags=["health"], response_class=PlainTextResponse)
async def metrics(request: Request, db: AsyncDatabase = Depends(get_db)) -> str:
    """Prometheus metrics. Needs a viewer token unless METRICS_PUBLIC=true."""
    state = request.app.state
    if not state.settings.metrics_public:
        await authenticate(state.provider, bearer_token(request), Role.VIEWER, limiter=state.limiter,
                           client=client_address(request))
    s = await compute_stats(db)
    lines = ["# TYPE watchtower_workers gauge"]
    lines += [f'watchtower_workers{{state="{k}"}} {v}' for k, v in s.workers.items()]
    lines.append("# TYPE watchtower_streams gauge")
    lines += [f'watchtower_streams{{status="{k}"}} {v}' for k, v in s.streams.items()]
    lines.append("# TYPE watchtower_capacity_slots gauge")
    lines += [f'watchtower_capacity_slots{{kind="{k}"}} {v}' for k, v in s.capacity.items()]
    lines.append("# TYPE watchtower_worker_assigned_streams gauge")
    async for row in await db.streams.aggregate([
        {"$match": {"worker_id": {"$ne": None}, "desired_state": DesiredState.RUNNING}},
        {"$group": {"_id": "$worker_id", "n": {"$sum": 1}}},
    ]):
        lines.append(f'watchtower_worker_assigned_streams{{worker_id="{row["_id"]}"}} {row["n"]}')
    lines += [
        "# TYPE watchtower_scheduler_leader gauge",
        f"watchtower_scheduler_leader {int(state.scheduler.is_leader)}",
        "# TYPE watchtower_event_clients gauge",
        f"watchtower_event_clients {state.notifier.client_count}",
        "# TYPE watchtower_auth_failures_total counter",
        f"watchtower_auth_failures_total {state.limiter.failures_total}",
    ]
    return "\n".join(lines) + "\n"


@router.get("/api/v1/auth/config", tags=["auth"])
async def auth_config(request: Request) -> dict[str, Any]:
    """Public: how the dashboard can sign people in."""
    settings = request.app.state.settings
    oidc = None
    if "oidc" in settings.providers and settings.oidc_client_id:
        oidc = {"issuer": settings.oidc_issuer, "client_id": settings.oidc_client_id, "scope": settings.oidc_scope}
    return {"providers": settings.providers, "oidc": oidc}


@router.get("/api/v1/me", tags=["auth"])
async def me(principal: Principal = Depends(require(Role.VIEWER))) -> dict[str, str]:
    """Who the token belongs to and what it may do (the dashboard adapts to the role)."""
    return {"subject": principal.subject, "role": principal.role.value}


@router.get("/api/v1/audit", tags=["audit"], dependencies=[Depends(require(Role.ADMIN))])
async def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = None,
    target_id: str | None = None,
    db: AsyncDatabase = Depends(get_db),
) -> list[dict[str, Any]]:
    """Who changed what, newest first."""
    query: dict[str, Any] = {}
    if action:
        query["action"] = action
    if target_id:
        query["target_id"] = target_id
    return [{k: v for k, v in entry.items() if k not in ("_id", "expires_at")}
            async for entry in db.audit.find(query).sort("ts", -1).limit(limit)]


WS_TOKEN_PROTOCOL = "watchtower.bearer."  # noqa: S105 - a prefix, not a secret


@router.websocket("/api/v1/events")
async def events(ws: WebSocket) -> None:
    """Live events. Browsers can't set headers on WebSockets, so send the token as the
    subprotocol `watchtower.bearer.<token>` (keeps it out of URLs and access logs), or
    as ?token=… for tokens that contain characters a subprotocol can't."""
    offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    subprotocol = next((p for p in offered if p.startswith(WS_TOKEN_PROTOCOL)), None)
    token = subprotocol[len(WS_TOKEN_PROTOCOL):] if subprotocol else ws.query_params.get("token") or bearer_token(ws)
    try:
        await authenticate(ws.app.state.provider, token, Role.VIEWER, limiter=ws.app.state.limiter,
                           client=client_address(ws))
    except HTTPException as e:
        await ws.close(code=4401 if e.status_code == 401 else 4403 if e.status_code == 403 else 1013)
        return
    notifier = ws.app.state.notifier
    client = await notifier.attach(ws, subprotocol=subprotocol)
    try:
        while True:
            await ws.receive_text()  # clients may send keep-alives; content is ignored
    except WebSocketDisconnect:
        pass
    finally:
        await notifier.detach(client)


class MediaMTXAuthRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user: str = ""
    password: str = ""
    token: str = ""
    ip: str = ""
    action: str
    path: str = ""
    protocol: str = ""
    query: str = ""


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


@router.post("/api/v1/mediamtx/auth", tags=["mediamtx"], include_in_schema=False)
async def mediamtx_auth(body: MediaMTXAuthRequest, provider=Depends(get_provider)) -> Response:
    """MediaMTX `authMethod: http` hook. MediaMTX shares the worker's network namespace,
    so loopback clients are the worker itself; everyone else needs a viewer token."""
    denied = Response(status_code=status.HTTP_401_UNAUTHORIZED)
    if _is_loopback(body.ip):
        return Response(status_code=status.HTTP_200_OK)
    if body.action not in ("read", "playback"):
        return denied  # publishing and the MediaMTX API are local-only
    query = parse_qs(body.query)
    token = body.token or body.password or (query.get("token") or query.get("jwt") or [""])[0]
    if not token:
        return denied
    try:
        principal = await provider.verify(token)
    except (AuthError, AuthUnavailable):
        return denied
    if not principal.has_role(Role.VIEWER):
        return denied
    return Response(status_code=status.HTTP_200_OK)
