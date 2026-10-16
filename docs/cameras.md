# Cameras

This guide covers connecting real cameras, what every camera setting does, how to tune them for different
situations, and what each camera status means.

## Before you add a camera

Watchtower reads video from network cameras, video recorders (NVRs) and streaming servers. You need the
camera's **stream address**, a URL that usually looks like this:

```text
rtsp://username:password@10.0.0.12:554/stream1
```

Three things to know before you start:

**The worker opens the camera, not your browser.** The address must work from the machine that runs the worker.
If your cameras are on a separate network, the worker host needs a route to it. Your own computer doesn't need to
reach the camera at all.

**Test the address first.** On the worker host (or any machine on the same network), open the URL in
[VLC](https://www.videolan.org/) (*Media → Open Network Stream*) or run
`ffprobe "rtsp://username:password@10.0.0.12:554/stream1"`. If neither can play it, Watchtower can't either, and
the problem is the address, the password or the network.

**Special characters in passwords must be encoded.** A password like `p@ss:word` breaks the URL because `@` and
`:` have a meaning there. Replace them: `@` becomes `%40`, `:` becomes `%3A`, `/` becomes `%2F`, `#` becomes `%23`
and `?` becomes `%3F`. So `p@ss:word` is written `p%40ss%3Aword`.

### Common address formats

Most cameras speak RTSP. The exact path depends on the maker; these are the usual defaults, but check your
camera's manual or its web interface:

| Maker | Main stream address |
|---|---|
| Hikvision | `rtsp://user:pass@IP:554/Streaming/Channels/101` (use `102` for the smaller sub-stream) |
| Dahua, Amcrest | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` (`subtype=1` for the sub-stream) |
| Axis | `rtsp://user:pass@IP/axis-media/media.amp` |
| Reolink | `rtsp://user:pass@IP:554/h264Preview_01_main` (`_sub` for the sub-stream) |
| Video recorders (NVRs) | Usually one URL per channel; the recorder's settings page shows them |

Watchtower accepts `rtsp://`, `rtsps://`, `rtmp://`, `rtmps://`, `srt://` and `http(s)://` (an HLS playlist)
addresses. Each camera must have a different address; adding the same address twice is refused, so one camera is
never analysed twice by accident.

!!! tip "Main stream or sub-stream?"
    Many cameras offer a full-resolution *main stream* and a smaller *sub-stream*. Watchtower shrinks frames wider
    than 1280 pixels before analysing them anyway, so a 4K main stream mostly costs network bandwidth and decoding
    time on the worker. A 720p or 1080p stream is usually the best choice. Use the main stream when faces are small
    and far away, together with a larger detector size (see below).

## Adding a camera

**In the dashboard:** open **Cameras** and click **Add camera**. Enter a name, optionally a location, and the
source URL. The defaults for everything else suit a typical entrance camera, so you can save straight away and
tune later.

**With the API:**

```bash
curl -X POST https://watchtower.example.com/api/v1/streams \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
        "name": "Lobby entrance",
        "location": "Building A, ground floor",
        "source_url": "rtsp://user:pass@10.0.0.12:554/Streaming/Channels/101",
        "metadata": {"site": "hq", "zone": "lobby"},
        "config": {"fps": 8, "delivery_mode": "reliable"}
      }'
```

The `Idempotency-Key` header makes it safe to retry the request if your script loses its connection: the same
key never creates a second camera.

A new camera starts as **pending**, is placed on a worker within a couple of seconds and normally shows
**running** within ten seconds. You need the *operator* or *admin* role to add cameras.

## Camera settings

Every setting below can be changed later; saving a change restarts that camera's analysis, which causes a gap
of a few seconds. The dashboard shows the most useful settings; all of them can be set through the API in the
`config` object.

### Detection

**Frames per second** (`fps`, default `8`, from 1 to 60). How many frames per second Watchtower analyses. The
camera may send 25 or 30; Watchtower simply skips the rest. Eight is plenty for people walking through a door.
Lower it to 4 or 5 to save processor time on busy workers; raise it for fast movement, such as people on
escalators. Leave the field empty in the dashboard (or send `null`) to analyse every frame the camera sends,
which is rarely needed.

**Detector size** (`detector_resolution`, default `320`; also `416`, `512`, `640`). The size, in pixels, of the
picture the face detector looks at. Bigger finds smaller and more distant faces but costs noticeably more
processor time (640 costs about four times as much as 320). Keep 320 for doors and corridors where faces are
large; use 512 or 640 for wide halls, lobbies and outdoor areas.

**Minimum confidence** (`detection_confidence`, default `0.9`, from 0 to 1). How sure the detector must be before
something counts as a face. Lower values find more faces in poor conditions but also more false ones (posters,
patterns). The default works well; try 0.8 for dim or angled views. With the default detector engine, values
below 0.7 behave like 0.7.

**Most faces per frame** (`max_faces`, API only, default `20`, from 1 to 20). An upper limit on faces analysed in
one frame.

**Largest frame width** (`max_frame_width`, API only, default `1280`, from 160 to 7680, or `null`). Frames wider
than this are shrunk before analysis. This is also the size of the full-frame picture sent with results. Raise it
to 1920 together with a larger detector when you need small faces from a high-resolution camera.

### Tracking

Watchtower follows each face from frame to frame, so that one person produces a few results instead of one per
frame.

**Frames to confirm** (`track_confirm_frames`, API only, default `5`). A face must be seen in this many frames in
a row before it counts. This filters out one-off false detections. At 8 frames per second, 5 frames is about
0.6 seconds.

**Frames before a face is lost** (`track_max_missed_frames`, API only, default `8`). How many frames a face may be
missing (someone turns away, walks behind a pillar) before Watchtower decides they have left. When that happens
it sends a final result for that face.

### Results

**Result every** (`results_interval_ms`, default `1000`, from 100 to 120000 milliseconds). While a face stays in
view, Watchtower sends a fresh result for it at most this often. With the default, someone standing in front of
the camera for ten seconds produces about ten results. Raise it to 5000 or more if you only care that someone was
there, not about every second.

**Snapshot** (`snapshot`, default `best`). Which picture of the face each result carries: **best** is the
clearest one seen so far (highest detector confidence), **first** is the first one, **last** is the most recent.
*Best* gives the most useful pictures for most purposes.

**Include full frame** (`include_frame`, default on). Send the whole camera picture with each result, not only
the face crop. Turn it off to send less data and to keep bystanders and background out of your results.

**Face crop size** (`face_crop_size`, API only, default `224`, from 64 to 1024 pixels). The longest side of the
face picture. **Face crop margin** (`face_crop_margin`, API only, default `0.1`) adds room around the face: `0.1`
means 10% on each side.

### Delivery

**Delivery** (`delivery_mode`). **Real time** never delivers a result late: if your application can't keep up,
older results are dropped. **Reliable** never loses a result: results wait on the worker's disk and are delivered
in order, even after hours of downtime. *Worker default* uses the worker's own setting. See
[Receiving results](results.md#choosing-a-delivery-mode) for how to choose.

**Results URL** (`results_url`). Send this camera's results somewhere other than the default. Leave it empty to
use the controller's `RESULTS_URL`.

### Connection

**Reconnect interval** (`reconnect_interval_s`, API only, default `5` seconds) and **reconnect attempts**
(`reconnect_attempts`, API only, default `5`). When the video stops, the analysis tries to reconnect this many
times, this many seconds apart, before giving up and letting the worker restart it. Raise them for cameras on
flaky links (a wireless bridge, a 4G router) so short outages don't count as failures.

### Metadata

**Metadata** is free-form JSON stored with the camera, for example `{"site": "north", "zone": "entrance",
"owner": "facilities"}`. Watchtower doesn't use it; it is returned by the API so your own scripts and integrations
can group and find cameras. It is not added to results (results carry the camera's id and name).

## Recipes

Start from the defaults and change only what the situation calls for.

| Situation | Change |
|---|---|
| A door or corridor, faces close to the camera | Nothing. The defaults are made for this. |
| A wide hall or outdoor area, faces small and far away | Detector size `512` or `640`; `max_frame_width` `1920` if the camera sends 1080p or more. |
| A busy worker, save processor time | Frames per second `4` or `5`; detector size `320`. |
| Only need to know that someone was there | Result every `5000` to `30000`; snapshot `best`. |
| Keep bystanders out of results | Include full frame off; a smaller face crop. |
| A camera on an unreliable link | Reconnect interval `10`, reconnect attempts `30`. |
| Results feed an audit trail or search index | Delivery `reliable`. |
| Results drive live alerts or a door | Delivery `realtime`. |

## Camera statuses

| Status | What it means | What to do |
|---|---|---|
| **Pending** | The camera should run, but isn't on a worker yet: either no online worker has a free slot, or it failed recently and is waiting before the next try. | Check the **Workers** page for free capacity. Add a worker or raise a worker's `MAX_STREAMS` if all slots are used. |
| **Starting** | It has been given to a worker, which hasn't reported it running yet. | Wait a few seconds. If it stays here for a minute, the worker may be stuck; the camera is then moved automatically. |
| **Running** | Frames are being analysed. The *FPS* column shows the actual rate. | Nothing. |
| **Reconnecting** | The video stopped and the worker is trying to reconnect. The error text says why, such as *cannot open source*. | Check the camera, its address and the network between the worker and the camera. |
| **Error** | The worker gave up on the camera after several restarts. This is short-lived: the scheduler takes the camera back and tries again later, on another worker if it can. | Same as reconnecting. |
| **Failed** | Watchtower gave up after five failed starts in a row (`MAX_START_ATTEMPTS`). It won't try again on its own. | Fix the cause, then press **Retry** (the play icon). |
| **Stopped** | Someone stopped it. | Press play to start it again. |

## When a camera won't start

Work through these in order:

1. **Read the error** under the status on the **Cameras** page. *cannot open source* means the worker couldn't
   connect to the address.
2. **Test the address from the worker host** with VLC or `ffprobe`, as described [above](#before-you-add-a-camera).
   Testing from your own computer proves nothing if the worker is elsewhere.
3. **Check the password**, and that special characters are encoded.
4. **Check the camera's connection limit.** Many cameras accept only a few simultaneous viewers. Watchtower opens
   one connection per camera (MediaMTX shares it with live viewers), but other recorders or viewer apps may already
   be using the camera's slots.
5. **Check the codec.** H.264 works everywhere. Some cameras default to H.265 (HEVC); analysis handles it, but many
   browsers can't play it in live view. If live view is black while the camera shows *running*, switch the camera's
   stream to H.264.
6. Once fixed, press **Retry** on a failed camera. A camera that is still *reconnecting* picks the fix up by itself.

The worker's log has the full details: `docker compose logs worker` on the worker host.

## Editing, stopping and deleting

- **Edit** with the pencil icon. Changing the name, location or metadata takes effect immediately. Changing the
  source URL or any setting restarts the analysis on its worker, so expect a gap of a few seconds.
- **Stop** with the square icon. The camera keeps its settings and shows *stopped*; its worker slot is freed.
- **Start** a stopped camera, or **Retry** a failed one, with the play icon.
- **Delete** with the bin icon. Its settings are removed; results that were already delivered are not affected.

Watchtower decides which worker runs a camera; there is no way to pin a camera to a particular worker. To move
cameras off a worker, [drain it](workers.md#draining-a-worker-for-maintenance).

## Using the API

The API covers everything above; a few common calls:

```bash
# All cameras that need attention
curl -s -H "Authorization: Bearer $TOKEN" "https://watchtower.example.com/api/v1/streams?status=failed"

# Change settings (only the keys you send change)
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  https://watchtower.example.com/api/v1/streams/$ID -d '{"config": {"fps": 5, "detector_resolution": 512}}'

# Stop, start (or retry), delete
curl -s -X POST   -H "Authorization: Bearer $TOKEN" https://watchtower.example.com/api/v1/streams/$ID/stop
curl -s -X POST   -H "Authorization: Bearer $TOKEN" https://watchtower.example.com/api/v1/streams/$ID/start
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" https://watchtower.example.com/api/v1/streams/$ID
```

See the [REST API reference](api.md) for every endpoint.
