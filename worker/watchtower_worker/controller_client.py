from __future__ import annotations

import httpx
from pydantic import ValidationError

from watchtower_contracts import RegisterRequest, RegisterResponse, SyncRequest, SyncResponse

WORKER_TOKEN_HEADER = "X-Watchtower-Token"  # noqa: S105 - a header name


class ControllerError(Exception):
    pass


class Unauthorized(ControllerError):
    """401: the controller doesn't know this worker or its secret. Register again."""


class Forbidden(ControllerError):
    """403: the worker token was rejected. Stop all streams."""


class Unavailable(ControllerError):
    """Network error, timeout, 5xx or an unexpected answer. Retry later."""


class ControllerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.token = token
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    async def register(self, body: RegisterRequest) -> RegisterResponse:
        r = await self._post("/api/v1/workers/register", body.model_dump(mode="json"),
                             {"Authorization": f"Bearer {self.token}"})
        return self._parse(RegisterResponse, r)

    async def sync(self, worker_id: str, secret: str, body: SyncRequest) -> SyncResponse:
        r = await self._post(f"/api/v1/workers/{worker_id}/sync", body.model_dump(mode="json"),
                             {"Authorization": f"Bearer {secret}", WORKER_TOKEN_HEADER: self.token})
        return self._parse(SyncResponse, r)

    async def authorize_viewer(self, hook_body: dict) -> bool:
        """Forward a MediaMTX auth request for a remote viewer to the controller."""
        try:
            r = await self._client.post("/api/v1/mediamtx/auth", json=hook_body)
        except httpx.HTTPError:
            return False
        return r.is_success

    async def _post(self, path: str, body: dict, headers: dict[str, str]) -> httpx.Response:
        try:
            r = await self._client.post(path, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise Unavailable(f"controller unreachable: {e}") from e
        if r.status_code == 401:
            raise Unauthorized(r.text[:200])
        if r.status_code == 403:
            raise Forbidden(r.text[:200])
        if not r.is_success:
            raise Unavailable(f"controller returned HTTP {r.status_code}: {r.text[:200]}")
        return r

    @staticmethod
    def _parse(model, response: httpx.Response):
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as e:
            raise Unavailable(f"controller sent an unexpected answer: {e}") from e

    async def aclose(self) -> None:
        await self._client.aclose()
