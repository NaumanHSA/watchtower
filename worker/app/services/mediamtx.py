# app/services/mediamtx_service.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urljoin
import logging
import time
import httpx

from config import config
from .token_provider import TokenProvider


@dataclass(frozen=True)
class MediaMTXCredentials:
    """
    Optional credentials to talk to your MediaMTX/Controller layer.
    If you need to decrypt, pass the decrypted username/password to this struct.
    If you need a bearer token, you can either:
      - populate bearer_token directly, or
      - provide auth_url and set fetch_bearer_token=True to attempt simple retrieval.
    """
    username: Optional[str] = None
    password: Optional[str] = None
    bearer_token: Optional[str] = None
    auth_url: Optional[str] = None
    fetch_bearer_token: bool = False


@dataclass(frozen=True)
class MediaMTXServiceOptions:
    """
    Python port of your C# options.
    - Url: Base URL of the MediaMTX controller that exposes add/delete endpoints.
    - TimeoutInSec: total timeout per request.
    - RetryCount: number of retries on 408/429/5xx and transport errors.
    - AddCameraUrl: relative path for add-camera (POST).
    - DeleteCameraUrl: relative path prefix for delete-camera (DELETE + stream_id).
    - ListCameraUrl: relative path prefix for list-camera (GET).
    - Enable: soft switch; if False, client raises on calls (useful in multi-env).
    - Credentials: optional auth info (basic or bearer).
    """
    Url: str
    TimeoutInSec: int = 90
    RetryCount: int = 2
    AddCameraUrl: str = "v3/config/paths/add/"
    DeleteCameraUrl: str = "v3/config/paths/delete/"
    ListCameraUrl: str = "v3/config/paths/list/"
    Enable: bool = True
    Credentials: Optional[MediaMTXCredentials] = None


class MediaMTXService:
    """
    Minimal client for:
      - Add camera (POST)
      - Delete camera (DELETE)
      - List cameras (GET)

    Usage pattern:
      svc = MediaMTXService(opts, logger)
      svc.add_camera(name=stream_id, source=rtsp_url)
      svc.delete_camera(stream_id)
      svc.list_cameras()
    """

    RETRY_STATUS = {408, 429, 500, 502, 503, 504}

    def __init__(self, options: MediaMTXServiceOptions = None, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("MediaMTXService")
        self.opts = options
        if self.opts is None:            
            self.logger.warning("MediaMTXServiceOptions is None, using env variables")
            # choose credential strategy
            username = config.MTX_USERNAME or self._maybe_decrypt(config.MTX_ENC_USERNAME)
            password = config.MTX_PASSWORD or self._maybe_decrypt(config.MTX_ENC_PASSWORD)
            
            creds = MediaMTXCredentials(
                username=username,
                password=password,
                bearer_token=config.MTX_BEARER_TOKEN,
                auth_url=config.MTX_AUTH_URL,
                fetch_bearer_token=bool(config.MTX_AUTH_FETCH),
            )

            self.opts = MediaMTXServiceOptions(
                Url=config.MTX_URL_INTERNAL,
                TimeoutInSec=int(config.MTX_TIMEOUT_SEC),
                RetryCount=int(config.MTX_RETRY_COUNT),
                AddCameraUrl=config.MTX_ADD_PATH,
                DeleteCameraUrl=config.MTX_DELETE_PATH,
                ListCameraUrl=config.MTX_LIST_PATH,
                Enable=bool(config.MTX_ENABLE),
                Credentials=creds,
            )

        if not self.opts.Enable:
            raise RuntimeError("MediaMTXService disabled by config (Enable=False)")
        if not self.opts.Url:
            raise ValueError("MediaMTXServiceOptions.Url is required")

        self._base_url = self._normalize_base(self.opts.Url)
        self._add_path = self.opts.AddCameraUrl.lstrip("/")
        self._del_path = self.opts.DeleteCameraUrl.rstrip("/") + "/"
        self._list_path = self.opts.ListCameraUrl.rstrip("/") + "/"

        # Build client with timeouts
        self._timeout = httpx.Timeout(self.opts.TimeoutInSec)
        self._client = httpx.Client(timeout=self._timeout, headers=self._make_headers())

        self._token_provider: Optional[TokenProvider] = None
        # If configured to fetch bearer automatically, instantiate provider:
        c = self.opts.Credentials
        if c and c.auth_url and c.fetch_bearer_token and c.username and c.password:
            self._token_provider = TokenProvider(
                auth_url=c.auth_url,
                username=c.username,
                password=c.password,
                timeout=min(self.opts.TimeoutInSec, 15),
                leeway_sec=30,
            )
            # prime a token right away
            self._set_bearer(self._token_provider.get_valid_token())
        elif c and c.bearer_token:
            self._set_bearer(c.bearer_token)

    def add_camera(self, *, name: str, source: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Returns: (is_success, error_message)
        """
        url = httpx.URL(self._base_url).join(self._add_path).join(name)
        payload = {'name': name, 'source': source}
        ok, data, status, err = self._request_with_retries(
            "POST", str(url), json=payload, auth=self._auth()
        )
        if not ok:
            return False, None, err or f"HTTP {status}"
        # Expected: { "Error": null|"...", "Url": null|"..." }
        if not isinstance(data, dict):
            return False, None, "malformed JSON"
        if data.get("Error"):
            return False, None, str(data["Error"])
        # webrtc_url = data.get("Url") or httpx.URL(self._webrtc_base).join(name)
        webrtc_url = None
        return True, str(webrtc_url), None

    def delete_camera(self, *, name: str) -> Tuple[bool, Optional[str]]:
        """
        Returns: (is_success, error_message)
        """
        url = httpx.URL(self._base_url).join(f"{self._del_path}{name}")
        ok, data, status, err = self._request_with_retries("DELETE", str(url), auth=self._auth())
        if not ok:
            return False, err or f"HTTP {status}"
        if not isinstance(data, dict):
            return False, "malformed JSON"
        if data.get("Error"):
            return False, str(data["Error"])
        if not bool(data.get("IsSuccess", True)):
            return False, "IsSuccess=false"
        return True, None

    def list_cameras(self, page: int = 0, limit: int = 100) -> Tuple[bool, Optional[str]]:
        """
        Returns: (is_success, error_message)
        """
        # {{baseUrl}}/v3/config/paths/list?page=0&itemsPerPage=100
        url = httpx.URL(self._base_url).join(self._list_path.rstrip("/")).join(f"?page={page}&itemsPerPage={limit}")
        ok, data, status, err = self._request_with_retries("GET", str(url), auth=self._auth())
        if not ok:
            return False, data, err or f"HTTP {status}"
        if not isinstance(data, dict):
            return False, data, "malformed JSON"
        if data.get("Error"):
            return False, data, str(data["Error"])
        return True, data, None
        
    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    def _request_with_retries(self, method: str, url: str, *, json: Optional[Dict[str, Any]] = None, auth=None) -> Tuple[bool, Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        """
        Make an HTTP request with retries and token refresh logic.
        Returns: (ok, json_data, status_code, error_message)
        Never raises to caller.
        """
        max_attempts = max(1, int(self.opts.RetryCount) + 1)  # e.g., RetryCount=2 => 3 tries total
        start = time.time()
        total_deadline = float(self.opts.TimeoutInSec) + 5.0  # small cushion

        for attempt in range(1, max_attempts + 1):
            # hard total deadline guard
            if (time.time() - start) > total_deadline:
                return (False, None, None, "total deadline exceeded")
            try:
                self._ensure_auth_header()
                resp = self._client.request(method.upper(), url, json=json, auth=auth)
                # Reactive refresh on 401/403 once
                if resp.status_code in (401, 403) and self._token_provider:
                    try:
                        self._set_bearer(self._token_provider.refresh_now())
                        resp = self._client.request(method.upper(), url, json=json, auth=auth)
                    except Exception as e:
                        # refresh failed — fall through to normal handling
                        pass

                if 200 <= resp.status_code < 300:
                    return (True, self._json(resp), resp.status_code, None)

                # Retry on transient statuses
                if resp.status_code in self.RETRY_STATUS and attempt < max_attempts:
                    delay = self._backoff(attempt)
                    self.logger.warning(
                        f"[MediaMTX] {method} {url} -> HTTP {resp.status_code}; retry in {delay:.2f}s (attempt {attempt}/{max_attempts})"
                    )
                    time.sleep(delay)
                    continue
                # Non-retryable or out of attempts
                return (False, None, resp.status_code, resp.text[:256])
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectError) as e:
                # Transport/timeout errors are retriable
                if attempt < max_attempts:
                    delay = self._backoff(attempt)
                    self.logger.warning(
                        f"[MediaMTX] {method} {url} transport {type(e).__name__}: {e}; retry in {delay:.2f}s (attempt {attempt}/{max_attempts})"
                    )
                    time.sleep(delay)
                    continue
                return (False, None, None, f"{type(e).__name__}: {e}")
            except httpx.HTTPError as e:
                # Any other HTTPX error (DNS failures, protocol errors, etc.)
                return (False, None, None, f"HTTPError: {e}")
            except Exception as e:
                # Truly unexpected — don’t crash the caller
                self.logger.exception(f"[MediaMTX] {method} {url} unexpected error")
                return (False, None, None, f"Unexpected: {e}")

        # Safety net (shouldn’t reach)
        return (False, None, None, "exhausted")

    def _json(self, resp: httpx.Response) -> Dict[str, Any]:
        try:
            return resp.json()
        except Exception:
            return {}

    def _make_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "video-worker/mediamtx-service/1.0"}
        # Add bearer token if present; we’ll also send Basic via httpx auth separately
        if self.opts.Credentials and self.opts.Credentials.bearer_token:
            headers["Authorization"] = f"Bearer {self.opts.Credentials.bearer_token}"
        return headers

    def _auth(self):
        """
        Optional httpx auth. If bearer token is present, we don't add Basic.
        """
        c = self.opts.Credentials
        if not c:
            return None
        if c.bearer_token:
            return None
        if c.username and c.password:
            # httpx accepts tuple for Basic Auth
            return (c.username, c.password)
        return None

    def _ensure_auth_header(self):
        """If a token provider exists, ensure a fresh bearer token is set before a call."""
        if self._token_provider:
            tok = self._token_provider.get_valid_token()
            self._set_bearer(tok)

    def _set_bearer(self, token: str):
        self._client.headers["Authorization"] = f"Bearer {token}"

    @staticmethod    
    def _maybe_decrypt(enc: str | None) -> str | None:
        # Replace with your KMS/Keyring/Custom decryptor. Example: base64 → plaintext
        if not enc:
            return None
        try:
            import base64
            return base64.b64decode(enc).decode("utf-8")
        except Exception:
            return enc  # fallback

    @staticmethod
    def _normalize_base(u: str) -> str:
        if not u:
            return ""
        return u.rstrip("/") + "/"

    @staticmethod
    def _backoff(attempt: int) -> float:
        # simple capped exponential backoff
        return min(2.0, 0.25 * (2 ** max(0, attempt - 1)))


