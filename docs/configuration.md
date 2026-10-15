# Configuration

Every service reads environment variables (names are case-insensitive) and, if
present, a `.env` file in its working directory.

## Controller

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string. |
| `MONGO_DB` | `watchtower` | Database name. |
| `AUTH_PROVIDER` | `http` | `http` (token server), `static` (tokens from config), `oidc` (identity provider JWTs) or `none`; or a comma-separated chain tried in order, e.g. `oidc,static`. See [token-server.md](token-server.md). |
| `TOKEN_SERVER_URL` | — | Verify endpoint, required for `http`. |
| `TOKEN_CACHE_SECONDS` | `60` | How long a token's verification is cached. Also bounds how fast a revocation takes effect. |
| `STATIC_TOKENS` | — | For `static`: comma-separated `role:token` entries. |
| `STATIC_TOKENS_FILE` | — | For `static`: file with one `role:token` per line (`#` comments allowed). |
| `RESULT_PUBLIC_KEY_FILE` | — | PEM public key workers encrypt results with, when the token server supplies none. |
| `ALLOW_NO_AUTH` | `false` | Must be `true` to start with `AUTH_PROVIDER=none`. |
| `OIDC_ISSUER` | — | Identity provider issuer URL; required for `oidc`. |
| `OIDC_AUDIENCE` | — | Expected `aud` of access tokens. Strongly recommended. |
| `OIDC_JWKS_URL` | discovered | Where the provider's signing keys are, if not found through discovery. |
| `OIDC_ROLE_CLAIM` | `roles` | Claim with the user's roles; dot path, e.g. `realm_access.roles`. |
| `OIDC_ROLE_MAP` | — | `idp-role:watchtower-role,...`; empty means claim values are Watchtower role names. |
| `OIDC_SUBJECT_CLAIMS` | `preferred_username,email,sub` | First present claim names the user in the dashboard and audit log. |
| `OIDC_ALGORITHMS` | `RS256,ES256,PS256` | Accepted signature algorithms. |
| `OIDC_CLIENT_ID` | — | Public client the dashboard uses for "Sign in with SSO" (redirect URI `<controller>/ui/`). |
| `OIDC_SCOPE` | `openid profile email` | Scopes the dashboard asks for. |
| `RESULTS_URL` | — | Default results URL for streams that don't set one. |
| `SYNC_INTERVAL_MS` | `5000` | How often workers sync. Lower = faster reaction, more load. |
| `LEASE_SECONDS` | `60` | A worker silent this long (plus the grace) loses its streams. |
| `OFFLINE_GRACE_SECONDS` | `15` | Extra margin before a silent worker is declared offline. |
| `SCHEDULER_ENABLED` | `true` | `false`: this replica serves the API but never schedules. |
| `SCHEDULER_INTERVAL_SECONDS` | `2.0` | Scheduler tick. |
| `PLACEMENT_TIMEOUT_SECONDS` | `60` | A placed stream not reported by its (online) worker by then is moved. |
| `MAX_START_ATTEMPTS` | `5` | Failed starts before a stream is marked `failed`. |
| `RETRY_BACKOFF_SECONDS` | `10` | First retry delay; doubles per attempt, capped at 10 minutes. |
| `REDIS_URL` | — | Set when running more than one controller replica, so live events reach every dashboard. Needs the `redis` extra (`watchtower-controller[redis]`). |
| `WS_SEND_QUEUE_SIZE` | `100` | Events buffered per dashboard connection before a slow client is dropped. |
| `WS_PING_INTERVAL_SECONDS` | `20` | Keep-alive for event connections. |
| `CORS_ALLOW_ORIGINS` | — | Comma-separated origins allowed to call the API from browsers. Not needed for the built-in dashboard. |
| `REQUEST_TIMEOUT_SECONDS` | `10` | Timeout for token-server calls. |
| `AUTH_FAILURE_LIMIT` | `30` | Failed sign-ins (bad tokens, wrong worker secrets) allowed per client address per window before it gets `429`. `0` disables. |
| `AUTH_FAILURE_WINDOW_SECONDS` | `300` | Window for that limit. |
| `AUDIT_RETENTION_DAYS` | `365` | How long audit entries are kept; `0` keeps them forever. |
| `MAX_REQUEST_BYTES` | `1048576` | Larger request bodies get `413`. `0` disables. |
| `METRICS_PUBLIC` | `false` | Serve `/metrics` without a token. Leave off unless the port is internal; Prometheus can send a viewer token (`authorization: {credentials: …}`). |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-For` header is trusted (comma-separated, or `*`). Set it to your reverse proxy's address; client addresses feed the rate limit. Only use `*` if nothing but your proxy can reach the controller. |
| `HOST` / `PORT` | `0.0.0.0` / `7000` | Listen address. |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `text` | `json` for one JSON object per line. |

## Worker

| Variable | Default | Purpose |
|---|---|---|
| `CONTROLLER_URL` | `http://localhost:7000` | Where the controller is. |
| `WATCHTOWER_TOKEN` | — | A token with the `worker` role. |
| `WORKER_NAME` | host name | Shown in the dashboard. |
| `WORKER_ID` | — | Fixed id. Normally left empty: the controller assigns one and the worker keeps it in `STATE_DIR`. |
| `STATE_DIR` | `/var/lib/watchtower` | Identity and the result spool. Persist it (a volume). |
| `MAX_STREAMS` | `2` | Cameras this worker accepts. As a starting point, one 720p camera at the defaults (8 fps, 320 px detector) measured about 6% of one x86 core and 130 MB of RAM; larger detectors, higher fps and 1080p+ cost more. Measure on your hardware. |
| `PUBLIC_WEBRTC_URL` | — | Base URL viewers' browsers use for this worker's MediaMTX, e.g. `http://203.0.113.10:8889`. Without it there is no live view. |
| `ON_LEASE_EXPIRY` | `keep_running` | What to do when the controller has been unreachable for a lease: `keep_running` or `stop`. See [architecture.md](architecture.md#failure-handling). |
| `CONTROLLER_TIMEOUT_SECONDS` | `10` | Per request. |
| `ENGINE_INPUT` | `mediamtx` | `mediamtx`: engines read the camera through MediaMTX (one connection per camera). `direct`: engines open the camera themselves. |
| `DETECTOR_BACKEND` | `onnx` | `onnx` (onnxruntime + bundled post-processor; ignores confidences below 0.7) or `opencv` (OpenCV's FaceDetectorYN). |
| `MAX_ENGINE_RESTARTS` | `3` | Engine restarts before the stream is reported as `error`. |
| `DELIVERY_MODE` | `realtime` | Default for streams that don't set one: `realtime` or `reliable`. See [results.md](results.md#delivery-modes). |
| `RESULTS_URL` | — | Last-resort results URL. |
| `REQUIRE_ENCRYPTION` | `false` | Never send results in plaintext: drop them if no key is available. |
| `RESULTS_SEND_TOKEN` | `false` | Also send the worker token as `watchtower-token` with results (older receivers). |
| `REALTIME_QUEUE_SIZE` | `100` | `realtime` queue length. |
| `MAX_RESULT_AGE_MS` | `5000` | `realtime` results older than this are dropped instead of sent. |
| `SPOOL_MAX_MB` | `512` | `reliable` spool size; the oldest results are evicted beyond it. |
| `DELIVERY_TIMEOUT_SECONDS` | `5` | Per results request. |
| `MEDIAMTX_ENABLED` | `true` | `false` disables live view and path management. |
| `MEDIAMTX_API_URL` | `http://127.0.0.1:9997` | MediaMTX control API. |
| `MEDIAMTX_RTSP_URL` | `rtsp://127.0.0.1:8554` | Where engines read re-streamed cameras. |
| `MEDIAMTX_USERNAME` / `MEDIAMTX_PASSWORD` / `MEDIAMTX_BEARER_TOKEN` | — | Credentials for the MediaMTX API, if it requires them. |
| `HEALTH_HOST` / `HEALTH_PORT` | `0.0.0.0` / `8081` | `/livez`, `/readyz`, `/metrics` and MediaMTX's auth hook. |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `text` | |

## Token server

See [token-server/README.md](../token-server/README.md#configuration).

## MediaMTX

[`deploy/mediamtx/mediamtx.yml`](../deploy/mediamtx/mediamtx.yml) is written for a
MediaMTX that shares the worker's network namespace. Override any key with an
`MTX_<KEY>` environment variable; the one you will usually need is
`MTX_WEBRTCADDITIONALHOSTS` (the host's address as viewers see it).

## Per-camera settings

Sent as `config` when creating or updating a stream (all optional). Changing any of
them restarts that camera's engine.

| Key | Default | Range | Purpose |
|---|---|---|---|
| `fps` | `8` | 1–60 or `null` | Frames analysed per second; `null` = as fast as the camera sends. |
| `detector_resolution` | `320` | 320, 416, 512, 640 | Detector input width. Larger finds smaller faces, costs more CPU. |
| `detection_confidence` | `0.9` | 0–1 | Minimum face score. |
| `max_faces` | `20` | 1–20 | Faces per frame. |
| `max_frame_width` | `1280` | 160–7680 or `null` | Wider frames are downscaled first (also the size of `photo`). |
| `face_crop_size` | `224` | 64–1024 | Long side of the face crop. |
| `face_crop_margin` | `0.1` | 0–1 | Margin around the face box in the crop. |
| `include_frame` | `true` | | Send the full frame with each result. |
| `snapshot` | `best` | `best`, `first`, `last` | Which moment of the track the images show. |
| `results_interval_ms` | `1000` | 100–120000 | Minimum gap between results for one face. |
| `track_confirm_frames` | `5` | 1–100 | Consecutive detections before a face counts. |
| `track_max_missed_frames` | `8` | 1–300 | Missed frames before a face's track ends. |
| `reconnect_interval_s` | `5` | 1–3600 | Wait between camera reconnects. |
| `reconnect_attempts` | `5` | 1–1000 | Reconnects before the engine gives up. |
| `delivery_mode` | worker default | `realtime`, `reliable` | See [results.md](results.md#delivery-modes). |
| `results_url` | controller/worker default | http(s) URL | Where this camera's results go. |
