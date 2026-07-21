from datetime import datetime, timezone
from typing import Any, Dict, Optional
from ..enums import StreamEvent, StreamSeverity
import uuid

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def evt(
    name: StreamEvent | str,
    *,
    severity: StreamSeverity = StreamSeverity.INFO,
    params: Optional[Dict[str, Any]] = None,
    # extra spices (all optional):
    source: str | None = None,           # emitter (svc or module)
    request_id: str | None = None,       # propagate from HTTP layer if present
    correlation_id: str | None = None,   # tie a flurry of events together
    actor: str | None = None,            # user/service that triggered it
    tenant_id: str | None = None,        # multi-tenant support if needed
    schema_version: str = "1",           # evolve safely
    dedupe_key: str | None = None,       # optional client-side dedupe
    i18n_key: str | None = None,         # frontend translation key (optional)
) -> Dict[str, Any]:
    """
    Build a compact, stable event envelope for websocket broadcast (and optional sinks).
    """
    # normalize
    event_name = name.value if isinstance(name, StreamEvent) else str(name)
    payload = {
        "event": event_name,
        "severity": severity.value,
        "category": event_name.split(".")[0],
        "params": params or {},
        "ts": _now_iso(),
        "schema": schema_version,
    }
    if source:          payload["source"] = source
    if request_id:      payload["request_id"] = request_id
    if correlation_id:  payload["correlation_id"] = correlation_id
    if actor:           payload["actor"] = actor
    if tenant_id:       payload["tenant_id"] = tenant_id
    if dedupe_key:      payload["dedupe_key"] = dedupe_key
    if i18n_key:        payload["i18n_key"] = i18n_key
    # Always include an event_id so the UI can key animations/lists
    payload["event_id"] = str(uuid.uuid4())
    return payload
