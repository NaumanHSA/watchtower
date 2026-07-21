# Watchtower

Distributed video analytics platform. A central **controller** orchestrates a fleet of
**workers**; each worker ingests camera streams, runs face detection in isolated
processes, and publishes viewable streams over WebRTC via MediaMTX.

The two halves live in one repo but deploy independently.

```
┌─────────────────┐         announce / heartbeat        ┌──────────────────┐
│                 │ ◄────────────────────────────────── │  worker-1        │
│   controller    │         stream assignments          │  + mediamtx      │
│   (one deploy)  │ ──────────────────────────────────► │                  │
│                 │                                     └──────────────────┘
│   FastAPI       │ ◄────────────────────────────────── ┌──────────────────┐
│   MongoDB       │                                     │  worker-N        │
└─────────────────┘                                     │  (anywhere)      │
                                                        └──────────────────┘
```

## Layout

| Path | What it is | Deployment |
|---|---|---|
| [controller/](controller/) | Orchestrator: worker registry, stream assignment, health monitoring, failover, WebSocket notifications | Single deployment |
| [worker/](worker/) | Stream processor: camera ingest, YuNet ONNX face detection, MediaMTX/WebRTC publishing | Many, anywhere |

Each directory is self-contained — its own `Dockerfile`, `requirements.txt`,
`docker-compose.yml`, and `.env`. Nothing at the repo root is needed to build or run
either side.

## Deploying the controller

One instance, typically alongside MongoDB.

```bash
cd controller
cp env.example .env    # set MONGO_URI, WORKER_API_KEY, TOKEN_VERIFICATION_URL
docker compose up -d --build
```

Listens on `:7000`. See [controller/README.md](controller/README.md).

## Deploying a worker

Run as many as you need, on any host that can reach the controller.

```bash
cd worker
cp env.example .env    # set CONTROLLER_URL, HOST_WORKER_URL, HOST_MTX_WEBRTC_URL
docker compose up -d --build
```

Two compose files are provided:

- `docker-compose.yml` — bridge network, worker + MediaMTX pair. Use for multiple
  workers on one host.
- `docker-compose-host.yml` — host networking. Use for a single worker per host.

A worker announces itself to `CONTROLLER_URL` on startup and heartbeats periodically,
so no controller-side registration is needed. See [worker/README.md](worker/README.md).

## Wiring the two sides

These must agree across both deployments:

| Setting | Controller | Worker |
|---|---|---|
| Shared API key | `WORKER_API_KEY` | `WORKER_API_KEY` |
| Controller address | — | `CONTROLLER_URL` |
| Worker's externally reachable URL | discovered via announce | `HOST_WORKER_URL` |
| Worker's WebRTC viewer URL | discovered via announce | `HOST_MTX_WEBRTC_URL` |

`HOST_WORKER_URL` and `HOST_MTX_WEBRTC_URL` must be addresses the **controller and
viewers** can reach — not `localhost` — whenever the worker runs on a different host.

## Repo history

This repo was formed by merging two previously separate repositories with their full
histories preserved:

- `video-worker-controller` → `controller/`
- `video-worker-for-controller` → `worker/`

`git blame` and `git log` work normally on all existing lines. Because the files moved
into subdirectories, path-scoped log needs `--follow` to cross the merge point:

```bash
git log --follow -- worker/main.py
```
