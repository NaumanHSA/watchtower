# Privacy

Watchtower processes images of people's faces. In many places that is biometric or
personal data with legal obligations (for example the GDPR in the EU, BIPA in
Illinois, the PDPL in the UAE and Saudi Arabia). This page describes what the software
does with that data so you can assess your deployment; it is not legal advice.

## What is stored where

| Data | Where | For how long |
|---|---|---|
| Camera URLs (including passwords), names, settings | Controller's MongoDB | Until the camera is deleted |
| Video frames | Worker memory only; MediaMTX relays video without storing it | Seconds |
| Results queued for delivery | Worker memory (`realtime`) or the worker's spool on disk (`reliable`), encrypted when a key is configured | Until delivered, or evicted at `SPOOL_MAX_MB` |
| Results | Your results receiver | Up to you |
| Token hashes, results private keys | The bundled token server's volume | Until revoked or rotated |

Watchtower does not identify people, keep a face database, or send any data anywhere
except to the results URL and the live viewers you authorise.

ONNX Runtime, the library that runs the face detector, sends usage telemetry to
Microsoft by default on Linux. Workers switch this off before the library loads (by
setting `ORT_DISABLE_TELEMETRY=1`). A packet capture of a worker loading the detector and
analysing frames confirmed that it then makes no outgoing connections of its own, and a
test makes sure the setting stays in place. If you build your own worker image or run the worker in
an unusual way, keep that variable set.

## Sending less

Per camera ([configuration](configuration.md#per-camera-settings)):

- `include_frame: false` sends only the face crop, not the whole scene.
- `face_crop_size` and `max_frame_width` limit image resolution.
- `results_interval_ms` and `snapshot` control how many images are sent per person.

Per worker:

- `REQUIRE_ENCRYPTION=true` refuses to send results in plaintext.
- Use `realtime` delivery if results must not be stored on worker disks, even
  encrypted.

## Before you deploy

- Decide how long your receiver keeps results, and delete them after that.
- Restrict who holds viewer tokens: live view shows everything the camera sees.
- Rotate the results key (`watchtower-token-server rotate-key`) on a schedule, and
  revoke tokens of people who leave.
- Tell people they are being recorded where the law requires it, and consider a data
  protection impact assessment.
