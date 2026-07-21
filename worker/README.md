# Video-Worker

> A lightweight, production-ready **video analytics worker** that manages camera streams, runs a pluggable processing engine in a separate process, and exposes a clean REST API for lifecycle management. It integrates out-of-the-box with **MediaMTX** (for ingest + WebRTC viewing) and can **announce/heartbeat** to a central **Controller** service.

---

## Table of Contents
- [Key Capabilities](#key-capabilities)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [Local Run (Python)](#local-run-python)
  - [Docker Compose (with MediaMTX)](#docker-compose-with-mediamtx)
- [Configuration](#configuration)
  - [Environment Variables (.env)](#environment-variables-env)
  - [Runtime Defaults \& Types](#runtime-defaults--types)
  - [Security Notes](#security-notes)
- [API Reference](#api-reference)
  - [Health](#health)
  - [Streams](#streams)
  - [Internal](#internal)
  - [OpenAPI UI](#openapi-ui)
- [Stream Lifecycle](#stream-lifecycle)
- [MediaMTX Integration](#mediamtx-integration)
- [Controller Announce \& Heartbeat](#controller-announce--heartbeat)
- [Persistence \& Shutdown Semantics](#persistence--shutdown-semantics)
- [Logging \& Observability](#logging--observability)
- [Performance Tuning](#performance-tuning)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [License](#license)

---

## Key Capabilities

- **Stream lifecycle API**: assign, start/stop, inspect, and delete streams.
- **External process isolation**: `StreamEngineManager` starts/stops the engine in a separate process for robustness.
- **MediaMTX integration**: registers paths on MediaMTX and returns a ready-to-view **WebRTC (WHEP)** URL.
- **Controller support**: announces itself on startup and periodically heartbeats with host info.
- **SQLite state**: simple built-in persistence via SQLite; clean wipe on startup by default (configurable when you wire it).
- **Safe shutdown**: internal shutdown endpoint with a one-time runtime key; cleanly stops streams and removes MediaMTX paths.
- **CORS-friendly**: permissive defaults, configurable via env.
- **Typed configuration**: Pydantic `BaseSettings` for env-driven configuration.

---

## Architecture

```
   +-----------------+            +-----------------+
   |     Client      |  REST API  |   Video-Worker  |
   |  (Controller /  +----------->|  (FastAPI app)  |
   |   UI / Tools)   |            +--------+--------+
   +--------+--------+                     |
            |                              | start/stop processes
            |                              v
            |                     +-----------------------+
            |                     |  StreamEngineManager  |
            |                     |  (separate process)   |
            |                     +-----------+-----------+
            |                                 |
            |  register/delete path           |
            |  return WebRTC URL              v
            |                         +---------------+
            +------------------------>|   MediaMTX    |
                                      | (RTSP/WHEP)   |
                                      +---------------+
```

- **FastAPI app** exposes endpoints.
- **StreamEngineManager** spawns/joins the actual per-stream processing engine in a separate process.
- **MediaMTXService** registers/deletes ingest paths and prepares a viewable **WebRTC** URL.
- **SQLite** stores stream rows and runtime metadata (pid, status, errors, webrtc_url, etc.).

---

## Quick Start

### Prerequisites

- Python **3.11+**
- FFmpeg / OpenCV runtime libs as required by your engine
- (Optional) **MediaMTX** server reachable by Video-Worker
- (Optional) A **Controller** endpoint if you want announce/heartbeat

### Local Run (Python)

```bash
# 1) Create & activate a virtualenv (recommended)
python -m venv .venv
source .venv/bin/activate

# 2) Install deps
pip install -U pip wheel
pip install -r requirements.txt  # ensure FastAPI, Uvicorn, Pydantic, etc.

# 3) Copy env and edit
cp .env.video-worker.example .env

# 4) Run the app
python -m app.main
# or (if you prefer direct uvicorn)
# uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8081 --log-level info
```

You should see logs similar to:

```
[startup] worker_url=http://<detected-host>:8081
[startup] worker_id=<hostname>
[startup] worker_health_url=/
[startup] Announced to controller
```

Visit:
- **http://localhost:8081/health** → `{"status":"ok"}`
- **http://localhost:8081/docs** for OpenAPI UI

### Docker Compose (with MediaMTX)

Below is a minimal compose that runs **Video-Worker** + **MediaMTX** locally. Adjust ports and env as needed.

```yaml
version: "3.9"
services:
  mediamtx:
    image: bluenviron/mediamtx:latest
    container_name: mediamtx
    restart: unless-stopped
    ports:
      - "8554:8554"   # RTSP
      - "8889:8889"   # WHEP (WebRTC HTTP egress)
    environment:
      - MTX_PROTOCOLS=tcp

  video-worker:
    build: .
    container_name: video-worker
    depends_on:
      - mediamtx
    restart: unless-stopped
    env_file:
      - ./.env
    ports:
      - "8081:8081"
    environment:
      # Example: point worker to MediaMTX in compose network
      - MTX_URL=http://mediamtx:9997/
      - MTX_WEBRTC_BASE=http://mediamtx:8889/
      - CONTROLLER_URL=         # set if you have a controller
```

> Tip: MediaMTX’s **on-the-fly path config API** typically listens at `:9997` when enabled. Ensure your MediaMTX image/config exposes this, or adjust `MTX_URL` accordingly.

---

## Configuration

All configuration is defined in `config.Config` (Pydantic `BaseSettings`), loaded from **environment variables**. The configuration is organized into logical sections for better management.

### Configuration Reference

#### Application Settings

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| `APP_NAME` | Name of the application | `"Video Worker"` | String |
| `HOST` | Host address to bind the server | `"0.0.0.0"` | String |
| `HOST_PORT` | Port to run the server on | `8081` | Integer |
| `LOGS_LEVEL` | Logging level | `"info"` | Enum: `debug`, `info`, `warning`, `error` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) | `"*"` | String |
| `AUTHORIZATION_KEY` | Optional shared secret for authentication | `""` | String |

#### Controller & Heartbeat

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| `CONTROLLER_URL` | Base URL of the controller service | `""` | URL |
| `BROADCAST_URL` | Base URL for broadcasting results | `""` | URL |
| `WORKER_ID` | Unique identifier for this worker | System hostname | String |
| `WORKER_HEALTH_URL` | Health check endpoint path | `"/"` | String |
| `ANNOUNCE_TIMEOUT_SECS` | Timeout for announcement to controller | `5.0` | Float |
| `ANNOUNCE_RETRIES` | Number of retry attempts for announcement | `3` | Integer |
| `ANNOUNCE_RETRY_INTERVAL` | Delay between announcement retries (seconds) | `2` | Integer |
| `HEARTBEAT_INTERVAL` | Interval between heartbeats (seconds) | `30` | Integer |
| `HEARTBEAT_RETRY_INTERVAL` | Delay between heartbeat retries (seconds) | `2` | Integer |
| `HEARTBEAT_RETRY_ATTEMPTS` | Number of heartbeat retry attempts | `3` | Integer |
| `MAX_ALLOWED_STREAMS` | Maximum number of concurrent streams | `2` | Integer |

#### Database

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| `SQLITE_PATH` | Path to SQLite database file | `"worker.sqlite3"` | String |

#### Video Processing

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| `OPENCV_THREADS` | Number of OpenCV worker threads | `1` | Integer |
| `OPENCV_OPTIMIZATION` | Enable OpenCV optimizations | `true` | Boolean |
| `RESULTS_INTERVAL` | Interval between processing results (seconds) | `1.0` | Float |
| `FACE_DETECTION_CONFIDENCE` | Minimum confidence for face detection | `0.5` | Float |
| `FACE_DETECTOR_RESOLUTION` | Resolution for face detection | `320` | Integer |
| `MAX_ALLOWED_DETECTIONS` | Maximum number of detections per frame | `100` | Integer |
| `MAX_FRAME_RESOLUTION` | Maximum frame resolution (width or height) | `1280` | Integer |
| `MAX_CROPPED_FACE_RESOLUTION` | Maximum size for cropped faces | `224` | Integer |
| `FRAME_TO_RETURN` | Type of frame to return in results | `"none"` | Enum: `"none"`, `"full"`, `"cropped"` |
| `STREAMING_FPS` | Target frames per second for processing | `10` | Integer |
| `CAMERA_RECONNECT_INTERVAL` | Delay between reconnection attempts (seconds) | `10` | Integer |
| `CAMERA_RECONNECT_ATTEMPTS` | Maximum number of reconnection attempts | `5` | Integer |

#### YuNet Face Detector

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| `YUNET_CHOICE` | Face detector implementation | `"onnx"` | Enum: `"onnx"`, `"cv"` |
| `YUNET_MODEL_PATH` | Path to YuNet model file | `"app/weights/face_detection_yunet.onnx"` | String |
| `YUNET_MODEL_INPUT_SIZE` | Input size for YuNet model | `320` | Integer |
| `YUNET_MODEL_INPUT_SIZE_RATIO` | Aspect ratio for model input | `1.333` | Float |
| `YUNET_BACKEND_TARGET` | Backend target for YuNet | `0` | Integer |
| `YUNET_CONF_THRESHOLD` | Confidence threshold for face detection | `0.7` | Float |
| `YUNET_NMS_THRESHOLD` | Non-maximum suppression threshold | `0.4` | Float |
| `YUNET_TOP_K` | Maximum number of detections to keep | `20` | Integer |

#### DeepSORT Tracker

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| `DEEPSORT_MAX_AGE` | Maximum frames to keep a track without updates | `1` | Integer |
| `DEEPSORT_N_INIT` | Number of detections before a track is confirmed | `10` | Integer |
| `DEEPSORT_MAX_IOU_DISTANCE` | Maximum IOU distance for matching | `0.7` | Float |
| `DEEPSORT_NMX_MAX_OVERLAP` | Maximum overlap for non-maximum suppression | `1.0` | Float |
| `DEEPSORT_MAX_COSINE_DISTANCE` | Maximum cosine distance for feature matching | `0.2` | Float |
| `DEEPSORT_EMBEDDER_GPU` | Use GPU for feature embedding | `false` | Boolean |

#### MediaMTX Integration

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| `MTX_ENABLE` | Enable MediaMTX integration | `true` | Boolean |
| `MTX_URL` | Base URL of MediaMTX API | `"http://localhost:9997/"` | URL |
| `MTX_TIMEOUT_SEC` | Timeout for MediaMTX API calls | `90` | Integer |
| `MTX_RETRY_COUNT` | Number of retry attempts for API calls | `2` | Integer |
| `MTX_ADD_PATH` | API path for adding paths | `"v3/config/paths/add/"` | String |
| `MTX_DELETE_PATH` | API path for deleting paths | `"v3/config/paths/delete/"` | String |
| `MTX_LIST_PATH` | API path for listing paths | `"v3/config/paths/list/"` | String |
| `MTX_WEBRTC_BASE` | Base URL for WebRTC streaming | `"http://localhost:8889/"` | URL |
| `MTX_USERNAME` | Username for MediaMTX authentication | `""` | String |
| `MTX_PASSWORD` | Password for MediaMTX authentication | `""` | String |
| `MTX_BEARER_TOKEN` | Bearer token for authentication | `""` | String |
| `MTX_AUTH_URL` | URL for authentication | `""` | URL |
| `MTX_AUTH_FETCH` | Enable auth token fetching | `false` | Boolean |
| `MTX_ENC_USERNAME` | Encrypted username | `""` | String |
| `MTX_ENC_PASSWORD` | Encrypted password | `""` | String |

### Security Notes

- **INTERNAL_SHUTDOWN_KEY**: A random key is generated on each boot if not provided. To enable **remote** programmatic shutdown in ops, set a strong static key via env and call the internal endpoint over a **trusted** network only.
- **AUTHORIZATION_KEY**: Present for potential shared-secret auth. The shipped endpoints in `app.main` don't enforce it—if you need auth, add `Depends(...)` or middleware.
- **CORS**: Defaults to `"*"`. Narrow this for production environments.
- **Authentication**: When using MediaMTX with authentication, ensure proper credentials are provided via `MTX_USERNAME`/`MTX_PASSWORD` or `MTX_BEARER_TOKEN`.

### Runtime Defaults & Types

Your `Config` class uses Pydantic to coerce types. Although many defaults are derived from `os.getenv(...)`, Pydantic will parse them into the defined type annotations (e.g., `int`, `float`, `bool`). When you override values via real environment variables, keep types in mind:
- Booleans: use `true/false` or `1/0` (Pydantic parses case-insensitively)
- Numbers: plain integers/floats (no quotes)
- Lists (e.g., `CORS_ORIGINS`): comma-separated string

### Security Notes

- **INTERNAL_SHUTDOWN_KEY**: A random key is generated on each boot if not provided. To enable **remote** programmatic shutdown in ops, set a strong static key via env and call the internal endpoint over a **trusted** network only.
- **AUTHORIZATION_KEY**: Present for potential shared-secret auth. The shipped endpoints in `app.main` don’t enforce it—if you need auth, add `Depends(...)` or middleware.
- **CORS**: Defaults to `"*"`. Narrow this for production.

---

## API Reference

> Base URL defaults to `http://<host>:8081` unless overridden by `HOST`/`HOST_PORT`.

### Health

#### `GET /health`
Returns app availability.

**Response**
```json
{ "status": "ok" }
```

---

### Streams

#### `POST /streams/assign`
Create a stream row, register the source with MediaMTX, and start the processing engine.

**Request body**
```json
{
  "stream_id": "cam-lobby-01",
  "source_url": "rtsp://user:pass@10.0.0.12:554/stream"
}
```
**Success (200)**
```json
{
  "stream_id": "cam-lobby-01",
  "source_url": "rtsp://user:pass@10.0.0.12:554/stream",
  "status": "running",
  "webrtc_url": "http://<mediamtx>:8889/whep/cam-lobby-01",
  "pid": 12345,
  "error": null,
  "...": "... other model fields depending on your StreamOut schema ..."
}
```
**Error (400)**
```json
{ "detail": "Failed to start stream: <reason>" }
```

> Notes
> - If a row with the same **source_url** exists, the request is rejected (`400`).  
> - On MediaMTX registration failure, the DB row is deleted and an error is returned.

---

#### `GET /streams/list`
Returns all streams (summaries).

**Response (200)**
```json
[
  {
    "stream_id": "cam-lobby-01",
    "status": "running",
    "webrtc_url": "http://<mediamtx>:8889/whep/cam-lobby-01"
  }
]
```

---

#### `GET /streams/info/{stream_id}`
Returns a detailed view of a stream.

**Response (200)**
```json
{
  "stream_id": "cam-lobby-01",
  "source_url": "rtsp://...",
  "status": "running",
  "webrtc_url": "http://...",
  "pid": 12345,
  "error": null,
  "created_at": "..."
}
```

**Error (404)**
```json
{ "detail": "Not found" }
```

---

#### `POST /streams/start/{stream_id}`
Registers the path on MediaMTX (if needed), stores the **WebRTC URL**, and starts the engine if not already running.

**Response**
```json
{ "started": true }
```
or
```json
{ "message": "Already running" }
```

---

#### `POST /streams/stop/{stream_id}`
Stops the engine if running, marks the DB row as stopped, and deletes the MediaMTX path.

**Response**
```json
{ "stopped": true }
```
or
```json
{ "message": "Already stopped or not found" }
```

---

#### `DELETE /streams/delete/{stream_id}`
Stops the engine if needed, deletes the MediaMTX path, and **removes the DB row**.

**Response**
```json
{ "deleted": true }
```
or
```json
{ "message": "Already deleted or not found" }
```

---

#### `POST /streams/error/{stream_id}`
Helper to mark a stream as errored. Stops if running and deletes the MediaMTX path.

**Response**
```json
{ "stopped": true }
```

---

### Internal

#### `POST /internal/shutdown/{internal_key}`
Gracefully stop and delete **all** streams, remove MediaMTX paths, and terminate the worker process.

**Response**
```json
{ "shutting_down": true }
```

> Linux/Docker sends itself `SIGINT`. Windows uses `os._exit(1)` to ensure termination.

---

### OpenAPI UI

- **Swagger UI**: `GET /docs`
- **ReDoc**: `GET /redoc`

---

## Stream Lifecycle

1. **Assign**
   - Creates a DB row and checks for duplicate `source_url`.
   - Registers a **path** on MediaMTX; obtains a **WebRTC WHEP** URL.
   - Starts the worker engine via `StreamEngineManager.start()` and records PID/status.
2. **Run/Observe**
   - Engine performs per-frame processing (face detection/tracking, etc.).
   - Results can be pushed to your Controller/Broadcast sink if configured.
3. **Stop**
   - Stops the process; DB status updated.
   - MediaMTX path is deleted.
4. **Delete**
   - Same as Stop + **remove DB row**.
5. **Error**
   - Helper endpoint to fail-fast, stop the engine, mark status `error`, and clean up MediaMTX.

---

## MediaMTX Integration

- **Add path**: `MTX_URL` + `MTX_ADD_PATH`
- **Delete path**: `MTX_URL` + `MTX_DELETE_PATH`
- **List paths**: `MTX_URL` + `MTX_LIST_PATH`
- WebRTC **view** base: `MTX_WEBRTC_BASE` (WHEP). The worker returns something like:
  - `http://<mtx-host>:8889/whep/<stream_id>`

> Auth flows are supported via basic credentials or bearer token. If you need dynamic token fetching, set `MTX_AUTH_FETCH=true` and implement `MTX_AUTH_URL` handling in `MediaMTXService` (placeholder present).

---

## Controller Announce & Heartbeat

On startup (inside the FastAPI `lifespan` context):

- Gathers host information (`collect_host_info()`).
- Auto-detects a callback URL via `worker_callback_url_auto()`.
- Calls `announce_with_retries(host_info)` against your **Controller** (if configured).
- Spawns a background task `announce_heartbeat(...)` at `HEARTBEAT_INTERVAL` seconds.

> If announce fails after retries, the worker **aborts startup** to avoid a hidden zombie process.

---

## Persistence & Shutdown Semantics

- Startup currently calls `initialize_db(..., wipe_db=True)` and then **clears all rows** with `db.query(Stream).delete()`. This ensures a clean state for each boot.
- On normal shutdown (and via `/internal/shutdown/...`):
  - `STREAM_MANAGER.stop_all(force=True)` is invoked.
  - MediaMTX paths for all streams are removed.
  - The SQLite file is removed if it exists.

If you want non-ephemeral state:
- Remove or make `wipe_db` configurable.
- Skip deleting the SQLite file on shutdown.

---

## Logging & Observability

- Log level is controlled by `LOGS_LEVEL` (**debug|info|warning|error**).  
- The code uses a central `configure_logging()` and includes informative `[startup]`, `[assign_stream]`, and `[start_stream]` markers.
- Add Prometheus/OTel as needed; FastAPI middlewares or engine worker metrics can be added later.

---

## Performance Tuning

- `OPENCV_THREADS`, `OPENCV_OPTIMIZATION`: control OpenCV threading/optimizations.
- Detector knobs:
  - `YUNET_MODEL_INPUT_SIZE` (320/416/512/640) – larger => better accuracy, higher cost.
  - `YUNET_CONF_THRESHOLD`, `YUNET_NMS_THRESHOLD`, `YUNET_TOP_K`.
- Tracking knobs (DeepSORT):
  - `DEEPSORT_*` family—trade precision vs throughput.
- Stream limits:
  - `MAX_ALLOWED_STREAMS`: hard guardrail at the API layer if you enforce it in your manager.
- Engine FPS / frame throttling:
  - `STREAMING_FPS`, `RESULTS_INTERVAL`.

---

## Troubleshooting

### “Failed to announce to controller” on startup
- Ensure `CONTROLLER_URL` is set and reachable.
- Check controller logs and any auth expectations.
- Disable announce temporarily if running standalone.

### “Failed to register stream to mtex” / MediaMTX errors
- Confirm `MTX_URL` is correct and the **config API** is enabled in your MediaMTX deployment.
- Verify credentials or bearer token.
- Try listing paths with `MTX_LIST_PATH` to verify connectivity.

### WebRTC URL returned but no video
- Confirm your source (`source_url`) is valid and reachable by **MediaMTX**.
- Check NAT/Firewall for WebRTC ports and HTTP endpoint (`MTX_WEBRTC_BASE`).
- Confirm the engine actually started (`pid` present) and hasn't crashed.

### Duplicate source error on assign
- The API rejects duplicate `source_url` entries by design.

### SQLite file not removed on shutdown
- On some environments, other processes may hold locks. The worker logs a warning if deletion fails; remove it manually.

---

## FAQ

**Q: Can I change the process start method?**  
Yes. The app sets `multiprocessing.set_start_method("spawn")`. If already set, it’s ignored. Adjust to your platform needs.

**Q: How do I plug in a different detection/tracking model?**  
Implement or extend your engine module that `StreamEngineManager` launches. Keep the same DB contracts (`set_stream_started`, `set_stream_stopped`, `set_stream_error`) and you’re good.

**Q: Do I have to use MediaMTX?**  
No. `MediaMTXService` is a thin adapter. You can replace it with another “ingest + view” manager and keep the same API flow.

**Q: Where does the WebRTC URL come from?**  
From `MTX_WEBRTC_BASE` + `/whep/<stream_id>` (subject to your MediaMTX config). The worker stores it in the DB for convenience.

---

## License

This repository is provided under your selected license (MIT/Apache-2.0/BSD-3-Clause). Update this section accordingly.