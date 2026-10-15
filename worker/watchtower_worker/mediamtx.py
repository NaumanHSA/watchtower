"""MediaMTX control API client (v3). MediaMTX pulls the camera and re-serves it to the
engine (RTSP on localhost) and to viewers (WebRTC)."""
from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger(__name__)

# Stream ids are UUIDs; only paths shaped like that are ours to delete.
_MANAGED = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class MediaMTX:
    def __init__(self, api_url: str, rtsp_url: str, *, username: str | None = None, password: str | None = None,
                 bearer_token: str | None = None, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        auth = (username, password) if username and password and not bearer_token else None
        self._client = httpx.AsyncClient(base_url=api_url.rstrip("/"), headers=headers, auth=auth, timeout=timeout,
                                         transport=transport)
        self._rtsp_url = rtsp_url.rstrip("/")

    def restream_url(self, name: str) -> str:
        return f"{self._rtsp_url}/{name}"

    @staticmethod
    def is_managed(name: str) -> bool:
        return bool(_MANAGED.match(name))

    async def add_path(self, name: str, source: str) -> tuple[bool, str | None]:
        conf = {"source": source}
        try:
            r = await self._client.post(f"/v3/config/paths/add/{name}", json=conf)
            if r.status_code == 400 and "exist" in r.text.lower():
                r = await self._client.post(f"/v3/config/paths/replace/{name}", json=conf)
        except httpx.HTTPError as e:
            return False, f"MediaMTX unreachable: {e}"
        return self._result(r)

    async def delete_path(self, name: str) -> bool:
        try:
            r = await self._client.delete(f"/v3/config/paths/delete/{name}")
        except httpx.HTTPError as e:
            log.warning("MediaMTX delete %s failed: %s", name, e)
            return False
        if r.status_code == 404:
            return True
        ok, error = self._result(r)
        if not ok:
            log.warning("MediaMTX delete %s failed: %s", name, error)
        return ok

    async def list_paths(self) -> list[str] | None:
        try:
            r = await self._client.get("/v3/config/paths/list", params={"itemsPerPage": 1000})
            r.raise_for_status()
            return [item["name"] for item in r.json().get("items", [])]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.debug("MediaMTX list failed: %s", e)
            return None

    @staticmethod
    def _result(r: httpx.Response) -> tuple[bool, str | None]:
        if r.is_success:
            try:
                body = r.json() if r.content else {}
            except ValueError:
                body = {}
            # Some deployments front MediaMTX with a wrapper that reports {"Error": ..., "IsSuccess": ...}.
            if isinstance(body, dict) and (body.get("Error") or body.get("IsSuccess") is False):
                return False, str(body.get("Error") or "IsSuccess=false")
            return True, None
        return False, f"HTTP {r.status_code}: {r.text[:200]}"

    async def aclose(self) -> None:
        await self._client.aclose()
