# app/services/token_provider.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import base64, json, time, threading
import httpx


@dataclass
class TokenInfo:
    access_token: str
    # Unix epoch seconds when token expires; None if unknown
    expires_at: Optional[int] = None


class TokenProvider:
    """
    - Fetches tokens from an auth endpoint using Basic auth (username/password).
    - Parses expiry from JSON { "access_token": "...", "expires_in": 3600 } OR JWT "exp".
    - Caches and refreshes tokens with a small leeway.
    - Thread-safe (per-process).
    """

    def __init__(
        self,
        *,
        auth_url: str,
        username: str,
        password: str,
        timeout: float = 10.0,
        leeway_sec: int = 30,   # refresh this many seconds before actual expiry
    ):
        if not auth_url:
            raise ValueError("auth_url is required for TokenProvider")
        self.auth_url = auth_url
        self.username = username
        self.password = password
        self.timeout = timeout
        self.leeway_sec = max(0, int(leeway_sec))
        self._lock = threading.Lock()
        self._tok: Optional[TokenInfo] = None

    def get_valid_token(self) -> str:
        """Return a (fresh) access token, refreshing if needed."""
        with self._lock:
            if self._is_token_valid(self._tok):
                return self._tok.access_token  # type: ignore
            self._tok = self._fetch_token()
            return self._tok.access_token

    def refresh_now(self) -> str:
        """Force refresh (e.g., after 401) and return new token."""
        with self._lock:
            self._tok = self._fetch_token()
            return self._tok.access_token

    # ------------------ internals ------------------

    def _is_token_valid(self, tok: Optional[TokenInfo]) -> bool:
        if not tok or not tok.access_token:
            return False
        if tok.expires_at is None:
            return True  # no expiry info → assume valid until server says otherwise
        return (time.time() + self.leeway_sec) < tok.expires_at

    def _fetch_token(self) -> TokenInfo:
        with httpx.Client(timeout=self.timeout) as c:
            resp = c.post(self.auth_url, auth=(self.username, self.password))
            resp.raise_for_status()
            data = resp.json()
            token = (data.get("access_token") or data.get("token") or "").strip()
            if not token:
                raise RuntimeError(f"Auth response missing token: {data}")

            expires_at = self._infer_expiry(data, token)
            return TokenInfo(access_token=token, expires_at=expires_at)

    def _infer_expiry(self, data: Dict[str, Any], token: str) -> Optional[int]:
        # Prefer explicit expires_in
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            return int(time.time() + float(expires_in))

        # Try decode JWT "exp"
        parts = token.split(".")
        if len(parts) == 3:
            try:
                pad = "=" * (-len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
                exp = payload.get("exp")
                if isinstance(exp, (int, float)):
                    return int(exp)
            except Exception:
                pass

        # Unknown
        return None
