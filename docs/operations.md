# Monitoring and maintenance

Watchtower heals most problems by itself, but you should still know when it is doing so. This page covers health
checks, metrics and alerts, logs, the audit log, backups and upgrades.

## Health checks

Every service answers two simple checks:

| Service | Address | Answers `200` when |
|---|---|---|
| Controller | `/livez` | The process is running. |
| Controller | `/readyz` | It can reach MongoDB. The answer also says whether this controller is the one currently scheduling (`scheduler_leader`). |
| Worker | `:8081/livez` | The process is running. |
| Worker | `:8081/readyz` | It has synced with the controller recently and its token hasn't been revoked. |
| Token server | `:9000/livez` | The process is running. |

The Docker images use these for their built-in health checks, and the Helm chart uses them as liveness and
readiness probes.

## Metrics

Both the controller and the workers publish metrics in the Prometheus format at `/metrics`.

**The controller** reports the state of the whole fleet. Reading it needs a viewer token (configure your Prometheus
scrape job with `authorization: {credentials: <token>}`), unless you set `METRICS_PUBLIC=true` on a controller that
only your monitoring network can reach.

| Metric | Meaning |
|---|---|
| `watchtower_workers{state}` | Workers that are `online`, `suspect` or `offline`. |
| `watchtower_streams{status}` | Cameras in each status: `running`, `pending`, `failed` and so on. |
| `watchtower_capacity_slots{kind}` | Camera slots on all known workers (`total`), on online workers (`online`), and how many are in use (`used`). |
| `watchtower_worker_assigned_streams{worker_id}` | Cameras placed on each worker. |
| `watchtower_scheduler_leader` | 1 on the controller that is currently scheduling. |
| `watchtower_event_clients` | Open dashboard connections. |
| `watchtower_auth_failures_total` | Rejected tokens and worker secrets. |

**Each worker** reports on itself, on port 8081 (no token needed; the port is bound to `127.0.0.1` unless you set
`HEALTH_BIND`).

| Metric | Meaning |
|---|---|
| `watchtower_worker_ready` | 1 while the worker is in contact with the controller. |
| `watchtower_worker_last_sync_age_seconds` | Seconds since the last successful sync. |
| `watchtower_worker_sync_failures_total` | Failed syncs since the worker started. |
| `watchtower_worker_stream_up{stream_id,status}` | 1 for each camera that is running. |
| `watchtower_worker_stream_fps{stream_id}` | Frames analysed per second, per camera. |
| `watchtower_worker_stream_restarts_total{stream_id}` | Analysis restarts, per camera. |
| `watchtower_worker_results_sent_total`, `_dropped_total`, `_failed_total` | Result delivery counters. |
| `watchtower_worker_spool_results`, `watchtower_worker_spool_bytes` | Results waiting on disk (reliable mode). |

## What to alert on

A good starting set of alerts, written as Prometheus rules:

```yaml
groups:
  - name: watchtower
    rules:
      - alert: WatchtowerWorkerOffline
        expr: watchtower_workers{state="offline"} > 0
        for: 5m
        annotations: {summary: "A Watchtower worker has been offline for 5 minutes"}
      - alert: WatchtowerCamerasFailed
        expr: watchtower_streams{status="failed"} > 0
        for: 10m
        annotations: {summary: "Cameras gave up and need attention"}
      - alert: WatchtowerCamerasWaiting
        expr: watchtower_streams{status="pending"} > 0
        for: 15m
        annotations: {summary: "Cameras are waiting for a worker; add capacity"}
      - alert: WatchtowerCapacityLow
        expr: watchtower_capacity_slots{kind="used"} / ignoring(kind) watchtower_capacity_slots{kind="online"} > 0.85
        for: 30m
        annotations: {summary: "Over 85% of worker slots are in use"}
      - alert: WatchtowerNoScheduler
        expr: max(watchtower_scheduler_leader) == 0
        for: 2m
        annotations: {summary: "No controller is scheduling"}
      - alert: WatchtowerResultsDropping
        expr: rate(watchtower_worker_results_dropped_total[10m]) > 0
        for: 10m
        annotations: {summary: "A worker is dropping results; is the results receiver healthy?"}
      - alert: WatchtowerResultsBacklog
        expr: watchtower_worker_spool_bytes > 100e6
        for: 15m
        annotations: {summary: "Results are piling up on a worker's disk (receiver down?)"}
```

Keep in mind that a camera reported *failed* stays failed until someone presses **Retry**, so that alert keeps
firing until a person has looked at it, which is the point.

## Logs

- **Controller:** `docker compose logs -f controller`. Set `LOG_FORMAT=json` to get one JSON object per line for
  your log system.
- **Worker:** `docker compose logs -f worker` on the worker machine. It logs registration, every camera started and
  stopped, connection problems with the controller, and delivery problems.
- **MediaMTX:** `docker compose logs -f mediamtx` on the worker machine, for camera connection details.

Watchtower's own log lines never contain tokens, worker secrets or camera addresses. MediaMTX's log may mention
camera addresses when a camera can't be reached, so treat worker logs as sensitive.

## The audit log

Every change made through the dashboard or the API (cameras added, changed, started, stopped and deleted; workers
registered, drained and forgotten) is recorded with who made it and when. Admins read it on the **Audit log** page
or with `GET /api/v1/audit` (filters: `?action=stream.delete`, `?target_id=<id>`, `?limit=`). Entries are kept for
`AUDIT_RETENTION_DAYS` (365 by default; `0` keeps them forever).

## Backups

Two things hold state you can't recreate:

- **MongoDB** holds your cameras, their settings, the list of workers and the audit log.
- **The token server's data** holds the token hashes and the results key pairs. If you lose the private keys,
  results that were encrypted with them can never be read again.

Workers hold nothing that needs a backup: their identity is recreated by registering again, and the results waiting
in their spool are only kept until they are delivered.

**Back up**, on the control plane host, from `deploy/compose/controller`:

```bash
# MongoDB, as a single compressed archive
docker compose exec -T mongo mongodump --archive --gzip > watchtower-mongo-$(date +%F).archive.gz

# The token server's data
docker compose cp token-server:/data ./watchtower-tokens-$(date +%F)
```

Store the copies somewhere else, encrypted: the token server backup contains your results private keys (encrypted
only if `TOKEN_SERVER_MASTER_KEY` is set, and then you must keep the master key too).

**Restore:**

```bash
docker compose exec -T mongo mongorestore --archive --gzip --drop < watchtower-mongo-2026-10-15.archive.gz
docker compose cp ./watchtower-tokens-2026-10-15/. token-server:/data
docker compose restart controller token-server
```

Try a restore once on a spare machine, so you know it works before you need it.

## Upgrades

Read the [changelog](https://github.com/NaumanHSA/watchtower/blob/main/CHANGELOG.md) first. Then:

1. **Back up** as described above.
2. **Upgrade the control plane.** On the control plane host: `git pull`, then
   `docker compose --profile token-server --profile tls up -d --build`. The dashboard is unavailable for a few
   seconds while the controller restarts; workers keep analysing and simply sync again when it is back.
3. **Upgrade the workers**, one at a time: `git pull`, then `docker compose up -d --build` on each worker machine.
   A worker that restarts hands its cameras to other workers first, so with spare capacity nothing stops.

Controllers and workers of neighbouring versions work together, so there is no need to upgrade everything at once.
If the release notes say the protocol changed, upgrade the controllers before the workers.

## Routine housekeeping

- **Retired workers** stay on the **Workers** page as *offline* until an admin forgets them.
- **Tokens** of people who leave should be revoked (`watchtower-token-server revoke <id>`). Consider giving tokens
  an expiry (`--expires-days`).
- **Results keys** can be rotated on a schedule with `watchtower-token-server rotate-key`; see
  [Receiving results](results.md#encryption).
- **Capacity:** keep an eye on *Capacity used* on the Overview. Add workers before it gets close to full, so a
  failed worker's cameras always have somewhere to go.
