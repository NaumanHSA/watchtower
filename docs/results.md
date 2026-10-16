# Receiving results

Every face Watchtower finds ends up as a *result*: an HTTP `POST` with a JSON body, sent by the worker to an
address you choose. This guide shows how to receive results, what they contain, how to choose between real-time
and reliable delivery, and how encryption works.

## Where results go

Each result is sent to the first of these that is set:

1. the camera's own **Results URL** (set in the camera's settings),
2. the controller's `RESULTS_URL` setting, the normal place to set it for everyone,
3. the worker's `RESULTS_URL` setting, a last resort.

If none is set, results are thrown away and counted as *dropped* on the worker's card. Results go straight from
the worker to that address; they never pass through the controller.

## Two ways to receive results

### The easy way: let the token server decrypt and forward

If you run the bundled token server, it can act as your receiver: it decrypts each result, optionally saves the
pictures, and forwards the plain JSON to your application. Your application then only has to accept a simple
`POST`, with no cryptography involved.

On the control plane, in `deploy/compose/controller/.env`:

```bash
TOKEN_SERVER_INGEST_ENABLED=true
TOKEN_SERVER_FORWARD_URL=https://your-app.example.com/watchtower/events
RESULTS_URL=https://watchtower-results.example.com/v1/ingest
```

`RESULTS_URL` must be an address of the token server that the **workers** can reach. In the Compose file the token
server is only reachable from inside the control plane, so publish it (with HTTPS in front) at an address your
workers can reach. The development setup uses exactly this arrangement, with `RESULTS_URL` pointing at the token
server.

If forwarding to your application fails, the token server answers the worker with an error, so a worker in
reliable mode keeps the result and tries again later.

### The direct way: your own receiver

Point `RESULTS_URL` at your own application and handle results there. A complete receiver in Python, which accepts
both encrypted and plain results:

```python
# pip install fastapi uvicorn "watchtower-contracts[crypto] @ git+https://github.com/NaumanHSA/watchtower#subdirectory=packages/contracts"
import base64
from pathlib import Path

from fastapi import FastAPI, Request
from watchtower_contracts.crypto import decrypt_json, is_envelope

app = FastAPI()
PRIVATE_KEYS = [Path("results-private.pem").read_text()]  # from: watchtower-token-server export-key
seen: set[str] = set()  # use your database in real life


@app.post("/watchtower/events")
async def receive(request: Request):
    body = await request.json()
    if is_envelope(body):  # encrypted on the worker
        body, _ = decrypt_json(body, PRIVATE_KEYS)
    result = body["received"]

    if result["eventId"] in seen:  # reliable delivery may repeat a result
        return {"ok": True}
    seen.add(result["eventId"])

    print(result["streamName"], result["trackId"], result["detectorParamsCore"]["quality"])
    Path(f"{result['eventId']}.jpg").write_bytes(base64.b64decode(result["normalized"]))
    return {"ok": True}  # answer quickly; do slow work in the background
```

Run it with `uvicorn receiver:app --port 8000` and set `RESULTS_URL=https://your-host/watchtower/events`.

Three rules for any receiver:

- **Answer quickly** with a `2xx` status. Do slow work (database writes, recognition, notifications) after
  answering, or in a queue.
- **Ignore duplicates** by `eventId`. In reliable mode a result can arrive twice, for example if your answer was
  lost on the way back.
- **Answer `5xx` when you couldn't store the result.** A worker in reliable mode keeps it and retries. Any other
  `4xx` answer (except `408`, `425` and `429`) tells the worker the result is bad, and it is dropped so that one
  broken result can't block the queue.

## When results are sent

For each face, Watchtower sends:

1. one result when the face is **confirmed**, that is, seen in several frames in a row (5 by default);
2. further results while the face stays in view, **at most once per result interval** (1 second by default);
3. a **final** result when the face leaves the picture, with `endOfTrack` set to `true`.

So a person who walks past in three seconds produces about four results, and a person standing at a reception
desk for a minute produces about sixty. Raise the camera's result interval if you need fewer.

## What a result contains

The JSON body (after decryption, if it was encrypted):

```json
{
  "ok": true,
  "received": {
    "eventId": "1c9e0c7f3f8f4a0e9d5d8a0a6c3b2f11",
    "streamId": "7a51dab7-9beb-4478-9ba2-d2e88383b3b7",
    "camId": "7a51dab7-9beb-4478-9ba2-d2e88383b3b7",
    "streamName": "Lobby",
    "workerId": "w-05f9f953aea8",
    "generation": 3,
    "trackId": "17",
    "timestamp": "2026-10-15T11:56:13.412Z",
    "capturedAt": "2026-10-15T11:56:14.020Z",
    "bsType": "overall",
    "bbox": {"xywh": {"x": 412, "y": 281, "w": 175, "h": 208}, "xyxy": [412, 281, 587, 489], "area": 36400},
    "detectorParamsCore": {"endOfTrack": false, "quality": 0.9961, "trackDurationSeconds": 2.187},
    "snapshotNote": "best",
    "photo": "<the full frame as a base64 JPEG, or null>",
    "normalized": "<the face crop as a base64 JPEG>"
  }
}
```

| Field | Meaning |
|---|---|
| `eventId` | Unique for every result. Use it to ignore duplicates. |
| `streamId`, `streamName` | Which camera. `camId` is the same id under its original name, kept for older receivers. |
| `workerId` | Which worker produced it. |
| `generation` | Which assignment of the camera produced it. It goes up every time the camera is moved, restarted or changed. |
| `trackId` | The same for every result about one face while it stays in view. Use it to group results by person visit. |
| `timestamp` | When the picture in this result was taken, in UTC. |
| `capturedAt` | When the result was produced, in UTC. |
| `bbox` | Where the face is in `photo`, in pixels: as x, y, width and height, and as two corners. |
| `detectorParamsCore.quality` | The detector's average confidence for this face so far, from 0 to 1. |
| `detectorParamsCore.endOfTrack` | `true` on the final result for a face. |
| `detectorParamsCore.trackDurationSeconds` | How long the face has been in view. |
| `snapshotNote` | Which picture this is: `best`, `first` or `last` (the camera's *Snapshot* setting). |
| `photo` | The full frame, unless the camera has *Include full frame* turned off. |
| `normalized` | The face crop. |

Every request also carries the headers `Content-Type: application/json`, `X-Watchtower-Event-Id` (the same as
`eventId`), and `X-Enc: v1` when the body is encrypted.

The exact schema is the `DetectionEvent` model in
[`packages/contracts`](../packages/contracts/watchtower_contracts/models.py).

## Choosing a delivery mode

Each camera uses one of two delivery modes. The worker's `DELIVERY_MODE` is the default; a camera's *Delivery*
setting overrides it.

**Real time** (`realtime`) is for results that are only useful *now*: opening a door, a live alert, a wall display.
Results wait in a small queue in memory. If your application is slow or down, the oldest results are dropped, and
any result older than 5 seconds (`MAX_RESULT_AGE_MS`) is thrown away instead of being sent late. When your
application comes back, the first thing it receives is what is happening right now.

**Reliable** (`reliable`) is for results you must not lose: a visitor log, a search index, an audit trail. Each
result is written to the worker's disk first, then sent; results are delivered in order and retried until your
application accepts them, even across worker restarts. The price is that results can arrive late after an outage.
The waiting results may use up to 512 MB of disk (`SPOOL_MAX_MB`); beyond that the oldest are removed.

| | Real time | Reliable |
|---|---|---|
| Your application is healthy | Sent immediately | Written to disk, sent immediately |
| Your application is slow or down | Kept briefly in memory; old results dropped | Kept on disk, sent in order when it is back |
| Promise | Never late, may lose results | Never lost (within the disk limit), may be late |
| Duplicates | Never | Possible; ignore them by `eventId` |

You can follow delivery on the **Workers** page: *Sent*, *Dropped*, *Failed*, and *Queued* (results waiting).

## Encryption

Results contain pictures of people, so Watchtower can encrypt them on the worker, before they are queued or
written to disk. Only the holder of the private key can read them, even if someone intercepts the traffic or gets
hold of a worker's disk.

**How keys reach the workers.** The bundled token server creates a key pair the first time it starts. When a
worker checks its token, the token server hands out the *public* half, and the worker encrypts every result with
it. The *private* half stays in the token server. (With static tokens or SSO, give the controller a public key with
`RESULT_PUBLIC_KEY_FILE`.) If no key is available, results are sent unencrypted, unless the worker has
`REQUIRE_ENCRYPTION=true`, in which case it refuses to send them.

**Getting the private key for your receiver:**

```bash
docker compose exec token-server watchtower-token-server export-key
```

Store it like a password. Anyone with it can read your results.

**Rotating keys.** `watchtower-token-server rotate-key` creates a new key pair. Workers pick up the new public key
within a minute. Every encrypted result names its key in `meta.kid`, and the token server keeps old keys so it can
still decrypt results that were already on their way. Export the new key for your receiver and keep the old ones
until no results encrypted with them can still arrive.

**The format.** An encrypted body looks like this:

```json
{"alg": "RSA-OAEP-256 + AES-256-GCM", "ek": "<base64>", "n": "<base64>", "c": "<base64>", "t": "<base64>",
 "meta": {"v": 1, "kid": "4f1c2e9a7b3d"}}
```

A random 256-bit AES key encrypts the JSON with AES-GCM (`n` is the nonce, `c` the ciphertext, `t` the
authentication tag). That AES key is itself encrypted with your RSA public key using OAEP with SHA-256 (`ek`).
To decrypt without the Python package above:

```python
import base64, json
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def decrypt(envelope: dict, private_key_pem: bytes) -> dict:
    b64 = base64.b64decode
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    data_key = key.decrypt(b64(envelope["ek"]), padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    plaintext = AESGCM(data_key).decrypt(b64(envelope["n"]), b64(envelope["c"]) + b64(envelope["t"]), None)
    return json.loads(plaintext)
```

## Older receivers

Receivers written for Watchtower 1.x keep working: the `received` object keeps all of its original fields. If your
receiver looked up keys by the worker's token, set `RESULTS_SEND_TOKEN=true` on the workers so they send it in a
`watchtower-token` header again.
