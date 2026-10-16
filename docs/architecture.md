# Architecture

Watchtower has three services and one shared protocol package:

- **controller**: the control plane. It holds the desired state (which cameras should
  run, with what settings) in MongoDB, decides which worker runs each camera, serves
  the REST API, the dashboard and a live event stream.
- **worker**: runs on any machine with access to cameras. Pulls its assignments from
  the controller, runs one detection engine process per camera, delivers results, and
  serves live video through a MediaMTX instance next to it.
- **token server**: answers "is this token valid, and what may it do?" for the
  controller, and holds the key results are encrypted with. Replaceable by your own
  service ([contract](token-server.md)).
- **watchtower-contracts**: the Pydantic models of every message above.

## Desired state, pulled

Nothing calls into a worker. The worker opens every connection:

1. **Register** (`POST /api/v1/workers/register`, once): the worker presents its
   *worker token*. The controller checks it with the token server and returns a
   `worker_id` and a per-worker secret, which the worker stores in its state directory.
2. **Sync** (`POST /api/v1/workers/{id}/sync`, every `SYNC_INTERVAL_MS`, 5 s by
   default): the worker reports what it runs (stream, generation, status, fps, error),
   its load and delivery statistics. The controller records that and answers with the
   streams the worker *should* run, the results key, and timing parameters.
3. **Reconcile**: the worker starts engines for new streams, stops engines for streams
   it no longer owns, and restarts an engine whose **generation** changed.

The generation is a counter on each stream, incremented whenever the stream is placed
on a worker, moved, stopped, or its source or settings change. Reports carrying an old
generation are ignored, and results carry the generation too, so a receiver can tell
results of a superseded assignment apart.

Because workers only make outbound requests, they work behind NAT and need no inbound
control port. The only inbound traffic to a worker host is live video for viewers.

## Scheduling

Exactly one controller replica runs the scheduler at a time: replicas compete for a
lease document in MongoDB, renewed every tick. Every other write (API calls, worker
syncs) only records desired or observed state, so placement never races.

Each tick (every 2 s, or immediately after a relevant change) the scheduler:

1. **Updates worker liveness** from the time of each worker's last sync:
   `online` (synced within 3 intervals), `suspect` (within the lease + grace),
   `offline` (beyond it). After a replica becomes the scheduler it waits one full lease
   before marking anyone offline, so a controller restart never reshuffles the fleet.
2. **Releases streams** from offline workers, draining workers and deleted workers.
3. **Retries failed starts**: a stream whose worker reported `error` (the worker gave
   up after restarting the engine several times) is released with an attempt counted,
   and placed again after a backoff (10 s, 20 s, 40 s … capped at 10 minutes),
   preferably on a different worker. After `MAX_START_ATTEMPTS` it is marked `failed`
   until someone restarts it. A stream placed on an online worker that never reports it
   within `PLACEMENT_TIMEOUT_SECONDS` is treated the same way.
4. **Places pending streams** on online, non-draining workers with free capacity,
   choosing the least loaded (`assigned / max_streams`).

### Stream status

| Status | Meaning |
|---|---|
| `pending` | Should run; waiting for a worker (none free, or backing off) |
| `starting` | Placed; the worker hasn't reported it running yet |
| `running` | Frames are being analysed |
| `reconnecting` | The camera dropped; the engine is reconnecting |
| `error` | The worker gave up; the scheduler will move it |
| `failed` | Gave up after `MAX_START_ATTEMPTS`; start it again to retry |
| `stopped` | Stopped by an operator |

## Failure handling

| What fails | What happens |
|---|---|
| A camera drops | The engine reconnects (`reconnect_attempts` × `reconnect_interval_s`), status `reconnecting`. If it can't, the engine exits, the worker restarts it with backoff (5, 15, 30 s), then reports `error`; the scheduler moves the stream elsewhere. |
| An engine process crashes | Same restart path as above. |
| A worker dies or loses its network | After the lease + grace (60 + 15 s by default) it is `offline` and its streams are placed on other workers. Measured end to end: 84 s from `SIGKILL` to running elsewhere. |
| A worker shuts down cleanly | On `SIGTERM` it reports `draining`; the controller moves its streams immediately, then the worker stops its engines. |
| The controller is down | Workers keep analysing and delivering with their last assignments, and retry syncs with backoff. With `ON_LEASE_EXPIRY=stop` they instead stop their streams once the lease expires. Choose this when you run several controller replicas and would rather lose coverage than risk two workers processing one camera during a network partition. |
| The token server is down | Workers keep syncing with their last known key; API calls with tokens not seen recently get `503`. |
| A worker's token is revoked | Its next sync gets `403`; it stops all streams and checks back every minute. |
| MediaMTX restarts | It forgets paths added through its API; the worker re-adds them on its next sync. Measured: live view and analysis back within 12 s. |
| The results receiver is down | `realtime`: results are dropped once older than `MAX_RESULT_AGE_MS`. `reliable`: results are spooled on disk and delivered in order when it returns. |

## Inside a worker

```
agent (asyncio)                     engine process (one per camera)
├─ sync loop ── controller          ├─ frame reader thread: keeps only the newest frame
├─ supervisor                       ├─ YuNet face detector (onnxruntime, or OpenCV)
│   ├─ spawns/stops engines   ◄──── ├─ IoU tracker: tentative → confirmed → ended
│   └─ MediaMTX paths               └─ per-face snapshots → result events
├─ delivery threads ── results URL       │
│   realtime queue | reliable spool ◄────┘ (multiprocessing queue)
└─ health server :8081 (/livez /readyz /metrics, MediaMTX auth hook)
```

- **One camera connection.** MediaMTX pulls each camera once; the engine reads it
  back over RTSP on localhost and viewers get it over WebRTC. Set `ENGINE_INPUT=direct`
  to have the engine open the camera itself.
- **Real time by construction.** The frame reader keeps only the newest frame and the
  engine analyses at most `fps` frames per second, so a slow detector never builds a
  backlog. Video *files* are played at their own frame rate and loop, so a file can
  stand in for a camera.
- **Tracking.** A face becomes a track after `track_confirm_frames` consecutive
  detections and ends after `track_max_missed_frames` misses. A track produces a
  result when it is confirmed, then at most every `results_interval_ms`, and a final
  one with `endOfTrack: true` when it ends.
- **Shared network namespace.** MediaMTX shares the worker's network namespace (a
  pause container in Compose, a sidecar in Kubernetes), so its API and RTSP server stay
  on localhost and the worker can tell its own engines (loopback) from remote viewers.

## Auth in one picture

| Who | Presents | Checked by |
|---|---|---|
| Dashboard / API user | `Authorization: Bearer <token>` (role viewer, operator or admin) | controller → token server (cached), or locally as an OIDC JWT |
| Worker, registering | `Authorization: Bearer <worker token>` | controller → token server |
| Worker, syncing | its per-worker secret + `X-Watchtower-Token` | controller (secret hash) + token server (revocation, key rotation) |
| Live viewer | viewer token in `Authorization` (or `?token=`) | MediaMTX → worker hook → controller → token server |
| Worker's own engine reading MediaMTX | nothing (loopback) | worker hook |

See [api.md](api.md) for what each role may do.
