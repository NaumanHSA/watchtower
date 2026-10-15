# Results

Workers `POST` one JSON document per result to the **results URL**. The URL comes
from, in order: the stream's `config.results_url`, the controller's `RESULTS_URL`, the
worker's `RESULTS_URL`. Without any, results are discarded (and counted as dropped).

## When results are sent

For each tracked face:

1. once the face is confirmed (seen in `track_confirm_frames` consecutive frames);
2. again at most every `results_interval_ms` while it stays in view;
3. a final time with `endOfTrack: true` when it leaves (missed for
   `track_max_missed_frames` frames).

## Payload

Plaintext body (also what an encrypted envelope decrypts to):

```json
{
  "ok": true,
  "received": {
    "eventId": "1c9e0c7f3f8f4a0e9d5d8a0a6c3b2f11",
    "camId": "7a51dab7-9beb-4478-9ba2-d2e88383b3b7",
    "streamId": "7a51dab7-9beb-4478-9ba2-d2e88383b3b7",
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
    "photo": "<base64 JPEG of the full frame, or null>",
    "normalized": "<base64 JPEG of the face crop>"
  }
}
```

| Field | Meaning |
|---|---|
| `eventId` | Unique per result. Use it to ignore duplicates (`reliable` delivery is at-least-once). |
| `streamId` / `camId` | The camera. `camId` is the original field name, kept for compatibility. |
| `generation` | Which assignment of the stream produced it (see [architecture](architecture.md)). |
| `trackId` | Stable for one face while it stays in view on one engine. |
| `timestamp` | When the chosen snapshot was taken (UTC). |
| `capturedAt` | When the result was produced (UTC). |
| `bbox` | Face box in `photo` pixel coordinates (after `max_frame_width` downscaling). |
| `detectorParamsCore.quality` | Mean detection confidence over the track so far. |
| `snapshotNote` | Which snapshot `photo`/`normalized` show: `best` (highest confidence), `first` or `last`. |
| `photo` | Full frame, unless the stream sets `include_frame: false`. |
| `normalized` | Face crop with `face_crop_margin`, long side at most `face_crop_size`. |

The schema is `DetectionEvent` in
[`packages/contracts`](../packages/contracts/watchtower_contracts/models.py).

Headers sent with every result: `Content-Type: application/json`,
`X-Watchtower-Event-Id`, and `X-Enc: v1` when the body is encrypted. With
`RESULTS_SEND_TOKEN=true` the worker also sends its token as `watchtower-token`, for
older receivers that look keys up by token.

## Encryption

When the token server gives the worker a public key (`pub_cov`), every body is replaced
by an envelope:

```json
{"alg": "RSA-OAEP-256 + AES-256-GCM", "ek": "<b64>", "n": "<b64>", "c": "<b64>", "t": "<b64>",
 "meta": {"v": 1, "kid": "4f1c2e9a7b3d"}}
```

A random 256-bit AES key encrypts the JSON with AES-GCM (`n` nonce, `c` ciphertext,
`t` tag); RSA-OAEP with SHA-256 wraps that key (`ek`). `meta.kid` names the key pair.
Results are encrypted on the worker before they are queued or spooled.

Decrypting in Python (or install this repo's contracts package,
`pip install "./packages/contracts[crypto]"`, and call
`watchtower_contracts.crypto.decrypt_json`):

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

With the bundled token server, export a private key with
`watchtower-token-server export-key <kid>`. Or let the token server receive results
itself (`TOKEN_SERVER_INGEST_ENABLED=true`): it decrypts them and can forward the
plaintext to your app (`TOKEN_SERVER_FORWARD_URL`).

## Delivery modes

Set per worker with `DELIVERY_MODE`, override per camera with `config.delivery_mode`.

| | `realtime` | `reliable` |
|---|---|---|
| Receiver healthy | sent immediately | written to the spool, sent immediately |
| Receiver slow or down | in-memory queue (`REALTIME_QUEUE_SIZE`), oldest dropped first; results older than `MAX_RESULT_AGE_MS` are dropped | kept on disk, sent in order with backoff once the receiver answers again |
| Guarantee | at most once, never late | at least once, may be late; dedupe on `eventId` |
| Limit | queue size, max age | `SPOOL_MAX_MB`; beyond it the oldest results are evicted |
| Retries | none | `408`, `425`, `429` and `5xx` responses and network errors are retried; other `4xx` responses drop the result so one bad result can't block the queue |

Your receiver should answer `2xx` quickly and do heavy work asynchronously. If it
forwards results elsewhere, answering `5xx` when forwarding fails makes a `reliable`
worker keep the result and retry.

Delivery counters (`sent`, `dropped`, `failed`, queued) are shown per worker in the
dashboard and exported on the worker's `/metrics`.
