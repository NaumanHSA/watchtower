from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import queue
import time

import httpx
import pytest
from worker_helpers import crashing_engine, well_behaved_engine

import watchtower_worker.supervisor as supervisor_module
from watchtower_contracts import DeliveryMode, DesiredStream, ObservedStatus, StreamConfig
from watchtower_worker.agent import Agent
from watchtower_worker.controller_client import ControllerClient
from watchtower_worker.delivery import Route
from watchtower_worker.health import create_health_app
from watchtower_worker.identity import Identity
from watchtower_worker.mediamtx import MediaMTX
from watchtower_worker.settings import WorkerSettings
from watchtower_worker.supervisor import Supervisor

STREAM_ID = "0b8f8a52-2f3c-4f39-9df4-6a0d1b7a1e11"


def desired(generation: int = 1, source: str = "rtsp://cam/1") -> DesiredStream:
    return DesiredStream(stream_id=STREAM_ID, generation=generation, name="Door", source_url=source,
                         config=StreamConfig())


# ------------------------------------------------------------------ supervisor (real spawned processes)

def make_supervisor(target, max_restarts=2, mediamtx=None):
    ctx = mp.get_context("spawn")
    routes: dict[str, Route] = {}
    sup = Supervisor(worker_id="w1", mediamtx=mediamtx, engine_input="mediamtx", detector_backend="onnx",
                     default_delivery_mode=DeliveryMode.REALTIME, max_restarts=max_restarts,
                     results_queue=ctx.Queue(), set_route=routes.__setitem__, engine_target=target, mp_context=ctx)
    return sup, routes


def poll_until(sup, predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sup.poll()
        if predicate():
            return True
        time.sleep(0.1)
    return False


async def test_supervisor_starts_restarts_on_generation_and_stops():
    sup, routes = make_supervisor(well_behaved_engine)
    try:
        await sup.reconcile([desired(1)], "http://results/in")
        assert routes[STREAM_ID] == Route("http://results/in", DeliveryMode.REALTIME)
        assert poll_until(sup, lambda: sup.observed()[0].status == ObservedStatus.RUNNING)
        first_pid = sup.streams[STREAM_ID].process.pid

        await sup.reconcile([desired(2)], None)  # new generation: restart the engine
        assert sup.streams[STREAM_ID].desired.generation == 2
        assert sup.streams[STREAM_ID].process.pid != first_pid
        assert poll_until(sup, lambda: sup.observed()[0].generation == 2
                          and sup.observed()[0].status == ObservedStatus.RUNNING)

        await sup.reconcile([], None)
        assert sup.streams == {}
    finally:
        await sup.stop_all()


async def test_supervisor_restarts_crashes_then_gives_up(monkeypatch):
    monkeypatch.setattr(supervisor_module, "RESTART_BACKOFF_SECONDS", (0,))
    sup, _ = make_supervisor(crashing_engine, max_restarts=2)
    try:
        await sup.reconcile([desired(1)], None)
        assert poll_until(sup, lambda: sup.streams[STREAM_ID].gave_up, timeout=30)
        observed = sup.observed()[0]
        assert observed.status == ObservedStatus.ERROR and observed.restarts == 3 and observed.error == "boom"
    finally:
        await sup.stop_all()


STALE = "11111111-2222-3333-4444-555555555555"


class FakeMediaMTX:
    """Keeps paths in memory, like MediaMTX does for API-added paths."""

    def __init__(self, ok=True):
        self.ok = ok
        self.up = True
        self.paths = {STALE, "all_others"}  # left over from before a worker restart
        self.deleted: list[str] = []

    async def add_path(self, name, source):
        if not (self.ok and self.up):
            return False, "HTTP 500"
        self.paths.add(name)
        return True, None

    async def delete_path(self, name):
        self.deleted.append(name)
        self.paths.discard(name)
        return True

    async def list_paths(self):
        return sorted(self.paths) if self.up else None

    def restream_url(self, name):
        return f"rtsp://127.0.0.1:8554/{name}"

    is_managed = staticmethod(MediaMTX.is_managed)


async def test_supervisor_uses_mediamtx_and_cleans_stale_paths():
    mtx = FakeMediaMTX()
    sup, _ = make_supervisor(well_behaved_engine, mediamtx=mtx)
    try:
        await sup.reconcile([desired(1)], None)
        assert sup.streams[STREAM_ID].spec.input_url == f"rtsp://127.0.0.1:8554/{STREAM_ID}"
        assert mtx.deleted == [STALE]  # ours but unassigned; all_others is not ours
        await sup.reconcile([desired(2)], None)  # same source: path kept
        assert mtx.deleted == [STALE] and STREAM_ID in mtx.paths
        await sup.reconcile([], None)
        assert STREAM_ID in mtx.deleted
    finally:
        await sup.stop_all()


async def test_supervisor_restores_paths_after_mediamtx_restart():
    mtx = FakeMediaMTX()
    sup, _ = make_supervisor(well_behaved_engine, mediamtx=mtx)
    try:
        await sup.reconcile([desired(1)], None)
        mtx.up = False  # MediaMTX restarting
        await sup.reconcile([desired(1)], None)
        mtx.up, mtx.paths = True, {"all_others"}  # back, with its in-memory paths gone
        await sup.reconcile([desired(1)], None)
        assert STREAM_ID in mtx.paths
        assert sup.observed()[0].error is None
    finally:
        await sup.stop_all()


async def test_supervisor_falls_back_to_camera_when_mediamtx_fails():
    sup, _ = make_supervisor(well_behaved_engine, mediamtx=FakeMediaMTX(ok=False))
    try:
        await sup.reconcile([desired(1)], None)
        assert sup.streams[STREAM_ID].spec.input_url == "rtsp://cam/1"
        assert "live view unavailable" in sup.observed()[0].error
    finally:
        await sup.stop_all()


# ------------------------------------------------------------------ agent protocol (fake controller)

class FakeSupervisor:
    def __init__(self):
        self.worker_id = "unregistered"
        self.reconciled: list[tuple[list, str | None]] = []
        self.stopped = 0
        self.streams: dict = {}

    async def reconcile(self, desired, results_url):
        self.reconciled.append((desired, results_url))
        self.streams = {d.stream_id: d for d in desired}

    async def stop_all(self):
        self.stopped += 1
        self.streams = {}

    def observed(self):
        return []

    def poll(self):
        pass


class FakeDelivery:
    def __init__(self):
        self.key = None

    def set_key(self, key):
        self.key = key

    def set_route(self, *a):
        pass

    def stats(self):
        from watchtower_contracts import DeliveryStats

        return DeliveryStats()


class FakeController:
    def __init__(self):
        self.mode = "ok"
        self.registrations = 0
        self.syncs: list[dict] = []
        self.secret = "s-1"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.mode == "down":
            raise httpx.ConnectError("down")
        if request.url.path.endswith("/register"):
            if request.headers["authorization"] != "Bearer worker-token":
                return httpx.Response(401)
            self.registrations += 1
            self.secret = f"s-{self.registrations}"
            body = json.loads(request.content)
            return httpx.Response(200, json={"worker_id": body.get("worker_id") or "w-abc",
                                             "worker_secret": self.secret, "sync_interval_ms": 2000,
                                             "lease_seconds": 60})
        if self.mode == "revoked":
            return httpx.Response(403)
        if request.headers["authorization"] != f"Bearer {self.secret}":
            return httpx.Response(401)
        assert request.headers["x-watchtower-token"] == "worker-token"
        self.syncs.append(json.loads(request.content))
        return httpx.Response(200, json={
            "desired": [desired(3).model_dump(mode="json")], "results_url": "http://results/in",
            "result_key": {"kid": "k1", "public_key_pem": "PEM"}, "sync_interval_ms": 2000, "lease_seconds": 60})


@pytest.fixture
def agent_parts(tmp_path):
    controller = FakeController()
    settings = WorkerSettings(_env_file=None, controller_url="http://controller", watchtower_token="worker-token",
                              state_dir=tmp_path, worker_name="test-worker", mediamtx_enabled=False)
    client = ControllerClient(settings.controller_url, settings.watchtower_token,
                              transport=httpx.MockTransport(controller.handler))
    sup, delivery = FakeSupervisor(), FakeDelivery()
    agent = Agent(settings, client=client, delivery=delivery, results_queue=queue.Queue(), supervisor=sup)
    return agent, controller, sup, delivery


async def test_agent_registers_syncs_and_reconciles(agent_parts, tmp_path):
    agent, controller, sup, delivery = agent_parts
    delay = await agent.step()
    assert 1.5 < delay < 2.5  # sync interval from the controller, with jitter
    assert controller.registrations == 1
    assert Identity.load(tmp_path).worker_id == "w-abc"
    assert sup.worker_id == "w-abc"
    assert sup.reconciled[-1][1] == "http://results/in"
    assert sup.reconciled[-1][0][0].generation == 3
    assert delivery.key.kid == "k1"
    assert agent.ready


async def test_agent_reregisters_on_401_with_same_id(agent_parts):
    agent, controller, _, _ = agent_parts
    await agent.step()
    controller.secret = "rotated-by-someone-else"
    assert await agent.step() == 1.0  # 401: forget the secret
    assert agent.identity is None
    await agent.step()
    assert controller.registrations == 2
    assert agent.identity.worker_id == "w-abc"


async def test_agent_stops_streams_when_token_revoked(agent_parts):
    agent, controller, sup, _ = agent_parts
    await agent.step()
    controller.mode = "revoked"
    assert await agent.step() == 60.0
    assert sup.stopped == 1 and agent.revoked and not agent.ready
    await agent.step()
    assert sup.stopped == 1  # only once
    controller.mode = "ok"
    await agent.step()
    assert not agent.revoked


async def test_agent_keeps_running_through_controller_outage_by_default(agent_parts):
    agent, controller, sup, _ = agent_parts
    await agent.step()
    controller.mode = "down"
    agent.last_sync_ok = time.monotonic() - 3600  # long past the lease
    delay = await agent.step()
    assert 0 < delay <= 30 and sup.stopped == 0


async def test_agent_stop_policy_on_lease_expiry(agent_parts):
    agent, controller, sup, _ = agent_parts
    agent.settings = agent.settings.model_copy(update={"on_lease_expiry": "stop"})
    await agent.step()
    controller.mode = "down"
    await agent.step()
    assert sup.stopped == 0  # lease still valid
    agent.last_sync_ok = time.monotonic() - 3600
    await agent.step()
    await agent.step()
    assert sup.stopped == 1


async def test_agent_bad_token_backs_off(tmp_path):
    controller = FakeController()
    settings = WorkerSettings(_env_file=None, controller_url="http://controller", watchtower_token="nope",
                              state_dir=tmp_path, mediamtx_enabled=False)
    client = ControllerClient("http://controller", "nope", transport=httpx.MockTransport(controller.handler))
    agent = Agent(settings, client=client, delivery=FakeDelivery(), results_queue=queue.Queue(),
                  supervisor=FakeSupervisor())
    assert await agent.step() == 60.0  # not a hot loop


async def test_agent_drains_on_shutdown(agent_parts):
    agent, controller, sup, _ = agent_parts
    await agent.step()
    await agent.drain_and_stop()
    assert controller.syncs[-1]["draining"] is True and controller.syncs[-1]["streams"] == []
    assert sup.stopped == 1


# ------------------------------------------------------------------ health app + MediaMTX hook

async def test_health_and_mediamtx_hook(agent_parts):
    agent, *_ = agent_parts
    forwarded: list[dict] = []

    async def authorize(body):
        forwarded.append(body)
        return body.get("token") == "viewer-token"

    agent.client.authorize_viewer = authorize
    app = create_health_app(agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w") as c:
        assert (await c.get("/livez")).status_code == 200
        assert (await c.get("/readyz")).status_code == 503  # never synced
        await agent.step()
        assert (await c.get("/readyz")).status_code == 200
        assert "watchtower_worker_ready 1" in (await c.get("/metrics")).text

        assert (await c.post("/mediamtx/auth", json={"ip": "127.0.0.1", "action": "read"})).status_code == 200
        assert forwarded == []  # local reads never leave the worker
        assert (await c.post("/mediamtx/auth", json={"ip": "10.0.0.9", "action": "api"})).status_code == 401
        viewer = {"ip": "10.0.0.9", "action": "read", "path": STREAM_ID, "token": "viewer-token"}
        assert (await c.post("/mediamtx/auth", json=viewer)).status_code == 200
        assert (await c.post("/mediamtx/auth", json=viewer)).status_code == 200
        assert len(forwarded) == 1  # cached
        bad = {**viewer, "token": "nope"}
        assert (await c.post("/mediamtx/auth", json=bad)).status_code == 401


# ------------------------------------------------------------------ MediaMTX client

async def test_mediamtx_client():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.startswith("/v3/config/paths/add/dup"):
            return httpx.Response(400, json={"error": "path already exists"})
        if request.url.path.startswith("/v3/config/paths/delete/gone"):
            return httpx.Response(404)
        if request.url.path == "/v3/config/paths/list":
            return httpx.Response(200, json={"items": [{"name": "a"}, {"name": "b"}]})
        if request.url.path.startswith("/v3/config/paths/add/wrapped"):
            return httpx.Response(200, json={"Error": "camera offline", "IsSuccess": False})
        return httpx.Response(200)

    mtx = MediaMTX("http://mtx:9997", "rtsp://127.0.0.1:8554", transport=httpx.MockTransport(handler))
    assert await mtx.add_path("new", "rtsp://cam") == (True, None)
    assert await mtx.add_path("dup", "rtsp://cam") == (True, None)
    assert ("POST", "/v3/config/paths/replace/dup") in calls
    assert (await mtx.add_path("wrapped", "rtsp://cam"))[1] == "camera offline"
    assert await mtx.delete_path("gone") is True
    assert await mtx.list_paths() == ["a", "b"]
    assert mtx.restream_url("x") == "rtsp://127.0.0.1:8554/x"
    await mtx.aclose()


def test_settings_reject_bad_urls_at_startup(tmp_path):
    with pytest.raises(ValueError):
        WorkerSettings(_env_file=None, controller_url="", state_dir=tmp_path)
    with pytest.raises(ValueError):
        WorkerSettings(_env_file=None, public_webrtc_url="203.0.113.10:8889", state_dir=tmp_path)
    ok = WorkerSettings(_env_file=None, results_url="", state_dir=tmp_path)
    assert ok.results_url is None


async def test_agent_treats_unreadable_answers_as_retryable(agent_parts):
    agent, controller, sup, _ = agent_parts
    await agent.step()
    original = controller.handler
    controller.handler = lambda request: httpx.Response(200, json={"unexpected": True})
    agent.client._client._transport = httpx.MockTransport(controller.handler)
    delay = await agent.step()
    assert 0 < delay <= 30 and agent.consecutive_failures == 1
    controller.handler = original


async def test_agent_loop_survives_a_crashing_round(agent_parts):
    agent, *_ = agent_parts
    calls = {"n": 0}

    async def flaky_step():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bug")
        agent._stopping.set()
        return 0.01

    agent.step = flaky_step
    import watchtower_worker.agent as agent_module
    agent_module.MAX_BACKOFF_SECONDS = 0.01
    try:
        await asyncio.wait_for(agent.run(), timeout=5)
    finally:
        agent_module.MAX_BACKOFF_SECONDS = 30.0
    assert calls["n"] == 2
