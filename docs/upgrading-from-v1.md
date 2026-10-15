# Upgrading from 1.x

Version 2 is a redesign of how the controller and workers talk to each other. There is
no in-place upgrade: deploy v2 next to v1, add the cameras again, and switch over.

## What changed

| 1.x | 2.0 |
|---|---|
| The controller called each worker's HTTP API to start and stop streams | Workers pull their assignments; the worker has no control API |
| Workers announced themselves to `/internal/worker/go_live` and sent heartbeats | `POST /api/v1/workers/register` once, then `/api/v1/workers/{id}/sync` |
| One shared `WORKER_API_KEY` for workers and the dashboard | Bearer tokens checked by a token server, with roles; a secret per worker |
| `watchtower-token` verification was disabled in code | Every token is verified (`AUTH_PROVIDER`) |
| Endpoints under `/dashboard/…` | Endpoints under `/api/v1/…`; see [api.md](api.md) |
| `PATCH /dashboard/stream/{id}` reassigned the stream | `PATCH /api/v1/streams/{id}` edits it |
| A worker whose controller was unreachable shut itself down | Workers keep running and retry |
| Results were never actually sent (a stray `return`) | Results are delivered, `realtime` or `reliable` |
| Per-worker settings in env (`FACE_DETECTION_CONFIDENCE`, `STREAMING_FPS`, …) | Per-camera `config` set through the API |
| Controller and worker each in their own repo layout | One uv workspace; images built from the repo root |

## Result payload

Receivers of v1 payloads keep working: the `received` object keeps `camId`, `bsType`,
`bbox`, `detectorParamsCore`, `snapshotNote`, `photo` and `normalized`, and adds
`eventId`, `streamId`, `streamName`, `workerId`, `generation`, `trackId`, `timestamp`
and `capturedAt` ([results.md](results.md)). Two differences:

- `detectorParamsCore` now carries real values (`endOfTrack`, `quality`,
  `trackDurationSeconds` were always `false`/`0` in 1.x).
- The `watchtower-token` header is no longer sent with results unless
  `RESULTS_SEND_TOKEN=true`; encrypted envelopes name their key in `meta.kid` instead.

## Token servers

A token server written for 1.x (answering `isSuccess` and `pub_cov`) works unchanged:
tokens without a `role` are treated as `admin`. Add `role` to its answers to separate
viewers, operators and workers ([token-server.md](token-server.md)).
