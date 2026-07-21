"""
AES-256-GCM for the payload (c = ciphertext, n = nonce, t = tag)
RSA-OAEP(SHA-256) to encrypt the random AES key (ek)

{
  "alg": "RSA-OAEP-256 + AES-256-GCM",
  "ek": "<base64 RSA-OAEP encrypted AES key>",
  "n": "<base64 12-byte nonce>",
  "c": "<base64 ciphertext>",
  "t": "<base64 16-byte tag>",
  "meta": {"v": 1}
}

"""

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple, Iterable, Callable
import os
import json
import logging
import random
import time
import traceback
import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from requests_toolbelt.multipart.encoder import MultipartEncoder
from app.utils import get_bin_images

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# -----------------------------
# Retry policy with backoff
# -----------------------------
class RetryPolicy:
    def __init__(
        self,
        max_attempts: int = 3,
        base_backoff: float = 0.25,   # seconds
        max_backoff: float = 3.0,     # seconds
        jitter: float = 0.25,         # +/- jitter fraction on each sleep
        retry_on_status: Iterable[int] = (408, 429, 500, 502, 503, 504),
        retry_on_exceptions: Tuple[type, ...] = (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError),
    ):
        self.max_attempts = max(1, int(max_attempts))
        self.base_backoff = float(base_backoff)
        self.max_backoff = float(max_backoff)
        self.jitter = float(jitter)
        self.retry_on_status = set(int(s) for s in retry_on_status)
        self.retry_on_exceptions = retry_on_exceptions

    def sleep_time(self, attempt_idx: int) -> float:
        # exponential backoff with cap and +/- jitter
        delay = min(self.max_backoff, self.base_backoff * (2 ** max(0, attempt_idx - 1)))
        if self.jitter:
            jitter_amt = delay * self.jitter
            delay = max(0.0, delay + random.uniform(-jitter_amt, jitter_amt))
        return delay

# ----------------------------------------
# encryption_utils.py (inline or separate)
# ----------------------------------------
def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")

def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))

def load_server_public_key(pem: str):
    """
    pem: server RSA public key (PEM string). Example: content of server_public.pem
    """
    return serialization.load_pem_public_key(pem.encode("utf-8"))

def load_server_private_key(pem: str, password: Optional[str] = None):
    """
    For server-side decryption (example below). Not needed by the client.
    """
    pw = password.encode("utf-8") if isinstance(password, str) else password
    return serialization.load_pem_private_key(pem.encode("utf-8"), password=pw)

def encrypt_payload_json(payload: Dict[str, Any], server_pub_pem: str) -> Dict[str, Any]:
    """
    Hybrid encrypt a JSON-compatible dict with AES-256-GCM + RSA-OAEP(SHA-256).
    Returns dict {"alg","ek","n","c","t","meta"} with base64 fields.
    """
    # 1) Serialize plaintext
    plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    # 2) Generate random data key and nonce
    data_key = os.urandom(32)  # AES-256
    nonce = os.urandom(12)     # GCM standard nonce size

    # 3) Encrypt plaintext with AES-GCM
    aes = AESGCM(data_key)
    ciphertext = aes.encrypt(nonce, plaintext, associated_data=None)  # returns c || tag
    c, t = ciphertext[:-16], ciphertext[-16:]                         # split out tag

    # 4) Encrypt data key with RSA-OAEP(SHA-256)
    pub = load_server_public_key(server_pub_pem)
    ek = pub.encrypt(
        data_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None)
    )

    # 5) Package
    return {
        "alg": "RSA-OAEP-256 + AES-256-GCM",
        "ek": b64e(ek),
        "n":  b64e(nonce),
        "c":  b64e(c),
        "t":  b64e(t),
        "meta": {"v": 1}
    }
 
def decrypt_envelope(envelope: Dict[str, Any], server_priv_pem: str, password: Optional[str] = None) -> Dict[str, Any]:
    """
    Server-side helper: decrypt envelope -> dict payload.
    """
    ek = b64d(envelope["ek"])
    n  = b64d(envelope["n"])
    c  = b64d(envelope["c"])
    t  = b64d(envelope["t"])
    priv = load_server_private_key(server_priv_pem, password=password)

    # 1) Recover data key
    data_key = priv.decrypt(
        ek,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(), 
            label=None
        )
    )
    # 2) Decrypt AES-GCM
    aes = AESGCM(data_key)
    plaintext = aes.decrypt(n, c + t, associated_data=None)
    return json.loads(plaintext.decode("utf-8"))


# === payload builder: convert your existing multipart sources into one JSON =====
def build_encrypted_detection_payload(
    *,
    record: Dict[str, Any],
    frame,
    frame_to_return_flag: str,
    max_cropped_face_resolution: int,
    frame_width: int,
    frame_height: int,
    fmt: str = ".jpg",
) -> Dict[str, Any]:
    """
    Builds the JSON that mirrors what you'd send via multipart, but with images base64-ed.
    """
    img_bin, cf_bin, bbox_out = get_bin_images(
        frame=frame,
        bbox=record["track"]["face"][frame_to_return_flag]["bbox"],
        max_cropped_face_resolution=max_cropped_face_resolution,
        frame_width=frame_width,
        frame_height=frame_height,
        format=fmt,
    )
    payload = {
        "bbox": [bbox_out],
        "cam_id": str(record["cam_id"]),
        "timestamp": str(record.get("track", {}).get("last_timestamp", "")),
        "detectorParams": record,  # already JSON-serializable by your design
        "bs_type": "overall",
        "files": {
            "photo": {
                "filename": "photo.jpg",
                "content_type": "image/jpeg",
                "b64": b64e(img_bin),
            },
            "normalized": {
                "filename": "normalized.jpg",
                "content_type": "image/jpeg",
                "b64": b64e(cf_bin),
            },
        },
    }
    return payload

def _to_iso8601_z(ts: Optional[str | float | int]) -> str:
    """
    Normalize timestamps to ISO 8601 with 'Z'.
    Accepts:
      - ISO strings (returned as-is if they already look ISO)
      - epoch seconds (int/float)
      - None -> current time
    """
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(ts, str) and ts:
        # trust caller's ISO-ish string; ensure ends with Z if UTC-like
        # (safe no-op if already has Z or an offset)
        if ts.endswith("Z") or "+" in ts:
            return ts
        return ts + "Z"
    # default now
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")

def _bbox_components(bbox_any) -> Tuple[Dict[str, int], list[int], int]:
    """
    Robustly derive xywh, xyxy, area from various bbox forms.
    - If dict with xywh or xyxy: use it.
    - If list/tuple of 4: [x1, y1, x2, y2].
    """
    if isinstance(bbox_any, dict):
        if "xywh" in bbox_any and isinstance(bbox_any["xywh"], dict):
            xywh = bbox_any["xywh"]
            x, y, w, h = int(xywh["x"]), int(xywh["y"]), int(xywh["w"]), int(xywh["h"])
            xyxy = [x, y, x + w, y + h]
            return {"x": x, "y": y, "w": w, "h": h}, xyxy, w * h
        if "xyxy" in bbox_any and isinstance(bbox_any["xyxy"], (list, tuple)) and len(bbox_any["xyxy"]) == 4:
            x1, y1, x2, y2 = map(int, bbox_any["xyxy"])
            w, h = max(0, x2 - x1), max(0, y2 - y1)
            return {"x": x1, "y": y1, "w": w, "h": h}, [x1, y1, x2, y2], w * h

    if isinstance(bbox_any, (list, tuple)) and len(bbox_any) == 4:
        x1, y1, x2, y2 = map(int, bbox_any)
        w, h = max(0, x2 - x1), max(0, y2 - y1)
        return {"x": x1, "y": y1, "w": w, "h": h}, [x1, y1, x2, y2], w * h
    # Fallback safe default
    return {"x": 0, "y": 0, "w": 0, "h": 0}, [0, 0, 0, 0], 0

def build_received_detection(
    *,
    record: Dict[str, Any],
    frame,
    face_crop,
    frame_to_return_flag: str,
    frame_width: int,
    frame_height: int,
    fmt: str = ".jpg",
    snapshot_note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds the EXACT 'received' object with base64 images.
    """
    snap = record["track"]["face"][frame_to_return_flag]
    xywh, xyxy, area = _bbox_components(snap["xyxy"])
    img_bin, cf_bin = get_bin_images(
        frame=frame,
        face_crop=face_crop,
        frame_width=frame_width,
        frame_height=frame_height,
        format=fmt,
    )
    # core params (pull safely from your record/engine)
    track: Dict[str, Any] = record.get("track", {})
    detector_params_core = {
        "endOfTrack": bool(track.get("end_of_track", False)),
        "quality": float(track.get("quality", 0.0)),
        "trackDurationSeconds": float(track.get("duration_seconds", 0.0)),
    }

    received = {
        "camId": str(record.get("cam_id", "")),
        "timestamp": _to_iso8601_z(
            record.get("track", {}).get("last_timestamp")
            or record.get("timestamp")
        ),
        "bsType": "overall",
        "bbox": {
            "xywh": xywh,
            "xyxy": xyxy,
            "area": area,
        },
        "detectorParamsCore": detector_params_core,
        "snapshotNote": str(snapshot_note or record.get("snapshot_note", "")),
        "photo": _b64e(img_bin),
        "normalized": _b64e(cf_bin),
    }
    return received

# === integrate into your BroadcastClient / AsyncBroadcastClient =================
class BroadcastClient:
    def __init__(
        self,
        *,
        timeout: float = 5.0,
        connect_timeout: float = 3.0,
        max_keepalive_connections: int = 20,
        max_connections: int = 100,
        verify: bool = True,
        user_agent: str = "engine-broadcaster/1.0",
        retry_policy: Optional[RetryPolicy] = None,
        logger: Optional[logging.Logger] = None,
        encrypt: bool = False,
        server_public_key_pem: Optional[str] = None,  # required if encrypt=True
        watchtower_token: Optional[str] = None,
    ):
        self.logger = logger or logging.getLogger("BroadcastClient")
        self.retry = retry_policy or RetryPolicy()
        self.encrypt = encrypt
        self.server_public_key_pem = server_public_key_pem
        self.watchtower_token = watchtower_token
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            verify=verify,
            limits=httpx.Limits(
                max_keepalive_connections=max_keepalive_connections,
                max_connections=max_connections,
            ),
            headers={"User-Agent": user_agent},
        )

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

    def send_detection(
        self,
        *,
        url: str,
        record: Dict[str, Any],
        frame,
        face_crop,
        frame_to_return_flag: str,
        frame_width: int,
        frame_height: int,
        fmt: str = ".jpg",
    ) -> Optional[bool]:
        try:
            # Build the EXACT final payload
            received = build_received_detection(
                record=record,
                frame=frame,
                face_crop=face_crop,
                frame_to_return_flag=frame_to_return_flag,
                frame_width=frame_width,
                frame_height=frame_height,
                fmt=fmt,
            )
            return received
            body_obj = {
                "ok": True,
                "received": received,
            }

            headers = {
                "Content-Type": "application/json",
            }
            if self.watchtower_token:
                headers["watchtower-token"] = self.watchtower_token

            # Optional envelope encryption
            if self.encrypt:
                if not self.server_public_key_pem:
                    raise RuntimeError("Encryption enabled but server_public_key_pem not provided.")
                body_obj = encrypt_payload_json(body_obj, self.server_public_key_pem)
                headers["X-Enc"] = "v1"

            body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            post_args = dict(url=url, content=body, headers=headers)

        except Exception:
            self.logger.error(
                f"[{record.get('cam_id')}] Build payload error for {url}: {traceback.format_exc()}"
            )
            return False
        
        attempt = 1
        while True:
            try:
                resp = self.client.post(**post_args)
                if 200 <= resp.status_code < 300:
                    return True
                if resp.status_code in self.retry.retry_on_status and attempt < self.retry.max_attempts:
                    delay = self.retry.sleep_time(attempt)
                    self.logger.warning(
                        f"[broadcast] {url} -> HTTP {resp.status_code}; retry in {delay:.2f}s (attempt {attempt}/{self.retry.max_attempts})"
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                self.logger.error(f"[broadcast] {url} -> HTTP {resp.status_code}: {resp.text[:512]}")
                return False
            except self.retry.retry_on_exceptions as e:
                if attempt < self.retry.max_attempts:
                    delay = self.retry.sleep_time(attempt)
                    self.logger.warning(
                        f"[broadcast] transport {type(e).__name__}: {e}; retry in {delay:.2f}s (attempt {attempt}/{self.retry.max_attempts})"
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                self.logger.error(f"[broadcast] transport error after retries: {e}")
                return False
            except Exception as e:
                self.logger.exception(f"[broadcast] unexpected error: {e}")
                return False
