# Workers

Workers are the machines that analyse your cameras. This guide explains what a worker needs, how to add one step
by step, what each worker setting does, how cameras are shared between workers, and how to service or retire a
worker.

## What a worker needs

**A Linux machine with Docker.** Any 64-bit Intel/AMD (x86_64) or ARM (arm64) machine works: a rack server, a
mini PC on a shop floor, a virtual machine in a cloud. Analysis runs on the processor; no graphics card is needed.

**Processor and memory for its cameras.** As a starting point, one 720p camera at the default settings (8 frames
per second, detector size 320) used about 6% of one processor core and 130 MB of memory in our measurements.
Bigger detector sizes, more frames per second and higher resolutions cost more. A small 4-core machine comfortably
runs around ten cameras at the defaults; measure with your own cameras and settings, and leave headroom.

**Disk space for reliable delivery.** In `reliable` mode, results wait on the worker's disk while your results
application is unreachable, up to 512 MB by default (`SPOOL_MAX_MB`).

**Network access:**

| Direction | To or from | Port | Why |
|---|---|---|---|
| Outgoing | The controller | 443 (or 7000 without HTTPS) | Registering and syncing |
| Outgoing | Your cameras | Usually 554 (RTSP) | Reading video |
| Outgoing | Your results URL | Usually 443 | Delivering results |
| Incoming | People watching live video | 8889/tcp, 8189/udp and 8189/tcp (or 443 with HTTPS) | Live view only; analysis doesn't need it |

Nothing ever connects *in* to a worker to control it. A worker behind NAT or a firewall that only allows outgoing
traffic works fine, as long as you don't need live view from outside that network.

## Adding a worker, step by step

This is the standard way: Docker Compose on the worker machine, using the files in `deploy/compose/worker`. (For
Kubernetes, see [Running on Kubernetes](kubernetes.md).)

### 1. Get a worker token

Each worker proves itself to the controller with a token that has the **worker** role. Several workers may share
one token, but giving each site or machine its own token lets you shut out one of them later without touching the
others.

=== "Bundled token server"

    On the machine that runs the control plane:

    ```bash
    cd deploy/compose/controller
    docker compose exec token-server watchtower-token-server issue --role worker --subject site-a
    ```

    The token is printed once. Copy it now; only a hash of it is stored.

=== "Static tokens"

    If the controller uses `AUTH_PROVIDER=static`, add an entry like `worker:<long random string>` to its
    `STATIC_TOKENS` setting and restart the controller. Generate a random string with
    `openssl rand -base64 32 | tr '+/' '-_'`.

=== "Your own token service"

    Issue a token that your service answers with `"role": "worker"`. See [Token servers](token-server.md).

### 2. Prepare the machine

Install Docker with the Compose plugin, then get the deployment files:

```bash
git clone https://github.com/NaumanHSA/watchtower.git
cd watchtower/deploy/compose/worker
cp .env.example .env
```

!!! note "Building versus pulling images"
    By default the Compose file builds the worker image from the source you cloned, which takes a few minutes the
    first time. Once a release is published you can skip the build by setting `WORKER_IMAGE` in `.env` to the
    published image, for example `ghcr.io/naumanhsa/watchtower-worker:2.0.0`.

### 3. Fill in `.env`

Open `.env` in an editor. The settings you need to look at:

| Setting | Example | What it does |
|---|---|---|
| `CONTROLLER_URL` | `https://watchtower.example.com` | The controller's address, as this machine reaches it. |
| `WATCHTOWER_TOKEN` | the token from step 1 | Proves this machine may be a worker. |
| `WORKER_NAME` | `site-a-1` | The name shown in the dashboard. Pick something that tells you where the machine is. |
| `MAX_STREAMS` | `4` | How many cameras this worker accepts (its slots). See [sizing](#how-many-cameras-per-worker). |
| `PUBLIC_HOST` | `203.0.113.10` | The address viewers' browsers use to reach this machine, for live video. |
| `PUBLIC_WEBRTC_URL` | `http://203.0.113.10:8889` | The live video address the dashboard hands to viewers. Leave it empty to turn live view off. |
| `DELIVERY_MODE` | `realtime` | The delivery mode for cameras that don't choose one. See [Receiving results](results.md#choosing-a-delivery-mode). |
| `REQUIRE_ENCRYPTION` | `true` | Refuse to send results unencrypted. Keep it on unless you have no results key. |

The other settings in the file are optional; [What each worker setting does](#what-each-worker-setting-does)
explains them.

### 4. Start it

```bash
docker compose up -d --build
```

### 5. Check that it joined

Within a few seconds of starting, the worker registers with the controller. Check in three places:

- **The dashboard:** the new worker appears on the **Workers** page as **Online**.
- **The worker's log:** `docker compose logs worker` shows a line like
  `registered as worker w-05f9f953aea8`.
- **The health endpoint:** `curl http://127.0.0.1:8081/readyz` on the worker machine answers
  `{"status": "ok", ...}` once the worker has synced.

New cameras can now be placed on it.

### 6. Open the ports for live view

If people should be able to watch this worker's cameras live, allow incoming **8889/tcp** and **8189/udp** plus
**8189/tcp** in the machine's firewall (and in any cloud security group). Then read
[Live view from a worker](#live-view-from-a-worker), because an HTTPS dashboard needs one more step.

## How many cameras per worker

`MAX_STREAMS` is a promise you make about the machine: the controller never places more cameras than that on it.
Choose it like this:

1. Start low, for example 2 cameras per processor core at the default settings.
2. Add cameras and watch the worker's **CPU** bar on the **Workers** page, and the *FPS* column on the **Cameras**
   page. If a camera's actual frames per second falls clearly below its setting, the machine is overloaded.
3. Keep CPU use below about 70% so there is room for busy moments and for cameras moved here when another worker
   fails.

To change `MAX_STREAMS`, edit `.env` and run `docker compose up -d`. The worker restarts: its cameras move to other
workers while it is away (or wait as *pending* if there is no room elsewhere), and new cameras use the new capacity
once it is back.

## How cameras are shared between workers

When a camera needs a worker, the scheduler looks at every worker that is **online** and **not draining** and has
at least one free slot, and picks the one with the **lowest share of its slots in use**. A worker using 1 of 4
slots (25%) gets the next camera before one using 1 of 2 (50%). If a camera just failed on a worker, another worker
is preferred for the next attempt.

A few consequences worth knowing:

- **Running cameras stay where they are.** Adding a worker doesn't move existing cameras; it takes new cameras and
  cameras that need to move. To spread cameras onto a new worker right away, drain the busiest worker for a moment
  and undrain it.
- **You can't pin a camera to a worker.** Watchtower always decides placement. If some cameras can only be reached
  from one site's network, run a separate Watchtower installation per site, or make sure every worker can reach
  every camera.
- **Plan spare capacity.** When a worker fails, its cameras can only move if other workers have free slots.
  Keeping the total number of slots at least one worker's worth above the number of cameras means a single failure
  never leaves cameras waiting.

## What each worker setting does

All worker settings are environment variables, set in `.env` for Docker Compose. The full list with defaults is
in the [configuration reference](configuration.md#worker).

**Connecting to the controller**

- `CONTROLLER_URL`: where the controller is. Use the same address people use for the dashboard.
- `WATCHTOWER_TOKEN`: the worker token.
- `CONTROLLER_TIMEOUT_SECONDS` (10): how long one request to the controller may take.
- `ON_LEASE_EXPIRY` (`keep_running`): what to do when the controller has been unreachable for longer than a lease
  (about a minute). `keep_running` carries on analysing and delivering, which is right for almost everyone. `stop`
  stops all cameras until the controller is back; choose it only if you run several controllers and would rather
  lose coverage than risk two workers analysing the same camera during a network split.

**Identity and capacity**

- `WORKER_NAME`: the name in the dashboard. Defaults to the machine's host name.
- `MAX_STREAMS` (2): how many cameras this worker accepts.
- `STATE_DIR` (`/var/lib/watchtower`): where the worker keeps its identity and the results waiting to be delivered.
  The Compose file stores it in the `worker-state` volume; keep that volume, or the worker registers as a new worker
  after being recreated.
- `WORKER_ID`: leave empty. The controller assigns an id at registration.

**Live view**

- `PUBLIC_WEBRTC_URL`: the live video address handed to viewers. Empty means no live view.
- `PUBLIC_HOST`: the machine's address as viewers see it. MediaMTX includes it in the connection details it sends
  to browsers.

**Results**

- `DELIVERY_MODE` (`realtime`): the default for cameras that don't choose.
- `REQUIRE_ENCRYPTION` (`false`, but `true` in `.env.example`): never send a result in plain text.
- `RESULTS_URL`: a last-resort results address, used when neither the camera nor the controller sets one.
- `REALTIME_QUEUE_SIZE` (100) and `MAX_RESULT_AGE_MS` (5000): in real-time mode, how many results may wait in
  memory, and how old a result may get before it is dropped instead of sent.
- `SPOOL_MAX_MB` (512): in reliable mode, how much disk the waiting results may use. Beyond it the oldest are
  removed.
- `DELIVERY_TIMEOUT_SECONDS` (5): how long one delivery may take.

**Analysis**

- `ENGINE_INPUT` (`mediamtx`): the analysis reads each camera through the local MediaMTX, so the camera is opened
  only once no matter how many people watch it live. `direct` makes the analysis open the camera itself.
- `DETECTOR_BACKEND` (`onnx`): the face detector engine. `onnx` uses ONNX Runtime and is the fastest; `opencv`
  uses OpenCV's built-in YuNet implementation.
- `MAX_ENGINE_RESTARTS` (3): how many times the worker restarts a camera's analysis before reporting an error and
  letting the scheduler try elsewhere.

**Health and logs**

- Port 8081 serves `/livez`, `/readyz` and `/metrics`. The Compose file publishes it on `127.0.0.1` only; set
  `HEALTH_BIND=0.0.0.0` in `.env` if your monitoring system scrapes it from elsewhere.
- `LOG_FORMAT` (`text`): use `json` if your log system reads JSON.

## Live view from a worker

Live video travels straight from the worker's MediaMTX to the viewer's browser. The controller only checks that
the viewer is allowed to watch (MediaMTX asks the worker, and the worker asks the controller). For it to work:

1. The browser must reach `PUBLIC_WEBRTC_URL` (port 8889 by default) to set up the connection.
2. The browser and the worker must be able to exchange media, normally over **8189/udp** (8189/tcp as a fallback).
   `PUBLIC_HOST` must be the address the browser can reach.

### When the dashboard uses HTTPS

Browsers refuse to load live video from a plain `http://` address into a page served over `https://`. Since a
production dashboard should always use HTTPS, each worker needs an HTTPS address for live view too. The worker's
Compose file includes an optional proxy for this:

1. Give the worker machine a DNS name, for example `cameras-site-a.example.com`, pointing at its public address.
2. Allow incoming **80/tcp** and **443/tcp** (80 is used to obtain the certificate).
3. In `.env` set:

    ```bash
    WEBRTC_DOMAIN=cameras-site-a.example.com
    PUBLIC_WEBRTC_URL=https://cameras-site-a.example.com
    ```

4. Start with the `tls` profile: `docker compose --profile tls up -d`.

Caddy obtains a certificate automatically and forwards viewers to MediaMTX. Media still flows over 8189/udp; it is
encrypted by WebRTC itself.

!!! danger "Don't put your own proxy inside the worker's network"
    If you use another reverse proxy, run it as a separate container or machine, never inside the worker's network
    namespace (`network_mode: service:worker-net`). MediaMTX lets local connections in without a token, because those
    are the worker's own analysis processes; a proxy inside that namespace would make every viewer look local.

### Viewers behind strict firewalls

When viewers sit on networks that block UDP or direct connections (some corporate or mobile networks), add a TURN
relay that both sides can reach, such as [coturn](https://github.com/coturn/coturn), and give it to MediaMTX in
`.env`:

```bash
MTX_WEBRTCICESERVERS2_0_URL=turn:turn.example.com:3478
MTX_WEBRTCICESERVERS2_0_USERNAME=watchtower
MTX_WEBRTCICESERVERS2_0_PASSWORD=<secret>
```

MediaMTX hands the relay details to authorised viewers only, and the dashboard's player uses them automatically.

## Draining a worker for maintenance

Before rebooting, updating or moving a worker, **drain** it: on the **Workers** page click **Drain** on its card
(or call `POST /api/v1/workers/{id}/drain` with `{"draining": true}`). The controller moves its cameras to other
workers straight away and sends it no new ones. When you're done, click **Undrain**.

Stopping a worker normally (`docker compose down`, a system shutdown) also drains it automatically: the worker tells
the controller it is leaving and gives its cameras up to 30 seconds to move before it exits.

## Retiring a worker

1. Drain it, then stop it: `docker compose down` on the worker machine. Add `-v` to delete its state as well.
2. On the **Workers** page it turns **Offline** after about a minute. An admin can then remove it from the list
   with the bin icon (*Forget worker*).
3. If the machine might still be turned on by someone, revoke its token (for the bundled token server:
   `watchtower-token-server list`, then `watchtower-token-server revoke <id>`). A forgotten worker that still has a
   valid token simply registers again when it starts.

## Identity and copying machines

At its first start a worker receives an id and a private secret from the controller and stores them in
`STATE_DIR/identity.json` (the `worker-state` volume). Keep this file with the machine and never copy it to another
one: two machines with the same identity would take turns pretending to be one worker. If you clone a virtual
machine or a disk image that has already run a worker, delete the state volume on the copy before starting it, and
it registers as a new worker.

## Running a worker without Docker

You can run the worker directly with Python 3.11 or newer, for example on a machine where Docker isn't allowed:

```bash
uv sync --package watchtower-worker
CONTROLLER_URL=https://watchtower.example.com WATCHTOWER_TOKEN=... uv run watchtower-worker
```

For live view and the single-connection camera input you also need MediaMTX running on the same machine with
`deploy/mediamtx/mediamtx.yml` (it expects to reach the worker on `127.0.0.1:8081`). Without MediaMTX, set
`MEDIAMTX_ENABLED=false` and `ENGINE_INPUT=direct`: analysis then opens cameras itself and live view is off.

## Watching your workers

- The **Workers** page shows each worker's state, load and delivery counters.
- `docker compose logs -f worker` on the machine shows what it is doing: registration, cameras started and
  stopped, connection problems.
- Each worker serves Prometheus metrics on port 8081 at `/metrics`. [Monitoring and maintenance](operations.md)
  lists the useful ones and suggests alerts.
