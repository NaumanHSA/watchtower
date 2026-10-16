# Configuration

Every Watchtower service is configured with environment variables. With Docker Compose you put them in the `.env`
file next to the Compose file; with Kubernetes in the chart's `env` maps or in a Secret. Names are not case
sensitive. Changing a setting takes effect when the service restarts (`docker compose up -d` does that for you).

Camera settings are different: they belong to each camera and are changed in the dashboard or the API, without
restarting anything. They are listed at the [end of this page](#per-camera-settings).

## Which setting do I need?

| I want to… | Set | On |
|---|---|---|
| Tell a worker where the controller is | `CONTROLLER_URL`, `WATCHTOWER_TOKEN` | Worker |
| Let a worker take more cameras | `MAX_STREAMS` | Worker |
| Send results to my application | `RESULTS_URL` | Controller (or per camera) |
| Never lose a result, even if my application is down | `DELIVERY_MODE=reliable` | Worker (or per camera) |
| Never send unencrypted results | `REQUIRE_ENCRYPTION=true` | Worker |
| Turn on live view for a worker | `PUBLIC_WEBRTC_URL`, `PUBLIC_HOST` | Worker |
| Let people sign in with our company accounts | `AUTH_PROVIDER=oidc,...` and the `OIDC_*` settings | Controller |
| Use the bundled token server | `AUTH_PROVIDER=http`, `TOKEN_SERVER_URL` | Controller |
| Notice a dead worker sooner | `LEASE_SECONDS`, `OFFLINE_GRACE_SECONDS` | Controller |
| Run more than one controller | `REDIS_URL` | Controller |
| Get logs in JSON | `LOG_FORMAT=json` | All |
| Put the controller behind my own proxy | `FORWARDED_ALLOW_IPS` | Controller |
| Find smaller faces | `detector_resolution`, `max_frame_width` | Camera |
| Use less processor time | `fps`, `detector_resolution` | Camera |
| Get fewer results per person | `results_interval_ms` | Camera |

## Controller

### Database

| Setting | Default | What it does |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | Where MongoDB is. Include the user name and password if your MongoDB needs them, and point it at a replica set for high availability. |
| `MONGO_DB` | `watchtower` | The database name. Change it to run two independent installations on one MongoDB. |

### Sign-in

See [Users and sign-in](access.md) for how these fit together.

| Setting | Default | What it does |
|---|---|---|
| `AUTH_PROVIDER` | `http` | Where tokens are checked: `http` (a token server), `oidc` (your identity provider), `static` (a list in this configuration), `none` (no sign-in at all). Combine them with commas, such as `oidc,http`; the first one that accepts a token wins. |
| `TOKEN_SERVER_URL` | not set | The token server's verify address, for example `http://token-server:9000/v1/token/verify`. Required with `http`. |
| `TOKEN_CACHE_SECONDS` | `60` | How long the controller remembers a token server's answer. Lower it to make revocations take effect faster, at the cost of more requests to the token server. |
| `REQUEST_TIMEOUT_SECONDS` | `10` | How long a request to the token server may take. |
| `STATIC_TOKENS` | not set | With `static`: the tokens, as `role:token` pairs separated by commas. |
| `STATIC_TOKENS_FILE` | not set | With `static`: a file with one `role:token` per line. Lines starting with `#` are ignored. |
| `RESULT_PUBLIC_KEY_FILE` | not set | A PEM public key for workers to encrypt results with, when the token server doesn't provide one (always the case with `static` and `oidc`). |
| `ALLOW_NO_AUTH` | `false` | Must be `true` before the controller agrees to start with `AUTH_PROVIDER=none`. A safety catch. |
| `OIDC_ISSUER` | not set | Your identity provider's issuer address. Required with `oidc`. |
| `OIDC_AUDIENCE` | not set | The audience (`aud`) Watchtower's access tokens carry. Set it: without it, any token from your provider that has a Watchtower role is accepted, even one issued for another application. |
| `OIDC_ROLE_CLAIM` | `roles` | Where in the token the user's roles are. Use dots for nested claims, such as `realm_access.roles`. |
| `OIDC_ROLE_MAP` | not set | Translates your provider's role names to Watchtower's, as `their-role:watchtower-role` pairs separated by commas. Without it, the roles must be called `admin`, `operator` and `viewer`. |
| `OIDC_SUBJECT_CLAIMS` | `preferred_username,email,sub` | Which claim names the user in the dashboard and the audit log; the first one present is used. |
| `OIDC_CLIENT_ID` | not set | The public client the dashboard uses. Setting it shows the **Sign in with SSO** button. Register `https://<controller>/ui/` as its redirect address. |
| `OIDC_SCOPE` | `openid profile email` | The scopes the dashboard asks for when signing in. |
| `OIDC_JWKS_URL` | discovered | Where the provider's signing keys are. Normally found automatically from the issuer. |
| `OIDC_ALGORITHMS` | `RS256,ES256,PS256` | Accepted token signature algorithms. |

### Results

| Setting | Default | What it does |
|---|---|---|
| `RESULTS_URL` | not set | Where workers send results for cameras that don't set their own results URL. The usual place to set it. |

### Scheduling and worker health

These control how quickly Watchtower reacts to changes and failures. The defaults suit most installations.

| Setting | Default | What it does |
|---|---|---|
| `SYNC_INTERVAL_MS` | `5000` | How often workers check in, in milliseconds. Workers learn about new cameras on their next check-in, so a lower value makes changes show up faster but puts more load on the controller. |
| `LEASE_SECONDS` | `60` | How long a worker may be silent before its cameras are given to other workers (plus the grace below). Shorter means faster failover but more needless moves during brief network problems. |
| `OFFLINE_GRACE_SECONDS` | `15` | Extra time added to the lease before a silent worker counts as offline. |
| `PLACEMENT_TIMEOUT_SECONDS` | `60` | If a worker doesn't report a camera it was given within this time, the camera is moved. |
| `MAX_START_ATTEMPTS` | `5` | Failed starts in a row before a camera is marked *failed* and left alone until someone retries it. |
| `RETRY_BACKOFF_SECONDS` | `10` | How long to wait before trying a failed camera again. The wait doubles after each failure, up to 10 minutes. |
| `SCHEDULER_INTERVAL_SECONDS` | `2.0` | How often the scheduler runs when nothing else wakes it up. |
| `SCHEDULER_ENABLED` | `true` | Set to `false` on a controller that should serve the dashboard and API but never schedule. |

### Live updates and multiple controllers

| Setting | Default | What it does |
|---|---|---|
| `REDIS_URL` | not set | Required when you run more than one controller: live dashboard updates are shared through Redis so every user sees every event. |
| `WS_SEND_QUEUE_SIZE` | `100` | Updates buffered per dashboard connection; a browser that falls further behind is disconnected and reconnects. |
| `WS_PING_INTERVAL_SECONDS` | `20` | Keep-alive messages on dashboard connections, so proxies don't close idle ones. |
| `CORS_ALLOW_ORIGINS` | not set | Other web origins allowed to call the API from a browser, separated by commas. The built-in dashboard doesn't need it. |

### Security limits

| Setting | Default | What it does |
|---|---|---|
| `AUTH_FAILURE_LIMIT` | `30` | Failed sign-ins (bad tokens, wrong worker secrets) allowed from one address per window, after which it is refused with `429`. `0` turns the limit off. |
| `AUTH_FAILURE_WINDOW_SECONDS` | `300` | The window for that limit. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-For` header is believed, separated by commas, or `*`. Set it to your reverse proxy's address so the limit and the audit log see real client addresses. Use `*` only when nothing but your proxy can reach the controller. |
| `MAX_REQUEST_BYTES` | `1048576` | Larger request bodies are refused with `413`. `0` turns the limit off. |
| `AUDIT_RETENTION_DAYS` | `365` | How long audit log entries are kept. `0` keeps them forever. |
| `METRICS_PUBLIC` | `false` | Serve `/metrics` without a token. Only on a controller that just your monitoring network can reach. |

### Server and logging

| Setting | Default | What it does |
|---|---|---|
| `HOST`, `PORT` | `0.0.0.0`, `7000` | Where the controller listens. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail while investigating a problem. |
| `LOG_FORMAT` | `text` | `json` for one JSON object per line. |

## Worker

The [Workers guide](workers.md#what-each-worker-setting-does) explains when to change these.

### Connection and identity

| Setting | Default | What it does |
|---|---|---|
| `CONTROLLER_URL` | `http://localhost:7000` | Where the controller is. |
| `WATCHTOWER_TOKEN` | not set | A token with the `worker` role. Required. |
| `WORKER_NAME` | the host name | The name shown in the dashboard. |
| `MAX_STREAMS` | `2` | How many cameras this worker accepts. |
| `STATE_DIR` | `/var/lib/watchtower` | Where the worker keeps its identity and the results waiting to be delivered. Keep it on a persistent volume. |
| `WORKER_ID` | not set | Leave empty; the controller assigns an id when the worker first registers. |
| `CONTROLLER_TIMEOUT_SECONDS` | `10` | How long one request to the controller may take. |
| `ON_LEASE_EXPIRY` | `keep_running` | When the controller has been unreachable for longer than a lease: `keep_running` carries on, `stop` stops all cameras until it is back. |

### Live view

| Setting | Default | What it does |
|---|---|---|
| `PUBLIC_WEBRTC_URL` | not set | The address viewers' browsers use for this worker's live video, such as `http://203.0.113.10:8889`, or `https://cameras-site-a.example.com` with the HTTPS profile. Empty means no live view. |
| `PUBLIC_HOST` | `127.0.0.1` (Compose) | The machine's address as viewers see it; MediaMTX tells browsers to send media there. Used by the Compose file, not by the worker itself. |
| `WEBRTC_DOMAIN` | not set | With `--profile tls`: the DNS name Caddy gets a certificate for. |

### Results delivery

| Setting | Default | What it does |
|---|---|---|
| `DELIVERY_MODE` | `realtime` | The delivery mode for cameras that don't choose one: `realtime` or `reliable`. |
| `REQUIRE_ENCRYPTION` | `false` | Never send results unencrypted: when no key is available, results are dropped instead. |
| `RESULTS_URL` | not set | A last-resort results address, used when neither the camera nor the controller sets one. |
| `REALTIME_QUEUE_SIZE` | `100` | Real-time mode: how many results may wait in memory. |
| `MAX_RESULT_AGE_MS` | `5000` | Real-time mode: results older than this are dropped instead of sent. |
| `SPOOL_MAX_MB` | `512` | Reliable mode: how much disk waiting results may use; beyond it the oldest are removed. |
| `DELIVERY_TIMEOUT_SECONDS` | `5` | How long one delivery may take. |
| `RESULTS_SEND_TOKEN` | `false` | Also send the worker's token in a `watchtower-token` header, for receivers written for Watchtower 1.x. |

### Analysis

| Setting | Default | What it does |
|---|---|---|
| `ENGINE_INPUT` | `mediamtx` | `mediamtx`: analysis reads each camera through the local MediaMTX, so a camera is opened only once. `direct`: analysis opens the camera itself. |
| `DETECTOR_BACKEND` | `onnx` | `onnx` (ONNX Runtime, the fastest; ignores detections below 0.7 confidence) or `opencv` (OpenCV's YuNet). |
| `MAX_ENGINE_RESTARTS` | `3` | How many times a camera's analysis is restarted before the worker reports an error and lets the scheduler try elsewhere. |

### MediaMTX connection

| Setting | Default | What it does |
|---|---|---|
| `MEDIAMTX_ENABLED` | `true` | `false` turns off live view and camera sharing; then also set `ENGINE_INPUT=direct`. |
| `MEDIAMTX_API_URL` | `http://127.0.0.1:9997` | MediaMTX's control interface. |
| `MEDIAMTX_RTSP_URL` | `rtsp://127.0.0.1:8554` | Where analysis reads cameras from MediaMTX. |
| `MEDIAMTX_USERNAME`, `MEDIAMTX_PASSWORD`, `MEDIAMTX_BEARER_TOKEN` | not set | Credentials for MediaMTX's control interface, if you set it up to require them. |

### Health and logging

| Setting | Default | What it does |
|---|---|---|
| `HEALTH_HOST`, `HEALTH_PORT` | `0.0.0.0`, `8081` | Where `/livez`, `/readyz`, `/metrics` and MediaMTX's sign-in check are served. |
| `HEALTH_BIND` | `127.0.0.1` | Compose only: which host address port 8081 is published on. Set `0.0.0.0` to scrape metrics from another machine. |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `text` | As for the controller. |
| `ORT_DISABLE_TELEMETRY` | `1` | Keeps ONNX Runtime from sending usage telemetry to Microsoft. Set by the worker itself; leave it alone. |

## Token server

| Setting | Default | What it does |
|---|---|---|
| `TOKEN_SERVER_HOST`, `TOKEN_SERVER_PORT` | `127.0.0.1`, `9000` (`0.0.0.0` in the image) | Where it listens. |
| `TOKEN_SERVER_DATA_DIR` | `data` (`/data` in the image) | Where it stores token hashes and key pairs. Keep it on a persistent volume and back it up. |
| `TOKEN_SERVER_ADMIN_KEY` | not set | Turns on the admin API under `/v1/admin/`; send it as a bearer token. |
| `TOKEN_SERVER_MASTER_KEY` | not set | Encrypts the stored private keys. Generate with `watchtower-token-server gen-master-key`. |
| `TOKEN_SERVER_BOOTSTRAP` | `true` | Create an admin and a worker token when the store is empty. |
| `TOKEN_SERVER_BOOTSTRAP_TOKENS` | not set | Tokens to create at start if missing, as `role:token:subject` entries separated by commas. |
| `TOKEN_SERVER_INGEST_ENABLED` | `false` | Also receive results at `/v1/ingest`, decrypt them and log them. |
| `TOKEN_SERVER_FORWARD_URL` | not set | Forward decrypted results to this address. |
| `TOKEN_SERVER_SAVE_IMAGES_DIR` | not set | Save the pictures of received results here. |
| `TOKEN_SERVER_LOG_LEVEL` | `INFO` | `DEBUG` for more detail. |

## MediaMTX

[`deploy/mediamtx/mediamtx.yml`](../deploy/mediamtx/mediamtx.yml) is written for a MediaMTX that shares the worker's
network. Any of its settings can be overridden with an environment variable named `MTX_` followed by the setting
name in capitals, for example `MTX_WEBRTCADDITIONALHOSTS`. The ones you may need:

| Setting | What it does |
|---|---|
| `MTX_WEBRTCADDITIONALHOSTS` | The address viewers reach the machine at. The Compose file fills it from `PUBLIC_HOST`. |
| `MTX_WEBRTCICESERVERS2_0_URL`, `_USERNAME`, `_PASSWORD` | A TURN relay for viewers behind strict firewalls. |
| `MTX_WEBRTCADDRESS`, `MTX_WEBRTCLOCALUDPADDRESS`, `MTX_WEBRTCLOCALTCPADDRESS` | Different ports for live video, for example to run two workers on one machine. |

## Per-camera settings

Each camera has its own settings, changed in the dashboard or with the API (the `config` object). Changing them
restarts only that camera's analysis. [Cameras](cameras.md#camera-settings) explains each one in detail.

| Setting | Default | Allowed values | In short |
|---|---|---|---|
| `fps` | `8` | 1 to 60, or `null` | Frames analysed per second; `null` analyses every frame. |
| `detector_resolution` | `320` | 320, 416, 512, 640 | Detector size. Bigger finds smaller faces and costs more. |
| `detection_confidence` | `0.9` | 0 to 1 | How sure the detector must be. |
| `max_faces` | `20` | 1 to 20 | Faces analysed per frame. |
| `max_frame_width` | `1280` | 160 to 7680, or `null` | Wider frames are shrunk first; also the size of the full-frame picture. |
| `face_crop_size` | `224` | 64 to 1024 | Longest side of the face picture, in pixels. |
| `face_crop_margin` | `0.1` | 0 to 1 | Extra room around the face in the picture. |
| `include_frame` | `true` | true, false | Send the full frame with each result. |
| `snapshot` | `best` | `best`, `first`, `last` | Which picture of the face to send. |
| `results_interval_ms` | `1000` | 100 to 120000 | Shortest time between results for one face. |
| `track_confirm_frames` | `5` | 1 to 100 | Frames in a row before a face counts. |
| `track_max_missed_frames` | `8` | 1 to 300 | Frames a face may be missing before it has left. |
| `reconnect_interval_s` | `5` | 1 to 3600 | Seconds between reconnection attempts. |
| `reconnect_attempts` | `5` | 1 to 1000 | Reconnection attempts before giving up. |
| `delivery_mode` | the worker's | `realtime`, `reliable` | How results are delivered. |
| `results_url` | the controller's | an `http(s)` address | Where this camera's results go. |
