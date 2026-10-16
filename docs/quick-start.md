# Try it on your computer

This guide runs the whole of Watchtower on one computer: a controller, a worker, the token server and a fake
camera, all in Docker. Nothing here needs real cameras or a server. Plan on ten to fifteen minutes, most of it
spent waiting for the first build.

By the end you will have added cameras, watched them live, seen face detection results arrive, added a second
worker, and watched Watchtower move cameras off a worker that crashed.

## Before you start

You need:

- **Docker with Compose v2.** On Windows and macOS install [Docker Desktop](https://www.docker.com/products/docker-desktop/);
  on Linux install Docker Engine and the Compose plugin. Check with `docker compose version`.
- **Git**, to download the code.
- **About 4 GB of free memory** and 5 GB of disk space for the images.
- These ports free on your computer: **7000** (dashboard), **9000** (token server), **8081** and **8082**
  (worker health), **8889**, **8890**, **8189** and **8190** (live video). Everything listens on `localhost`
  only, so nothing is exposed to your network.

All commands below are run from the folder you clone into. They work the same in a Linux or macOS terminal, in
PowerShell and in Git Bash on Windows.

## 1. Start Watchtower

```bash
git clone https://github.com/NaumanHSA/watchtower.git
cd watchtower
docker compose -f deploy/compose/dev/compose.yml up --build -d
```

The first run builds three images and downloads MongoDB and MediaMTX, which takes a few minutes. Later starts
take seconds. When the command returns, check that everything is up:

```bash
docker compose -f deploy/compose/dev/compose.yml ps
```

You should see `mongo`, `token-server`, `controller` and `worker` with the status `healthy` or `running`, plus
`worker-net`, `mediamtx`, `camera` and `camera-feed`.

!!! info "What you just started"

    | Service | What it is |
    |---|---|
    | `controller` | The brain and the dashboard, on <http://localhost:7000>. |
    | `mongo` | The controller's database. |
    | `token-server` | Checks sign-in tokens, and in this setup also receives the results. |
    | `worker`, `mediamtx`, `worker-net` | One worker called `dev-worker-1` with 4 camera slots, its video server, and a tiny container that holds their shared network. |
    | `camera`, `camera-feed` | A fake IP camera. It plays a moving test pattern at `rtsp://camera:8554/cam1`, and the same picture at `cam2` and `cam3`. |

## 2. Sign in

Open **<http://localhost:7000>** in your browser. You land on the sign-in page.

Paste the token **`dev-admin-token`** and click **Continue**. Tick *Remember on this device* if you don't want to
paste it again after closing the tab.

This development setup creates three tokens for you:

| Token | Role | Use it to |
|---|---|---|
| `dev-admin-token` | admin | Do everything |
| `dev-viewer-token` | viewer | See what a read-only user sees |
| `dev-worker-token` | worker | Used by the workers; it can't open the dashboard |

!!! warning
    These tokens are written in a public file. Use this setup on your own computer only. For anything reachable
    by other people, follow [Deploying to production](deployment.md).

After signing in you see the **Overview** page. The *Workers online* card should say **1 / 1**: that is
`dev-worker-1`, which registered itself with the controller when it started.

## 3. Add your first camera

1. Click **Add camera** in the banner on the Overview page (or on the **Cameras** page).
2. Fill in:
    - **Name**: `Lobby entrance`
    - **Location**: `Building A` (optional, it only helps you find the camera later)
    - **Source URL**: `rtsp://camera:8554/cam1`
3. Leave everything else as it is and click **Add camera**.

Open the **Cameras** page. Within a few seconds the camera goes from *Pending* to *Starting* to **Running**, and
the *Worker* column shows `dev-worker-1`. The **Activity** page lists each of those steps as it happens.

!!! tip "Why `camera` and not `localhost`?"
    The source URL is opened by the worker, not by your browser, so it must be an address the *worker* can reach.
    Inside this Docker setup the fake camera is called `camera`. With real cameras you would use the camera's IP
    address on your network.

## 4. Watch it live

Open **Live view** in the sidebar and press the play button on the camera's tile. You should see the test
pattern moving, with a red **LIVE** badge. The video comes straight from the worker's MediaMTX over WebRTC; the
controller only checks that you are allowed to watch.

You can also click the broadcast icon in the camera's row on the **Cameras** page to open a larger player.

## 5. See face detection results

The test pattern has no faces in it, so no results are sent yet; the *Results delivered* card on the Overview
stays at 0. To see real detections, give the fake camera a video with people in it:

1. Copy any MP4 file with faces in it (a short clip from your phone is fine) to
   `deploy/compose/dev/media/sample.mp4`.
2. Restart the camera feed so it picks the file up:

    ```bash
    docker compose -f deploy/compose/dev/compose.yml restart camera-feed
    ```

The fake camera now loops your video. Within a few seconds the worker starts sending results. In this setup they
go to the token server, which decrypts them, writes one line per result to its log and saves the pictures. Watch
them arrive:

```bash
docker compose -f deploy/compose/dev/compose.yml logs -f token-server
```

Each line looks like `result stream=… track=17 end=False quality=0.98 bbox=[412, 281, 587, 489] (kid …)`. The
`kid` part shows the result was encrypted on the worker and decrypted here. To look at the saved pictures (the
full frame and the face crop of every result), copy them out of the container:

```bash
docker compose -f deploy/compose/dev/compose.yml cp token-server:/data/results ./results
```

Back in the dashboard, the *Results delivered* card and the worker's *Sent* counter go up.

## 6. Add a second worker

Real installations have many workers. Start a second one, `dev-worker-2`, with 2 camera slots:

```bash
docker compose -f deploy/compose/dev/compose.yml --profile second-worker up -d
```

Open **Workers**: after a few seconds there are two cards. Nothing moved, because Watchtower doesn't shuffle
cameras that are running fine. New cameras go to whichever worker has the lowest share of its slots in use. Add
two more cameras to see that:

| Name | Source URL |
|---|---|
| `Parking gate` | `rtsp://camera:8554/cam2` |
| `Reception` | `rtsp://camera:8554/cam3` |

(Each camera needs its own source URL; that is why the fake camera offers the same picture three times.) Open
**Cameras** and look at the *Worker* column: the cameras are spread over both workers. Play them all at once with
**Play all** on the **Live view** page.

## 7. Break something

Now the interesting part. Simulate a worker crash by killing `dev-worker-1` without warning:

```bash
docker compose -f deploy/compose/dev/compose.yml kill worker
```

Keep the **Workers** and **Activity** pages open and watch:

- after about **15 seconds** `dev-worker-1` turns **Suspect** (it missed a few syncs, but it might come back);
- after **a little over a minute** it turns **Offline**, and its cameras are moved to `dev-worker-2`, where they
  are running again a few seconds later;
- `dev-worker-2` has only 2 slots, so a camera that doesn't fit waits as **Pending** until room frees up.

The wait before *Offline* is deliberate: a worker that only had a short network hiccup shouldn't lose its
cameras. You can shorten it with the controller's `LEASE_SECONDS` setting.

Bring the worker back:

```bash
docker compose -f deploy/compose/dev/compose.yml start worker
```

It reconnects, turns **Online**, and the waiting camera is placed on it within seconds.

A *planned* shutdown is gentler. Click **Drain** on a worker card: its cameras move to the other worker
immediately and it receives no new ones. Click **Undrain** to bring it back. Stopping a worker normally
(`docker compose ... stop worker`) does the same thing: the worker tells the controller it is leaving before it
exits.

## 8. Try the other roles

Sign out (the arrow next to your name, bottom left) and sign in again with `dev-viewer-token`. The *Add camera*
buttons, the edit, stop and delete icons and the *Drain* buttons are gone, and there is no *Audit log* page: a
viewer can look and watch, but not change anything. Sign back in as `dev-admin-token` and open **Audit log** to
see every change you made, with who made it and when.

## 9. Try the API

Everything the dashboard does goes through the REST API, so you can script it. With the admin token:

```bash
TOKEN=dev-admin-token

# List cameras with their status and worker
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:7000/api/v1/streams

# Add a camera
curl -s -X POST http://localhost:7000/api/v1/streams \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "API camera", "source_url": "rtsp://camera:8554/cam1?from=api"}'
```

Interactive API documentation, where you can try every call from the browser, is at
<http://localhost:7000/docs>. The [REST API reference](api.md) explains each endpoint.

## 10. Stop or clean up

```bash
# Stop everything but keep your cameras and settings for next time
docker compose -f deploy/compose/dev/compose.yml --profile second-worker down

# Stop everything and delete all data (cameras, tokens, saved results)
docker compose -f deploy/compose/dev/compose.yml --profile second-worker down -v
```

## If something doesn't work

**The page at localhost:7000 doesn't load.** Run `docker compose -f deploy/compose/dev/compose.yml ps`. If the
controller isn't `healthy`, look at its log with `docker compose -f deploy/compose/dev/compose.yml logs controller`.
A message about port 7000 means another program is using it; stop that program, or change the left-hand number in
`ports: ["127.0.0.1:7000:7000"]` in `deploy/compose/dev/compose.yml`.

**"That token was not accepted."** Check for spaces before or after the token. If you changed `ADMIN_TOKEN` in
your environment, use that value instead.

**The camera stays Pending.** No worker has a free slot. Check the **Workers** page: is a worker online, and does
it have room? A camera also waits a while before it is retried after failing to start.

**The camera shows Reconnecting or Failed.** The worker can't open the source URL. For the fake camera, check
that `camera` and `camera-feed` are running. For a real camera, see [Cameras](cameras.md#when-a-camera-wont-start).

**Live view stays black or says the connection failed.** Live video uses ports 8889 and 8189 (8890 and 8190 for
the second worker). Make sure nothing else uses them, and that your browser runs on the same computer as Docker.

More answers are on the [Troubleshooting](troubleshooting.md) page.

## Next steps

- [A tour of the dashboard](dashboard.md) explains every page and button.
- [Cameras](cameras.md) explains each camera setting and how to connect real cameras.
- [Deploying to production](deployment.md) walks you through a real installation, step by step.
