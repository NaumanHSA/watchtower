# REST API

Everything the dashboard does, you can do from a script through the REST API. This page lists every endpoint
with the role it needs, and the formats of live events and errors.

The controller also serves interactive documentation at `/docs`, where you can try each call from the browser,
and a machine-readable description at `/openapi.json`. Every call under `/api/v1` needs a token in the
`Authorization: Bearer <token>` header (the older `watchtower-token` header also works).

## Cameras (streams)

| Method | Path | Role | |
|---|---|---|---|
| `GET` | `/api/v1/streams` | viewer | List. Filters: `?status=running`, `?worker_id=…` |
| `GET` | `/api/v1/streams/{id}` | viewer | One stream |
| `POST` | `/api/v1/streams` | operator | Add. Send `Idempotency-Key` to make retries safe. |
| `PATCH` | `/api/v1/streams/{id}` | operator | Change `name`, `location`, `metadata`, `source_url` or any `config` key; only the keys you send change |
| `POST` | `/api/v1/streams/{id}/start` | operator | Start, or retry a `failed` stream |
| `POST` | `/api/v1/streams/{id}/stop` | operator | Stop |
| `DELETE` | `/api/v1/streams/{id}` | operator | Remove |
| `GET` | `/api/v1/streams/{id}/view` | viewer | WebRTC URLs for live view, or nulls when not running |

```bash
curl -X POST http://localhost:7000/api/v1/streams \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"name": "Lobby", "location": "Building A", "source_url": "rtsp://user:pass@10.0.0.12:554/stream",
       "config": {"fps": 8, "delivery_mode": "reliable"}}'
```

`source_url` must be `rtsp`, `rtsps`, `rtmp(s)`, `http(s)` or `srt`, and must be
unique. The password in it is masked in every response. A new stream starts
`pending` and is usually `running` within a few seconds; follow it on the event
stream or by polling.

In the API, cameras are called *streams*. Every camera setting is described in
[Cameras](cameras.md#camera-settings).

A few everyday calls:

```bash
# Cameras that need attention
curl -s -H "Authorization: Bearer $TOKEN" "$WT/api/v1/streams?status=failed"

# Change two settings of one camera (restarts its analysis)
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \n  "$WT/api/v1/streams/$ID" -d '{"config": {"fps": 5, "results_interval_ms": 3000}}'

# Retry a failed camera
curl -s -X POST -H "Authorization: Bearer $TOKEN" "$WT/api/v1/streams/$ID/start"

# Drain a worker before maintenance, then undo it
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \n  "$WT/api/v1/workers/$WORKER/drain" -d '{"draining": true}'
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \n  "$WT/api/v1/workers/$WORKER/drain" -d '{"draining": false}'
```

(`$WT` is your controller's address, for example `https://watchtower.example.com`.)

## Workers

| Method | Path | Role | |
|---|---|---|---|
| `GET` | `/api/v1/workers` | viewer | List (filter `?state=online`) |
| `GET` | `/api/v1/workers/{id}` | viewer | One worker, with load and delivery counters |
| `POST` | `/api/v1/workers/{id}/drain` | operator | `{"draining": true}` moves its cameras away and keeps new ones off; `false` undoes it |
| `DELETE` | `/api/v1/workers/{id}` | admin | Forget a worker. Refused while it is online unless `?force=true`. A worker with a valid token can register again; revoke its token to keep it out. |

`POST /api/v1/workers/register` and `POST /api/v1/workers/{id}/sync` are the worker
protocol, described in [architecture.md](architecture.md).

## Other

| Method | Path | Role | |
|---|---|---|---|
| `GET` | `/api/v1/me` | viewer | `{"subject", "role"}` of the token |
| `GET` | `/api/v1/stats` | viewer | Workers by state, streams by status, capacity |
| `GET` | `/api/v1/audit` | admin | Who changed what, newest first. Filters: `?action=stream.delete`, `?target_id=…`, `?limit=` (≤ 500) |
| `WS` | `/api/v1/events` | viewer | Live events (below) |
| `GET` | `/livez`, `/readyz` | none | Probes. `readyz` checks MongoDB and reports whether this replica is the scheduler. |
| `GET` | `/metrics` | viewer (or none with `METRICS_PUBLIC`) | Prometheus metrics |
| `GET` | `/` | none | The dashboard |

## Live events

Connect a WebSocket to `/api/v1/events`. Browsers can't set headers on WebSockets, so
pass the token as the subprotocol `watchtower.bearer.<token>` (it then stays out of
URLs and access logs), or as `?token=<token>` for tokens with characters a subprotocol
can't carry.

```js
const ws = new WebSocket("wss://watchtower.example.com/api/v1/events", [`watchtower.bearer.${token}`]);
ws.onmessage = (m) => console.log(JSON.parse(m.data));
```

Each message:

```json
{"event": "stream.status", "category": "stream", "severity": "info",
 "params": {"stream_id": "…", "worker_id": "…", "status": "running", "error": null},
 "ts": "2026-10-15T11:56:13.412000+00:00", "event_id": "…", "schema": "1"}
```

Events: `stream.created`, `stream.updated`, `stream.started`, `stream.stopped`,
`stream.deleted`, `stream.placed`, `stream.status`, `stream.released`,
`stream.failed`, `worker.registered`, `worker.online`, `worker.suspect`,
`worker.offline`, `worker.draining`, `worker.undrained`, `worker.deleted`, and a
`ping` every `WS_PING_INTERVAL_SECONDS`. Treat events as hints to refresh: they are not
replayed after a disconnect.

## Errors

Errors are JSON `{"detail": …}` with the usual status codes: `401` missing or invalid
token, `403` role too low, `404`, `409` conflict (duplicate `source_url`, idempotency
key in use, worker online), `422` validation (detail lists each field), `429` too many
failed sign-ins from this address (see `Retry-After`), `503` token server unavailable.

## Audit log

Every change made through the API is recorded with the token's subject and role:
`stream.create`, `stream.update`, `stream.start`, `stream.stop`, `stream.delete`,
`worker.register`, `worker.drain`, `worker.undrain`, `worker.delete`. Camera passwords
are masked. Entries expire after `AUDIT_RETENTION_DAYS`. Admins see them in the
dashboard's **Audit log** page.
