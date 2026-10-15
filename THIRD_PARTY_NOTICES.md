# Third-party notices

## Bundled in this repository

**YuNet face detection models** — `worker/watchtower_worker/weights/face_detection_yunet*.onnx`
MIT License, Copyright (c) 2020 Shiqi Yu <shiqi.yu@gmail.com>.
Source: <https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet>.

The OpenCV detector wrapper in `worker/watchtower_worker/engine/detector.py` is adapted
from the YuNet demo in the OpenCV Zoo project (same source and license terms).

The `postproc_yunet_top20_th70_*.onnx` post-processing models (NMS over YuNet's raw
outputs) also come from the OpenCV Zoo YuNet work, under the same MIT license.

## Used at runtime (not bundled)

| Component | License | Use |
|---|---|---|
| [MediaMTX](https://github.com/bluenviron/mediamtx) | MIT | Container image next to each worker (camera re-stream, WebRTC) |
| [OpenCV](https://opencv.org) (opencv-python-headless) | Apache-2.0 | Video decoding, image processing |
| [ONNX Runtime](https://onnxruntime.ai) | MIT | Face detector inference |
| [FastAPI](https://fastapi.tiangolo.com), [Uvicorn](https://www.uvicorn.org) | MIT, BSD-3-Clause | HTTP services |
| [PyMongo](https://pymongo.readthedocs.io) | Apache-2.0 | Controller storage |
| [Pydantic](https://docs.pydantic.dev) | MIT | Models and settings |
| [cryptography](https://cryptography.io) | Apache-2.0 / BSD | Result encryption |
| [HTTPX](https://www.python-httpx.org) | BSD-3-Clause | HTTP client |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | Worker load metrics |
| [MongoDB](https://www.mongodb.com) | SSPL | Database (separate service) |
| [Caddy](https://caddyserver.com) | Apache-2.0 | Optional HTTPS proxy |

Full dependency versions are pinned in `uv.lock`.
