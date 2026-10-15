"""The worker's only HTTP surface: probes, Prometheus metrics, and the MediaMTX auth hook.

There is deliberately no control API; the worker takes instructions only from its syncs.
"""
from __future__ import annotations

import ipaddress
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

if TYPE_CHECKING:
    from .agent import Agent

VIEWER_CACHE_SECONDS = 30


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def create_health_app(agent: Agent) -> FastAPI:
    app = FastAPI(title="Watchtower Worker", docs_url=None, redoc_url=None, openapi_url=None)
    viewer_cache: dict[tuple[str, ...], tuple[float, bool]] = {}

    @app.get("/livez")
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict:
        ready = agent.ready
        if not ready:
            response.status_code = 503
        return {"status": "ok" if ready else "not ready", "worker_id": agent.supervisor.worker_id,
                "revoked": agent.revoked, "streams": len(agent.supervisor.streams)}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        return render_metrics(agent)

    @app.post("/mediamtx/auth")
    async def mediamtx_auth(request: Request) -> Response:
        """MediaMTX shares our network namespace: loopback clients are this worker's engines.
        Remote viewers are checked by the controller (so viewing needs the control plane,
        but analysis keeps running while the controller is down)."""
        body = await request.json()
        if _is_loopback(str(body.get("ip", ""))):
            return Response(status_code=200)
        if body.get("action") not in ("read", "playback"):
            return Response(status_code=401)
        key = (str(body.get("token", "")), str(body.get("password", "")), str(body.get("query", "")),
               str(body.get("action")))
        cached = viewer_cache.get(key)
        if cached and cached[0] > time.monotonic():
            allowed = cached[1]
        else:
            allowed = await agent.client.authorize_viewer(body)
            if len(viewer_cache) > 5000:
                viewer_cache.clear()
            viewer_cache[key] = (time.monotonic() + VIEWER_CACHE_SECONDS, allowed)
        return Response(status_code=200 if allowed else 401)

    return app


def render_metrics(agent: Agent) -> str:
    lines = [
        "# TYPE watchtower_worker_ready gauge",
        f"watchtower_worker_ready {int(agent.ready)}",
        "# TYPE watchtower_worker_sync_failures_total counter",
        f"watchtower_worker_sync_failures_total {agent.sync_failures_total}",
        "# TYPE watchtower_worker_last_sync_age_seconds gauge",
        f"watchtower_worker_last_sync_age_seconds "
        f"{round(time.monotonic() - agent.last_sync_ok, 3) if agent.last_sync_ok else -1}",
        "# TYPE watchtower_worker_stream_up gauge",
        "# TYPE watchtower_worker_stream_fps gauge",
        "# TYPE watchtower_worker_stream_restarts_total counter",
    ]
    for observed in agent.supervisor.observed():
        label = f'stream_id="{observed.stream_id}",status="{observed.status}"'
        lines.append(f"watchtower_worker_stream_up{{{label}}} {int(observed.status == 'running')}")
        lines.append(f'watchtower_worker_stream_fps{{stream_id="{observed.stream_id}"}} {observed.fps or 0}')
        lines.append(f'watchtower_worker_stream_restarts_total{{stream_id="{observed.stream_id}"}} '
                     f"{observed.restarts}")
    stats = agent.delivery.stats()
    for field in ("sent", "dropped", "failed"):
        lines.append(f"# TYPE watchtower_worker_results_{field}_total counter")
        lines.append(f"watchtower_worker_results_{field}_total {getattr(stats, field)}")
    lines.append("# TYPE watchtower_worker_spool_results gauge")
    lines.append(f"watchtower_worker_spool_results {stats.spooled}")
    lines.append("# TYPE watchtower_worker_spool_bytes gauge")
    lines.append(f"watchtower_worker_spool_bytes {stats.spool_bytes}")
    return "\n".join(lines) + "\n"
