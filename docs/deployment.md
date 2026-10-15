# Deployment

A production setup has two parts that deploy independently:

- **one control plane**: controller + MongoDB (+ a token server, unless you use your
  own), reachable over HTTPS by workers and dashboard users;
- **any number of worker hosts**, each running a worker and a MediaMTX, close to the
  cameras.

The Compose stacks in [`deploy/compose`](../deploy/compose) cover both. Kubernetes
notes are at the end.

## 1. Control plane

```bash
cd deploy/compose/controller
cp .env.example .env
```

Edit `.env`:

- **Authentication.** Keep `AUTH_PROVIDER=http` and run the bundled token server
  (`--profile token-server`), or point `TOKEN_SERVER_URL` at your own
  ([token-server.md](token-server.md)), or use `AUTH_PROVIDER=static` for a very small
  installation.
- With the bundled token server, set `TOKEN_SERVER_ADMIN_KEY` (enables its admin API)
  and `TOKEN_SERVER_MASTER_KEY` (encrypts the results private keys at rest):
  `docker compose run --rm token-server gen-master-key`.
- **`RESULTS_URL`**: where detections go by default.
- **HTTPS**: set `DOMAIN` and add `--profile tls` to get a Caddy reverse proxy with an
  automatic certificate (ports 80 and 443 must reach this host). Or put your own proxy
  in front of port 7000.
- **Behind a proxy**, set `FORWARDED_ALLOW_IPS` to the proxy's address so the sign-in
  rate limit and the audit log see real client addresses, and don't publish port 7000
  beyond the proxy (`CONTROLLER_BIND=127.0.0.1` when the proxy runs on the same host).

```bash
docker compose --profile token-server --profile tls up -d --build
docker compose exec token-server watchtower-token-server issue --role admin --subject you
docker compose exec token-server watchtower-token-server issue --role worker --subject site-a
```

Open `https://<DOMAIN>/`, sign in with the admin token, and keep the worker token for
the next step.

**Back up** the `mongo-data` volume (cameras and their settings) and the `tokens`
volume (token hashes and the results private keys). Losing the results keys means
results already encrypted to them can't be read.

## 2. Worker hosts

```bash
cd deploy/compose/worker
cp .env.example .env
```

Set `CONTROLLER_URL`, `WATCHTOWER_TOKEN` (a worker token), `WORKER_NAME`,
`MAX_STREAMS`, and the address viewers reach this host at (`PUBLIC_HOST`,
`PUBLIC_WEBRTC_URL`). Then:

```bash
docker compose up -d --build
```

The worker appears in the dashboard within seconds. Cameras must be reachable from
this host (MediaMTX opens them).

### Network

| Direction | Port | Needed for |
|---|---|---|
| out | controller (443) | registration and sync |
| out | results URL | delivering detections |
| out | cameras (usually 554) | reading video |
| in | 8889/tcp | WebRTC signalling for live viewers |
| in | 8189/udp and 8189/tcp | WebRTC media |
| local only | 8081 | health, metrics, MediaMTX auth hook (bound to 127.0.0.1 by default; `HEALTH_BIND`) |

Live view works when the viewer's browser can reach `PUBLIC_WEBRTC_URL` and the media
port. When viewers or workers sit behind strict NAT, run a TURN relay that both can
reach (for example [coturn](https://github.com/coturn/coturn)) and give it to MediaMTX in
the worker's `.env`:

```
MTX_WEBRTCICESERVERS2_0_URL=turn:turn.example.com:3478
MTX_WEBRTCICESERVERS2_0_USERNAME=watchtower
MTX_WEBRTCICESERVERS2_0_PASSWORD=<secret>
```

MediaMTX passes the relay to authorised viewers only, and the dashboard's player uses
it automatically. Analysis does not depend on live view.

## Scaling

- **More cameras:** add worker hosts, or raise `MAX_STREAMS` where CPU allows. The
  scheduler balances new cameras across workers by load ratio.
- **More controllers:** run several controller replicas behind a load balancer, all on
  the same MongoDB, and set `REDIS_URL` so dashboards see every event. One replica at a
  time runs the scheduler (lease in MongoDB); the others serve the API and syncs. With
  several replicas, consider `ON_LEASE_EXPIRY=stop` on workers (see
  [architecture.md](architecture.md#failure-handling)).
- **MongoDB:** use a replica set for availability.

## Upgrades

Workers and controllers check the protocol version at registration, and every message
ignores unknown fields, so a minor version of either side can be upgraded first.
Upgrade controllers before workers when the release notes say the protocol changed.
Stopping a worker (`docker compose down` / `SIGTERM`) drains it: its cameras move to
other workers before it exits (`stop_grace_period: 30s`).

## Observability

- Controller: `/livez`, `/readyz`; structured logs with `LOG_FORMAT=json`; Prometheus
  metrics at `/metrics` (workers by state, streams by status, capacity, per-worker
  assignments, scheduler leadership, failed sign-ins), with a viewer token unless
  `METRICS_PUBLIC=true`.
- Worker: `/livez`, `/readyz` (synced recently), and Prometheus metrics at `/metrics`:
  per-stream status, fps and restarts; results sent, dropped and failed; spool size;
  sync failures and last sync age.
- Alert on: a worker `offline`, streams `failed` or `error`, `watchtower_worker_results_dropped_total`
  rising, `watchtower_worker_spool_bytes` growing (receiver down in `reliable` mode).

## Kubernetes

A Helm chart is in [`deploy/helm/watchtower`](../deploy/helm/watchtower). It runs the
controller as a Deployment (any number of replicas; one schedules at a time), workers as
a StatefulSet whose pods each hold the worker and a MediaMTX sidecar (containers in a
pod share the network namespace, as the MediaMTX config expects) with a volume for the
worker's identity and spool, and optionally the token server.

```bash
kubectl create secret generic wt-worker --from-literal=WATCHTOWER_TOKEN=<worker token>
helm install wt deploy/helm/watchtower   --set controller.mongoUri=mongodb://mongo.db.svc:27017   --set worker.tokenSecret.name=wt-worker   --set tokenServer.enabled=true   --set controller.ingress.enabled=true --set controller.ingress.host=watchtower.example.com
```

Put credentials (MongoDB URI with a password, `STATIC_TOKENS`, `OIDC_*`, the token
server's admin and master keys) in Secrets and reference them with
`controller.existingSecret`, `worker.existingSecret` and `tokenServer.existingSecret`.
Every service setting can be passed through the `env` maps; see `values.yaml`.

**MongoDB and Redis** are not part of the chart: use a managed service or an operator,
and set `controller.redisUrl` when running more than one controller replica.

**Live video** is the part that depends on your network: media flows over UDP to the
worker pod. With `worker.hostNetwork=true` each worker advertises its node's IP and
viewers connect to the node directly (ports 8889/tcp and 8189/udp+tcp must be
reachable). Otherwise expose those ports with a LoadBalancer or NodePort per worker and
set `worker.publicWebrtcUrl` and `worker.publicHost`, or add a TURN server. Analysis
works either way.

The chart is linted, rendered and validated against the Kubernetes schemas in CI; it
has not yet been exercised on a live cluster, so please report anything that needs
adjusting for yours.
