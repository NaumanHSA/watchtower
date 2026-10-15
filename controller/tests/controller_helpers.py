from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from watchtower_controller.settings import Settings

MONGO_URI = os.environ.get("TEST_MONGO_URI", "mongodb://localhost:27018")

TOKENS = {
    "admin": "admin-token",
    "operator": "operator-token",
    "viewer": "viewer-token",
    "worker": "worker-token",
}


def _mongo_available() -> bool:
    try:
        MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500).admin.command("ping")
        return True
    except PyMongoError:
        return False


requires_mongo = pytest.mark.skipif(not _mongo_available(), reason=f"MongoDB not reachable at {MONGO_URI}")


def auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def make_settings(**overrides) -> Settings:
    values = dict(
        mongo_uri=MONGO_URI,
        mongo_db=f"wt_test_{uuid.uuid4().hex[:10]}",
        auth_provider="static",
        static_tokens=",".join(f"{role}:{token}" for role, token in TOKENS.items()),
        scheduler_enabled=False,  # tests drive the scheduler by hand
        _env_file=None,
    )
    values.update(overrides)
    return Settings(**values)


class FakeWorker:
    """Speaks the worker side of the protocol."""

    def __init__(self, client: httpx.AsyncClient, name: str, max_streams: int = 2):
        self.client = client
        self.name = name
        self.max_streams = max_streams
        self.worker_id: str | None = None
        self.secret: str | None = None

    async def register(self) -> None:
        r = await self.client.post("/api/v1/workers/register", headers=auth("worker"), json={
            "name": self.name, "version": "test", "capacity": {"max_streams": self.max_streams},
            "webrtc_base_url": f"http://{self.name}.example:8889",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        self.worker_id, self.secret = body["worker_id"], body["worker_secret"]

    async def sync(self, streams: list[dict] | None = None, **extra) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/workers/{self.worker_id}/sync",
            headers={"Authorization": f"Bearer {self.secret}", "X-Watchtower-Token": TOKENS["worker"]},
            json={"version": "test", "capacity": {"max_streams": self.max_streams}, "streams": streams or [], **extra},
        )

    async def desired(self) -> list[dict]:
        r = await self.sync()
        assert r.status_code == 200, r.text
        return r.json()["desired"]


async def create_stream(client: httpx.AsyncClient, name: str = "cam", **body) -> dict:
    payload = {"name": name, "source_url": f"rtsp://user:secret@cam.example/{name}-{uuid.uuid4().hex[:6]}", **body}
    r = await client.post("/api/v1/streams", headers=auth("operator"), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def later(seconds: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)
