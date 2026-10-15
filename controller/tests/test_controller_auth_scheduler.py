from __future__ import annotations

import time

import httpx
import pytest
from controller_helpers import MONGO_URI, auth, make_settings, requires_mongo
from fastapi.testclient import TestClient
from pymongo import MongoClient
from starlette.websockets import WebSocketDisconnect

from watchtower_contracts import Role
from watchtower_controller.app import create_app
from watchtower_controller.auth import AuthError, AuthUnavailable, HttpTokenProvider
from watchtower_controller.db import Mongo
from watchtower_controller.events import Notifier
from watchtower_controller.scheduler import Scheduler


class FakeTokenServer:
    def __init__(self):
        self.calls = 0
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.down:
            return httpx.Response(503)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token == "legacy":  # the original platform's answer: no role
            return httpx.Response(200, json={"isSuccess": True, "pub_cov": "PEM"})
        if token == "viewer":
            return httpx.Response(200, json={"isSuccess": True, "role": "viewer", "subject": "alice", "kid": "k1"})
        if token == "weird-role":
            return httpx.Response(200, json={"isSuccess": True, "role": "superuser"})
        if token == "forbidden":
            return httpx.Response(401)
        return httpx.Response(200, json={"isSuccess": False, "message": "Unknown token"})


@pytest.fixture
def token_server():
    server = FakeTokenServer()
    provider = HttpTokenProvider("http://tokens/verify", cache_seconds=60, timeout=1,
                                 transport=httpx.MockTransport(server.handler))
    return server, provider


async def test_http_provider_roles_and_legacy(token_server):
    _, provider = token_server
    legacy = await provider.verify("legacy")
    assert legacy.role == Role.ADMIN and legacy.public_key_pem == "PEM"
    viewer = await provider.verify("viewer")
    assert viewer.role == Role.VIEWER and viewer.subject == "alice" and viewer.kid == "k1"
    assert viewer.has_role(Role.VIEWER) and not viewer.has_role(Role.OPERATOR)
    for bad in ("nope", "forbidden", "weird-role"):
        with pytest.raises(AuthError):
            await provider.verify(bad)


async def test_http_provider_caches_and_survives_outage(token_server):
    server, provider = token_server
    await provider.verify("viewer")
    await provider.verify("viewer")
    assert server.calls == 1  # cached
    server.down = True
    fp = next(iter(provider._cache))
    provider._cache[fp] = (time.monotonic() - 1, provider._cache[fp][1])  # just expired
    assert (await provider.verify("viewer")).role == Role.VIEWER  # stale answer served during the outage
    with pytest.raises(AuthUnavailable):
        await provider.verify("never-seen")


@requires_mongo
async def test_only_one_scheduler_leads():
    settings = make_settings()
    mongo = Mongo(settings.mongo_uri, settings.mongo_db)
    notifier = Notifier()
    a = Scheduler(mongo, settings, notifier, "a")
    b = Scheduler(mongo, settings, notifier, "b")
    try:
        assert await a._hold_lease() is True
        assert await b._hold_lease() is False
        assert await a._hold_lease() is True  # renewal
        await a.release_lease()
        assert await b._hold_lease() is True
    finally:
        await mongo.close()
        MongoClient(MONGO_URI).drop_database(settings.mongo_db)


@requires_mongo
def test_events_websocket_streams_changes():
    settings = make_settings()
    try:
        with TestClient(create_app(settings)) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/api/v1/events") as ws:  # no token
                    ws.receive_json()
            with client.websocket_connect("/api/v1/events?token=viewer-token") as ws:
                r = client.post("/api/v1/streams", headers=auth("operator"),
                                json={"name": "lobby", "source_url": "rtsp://cam.example/lobby"})
                assert r.status_code == 201
                event = ws.receive_json()
                assert event["event"] == "stream.created" and event["params"]["name"] == "lobby"
    finally:
        MongoClient(MONGO_URI).drop_database(settings.mongo_db)


def test_settings_refuse_unsafe_auth():
    with pytest.raises(ValueError):
        make_settings(auth_provider="none")
    with pytest.raises(ValueError):
        make_settings(auth_provider="http", token_server_url=None)
    assert make_settings(auth_provider="none", allow_no_auth=True).auth_provider == "none"


@requires_mongo
def test_events_websocket_accepts_token_as_subprotocol():
    settings = make_settings()
    try:
        with TestClient(create_app(settings)) as client:
            proto = "watchtower.bearer.viewer-token"
            with client.websocket_connect("/api/v1/events", subprotocols=[proto]) as ws:
                assert ws.accepted_subprotocol == proto
                client.post("/api/v1/streams", headers=auth("operator"),
                            json={"name": "gate", "source_url": "rtsp://cam.example/gate"})
                assert ws.receive_json()["event"] == "stream.created"
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/api/v1/events", subprotocols=["watchtower.bearer.bogus"]) as ws:
                    ws.receive_json()
    finally:
        MongoClient(MONGO_URI).drop_database(settings.mongo_db)


def test_failure_limiter_window():
    from watchtower_controller.ratelimit import FailureLimiter

    limiter = FailureLimiter(limit=2, window_seconds=0.2)
    assert limiter.retry_after("1.2.3.4") is None
    limiter.record_failure("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 1
    assert limiter.retry_after("5.6.7.8") is None  # per address
    time.sleep(0.25)
    assert limiter.retry_after("1.2.3.4") is None


@requires_mongo
async def test_repeated_bad_tokens_are_rate_limited():
    settings = make_settings(auth_failure_limit=3, auth_failure_window_seconds=60)
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                guess = {"Authorization": "Bearer guess"}
                for _ in range(2):
                    assert (await c.get("/api/v1/streams", headers=guess)).status_code == 401
                # A wrong worker secret counts as a failed sign-in too.
                r = await c.post("/api/v1/workers/w-x/sync", json={}, headers={"Authorization": "Bearer nope"})
                assert r.status_code == 401
                r = await c.get("/api/v1/streams", headers=guess)
                assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
                # The block covers the address, even with a valid token, until the window passes.
                r = await c.get("/api/v1/streams", headers=auth("viewer"))
                assert r.status_code == 429
    finally:
        MongoClient(MONGO_URI).drop_database(settings.mongo_db)


@requires_mongo
def test_jwt_shaped_tokens_work_as_websocket_subprotocol():
    jwt_like = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXZhbHVl"
    settings = make_settings(static_tokens=f"viewer:{jwt_like},operator:operator-token")
    try:
        with TestClient(create_app(settings)) as client:
            proto = f"watchtower.bearer.{jwt_like}"
            with client.websocket_connect("/api/v1/events", subprotocols=[proto]) as ws:
                assert ws.accepted_subprotocol == proto
    finally:
        MongoClient(MONGO_URI).drop_database(settings.mongo_db)
