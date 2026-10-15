from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from controller_helpers import FakeWorker, auth, create_stream, later, requires_mongo

pytestmark = requires_mongo


async def get_stream(client, stream_id: str) -> dict:
    r = await client.get(f"/api/v1/streams/{stream_id}", headers=auth("viewer"))
    assert r.status_code == 200, r.text
    return r.json()


async def test_health_endpoints_need_no_auth(client):
    assert (await client.get("/livez")).json() == {"status": "ok"}
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json()["mongo"] is True


async def test_roles_are_enforced(client):
    assert (await client.get("/api/v1/streams")).status_code == 401
    assert (await client.get("/api/v1/streams", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await client.get("/api/v1/streams", headers=auth("viewer"))).status_code == 200
    body = {"name": "x", "source_url": "rtsp://cam.example/x"}
    assert (await client.post("/api/v1/streams", headers=auth("viewer"), json=body)).status_code == 403
    assert (await client.post("/api/v1/streams", headers=auth("worker"), json=body)).status_code == 403
    assert (await client.post("/api/v1/streams", headers=auth("operator"), json=body)).status_code == 201
    # only admins delete workers; viewers may read them
    assert (await client.get("/api/v1/workers", headers=auth("viewer"))).status_code == 200
    assert (await client.delete("/api/v1/workers/w-x", headers=auth("operator"))).status_code == 403
    # legacy header still works
    assert (await client.get("/api/v1/stats", headers={"watchtower-token": "viewer-token"})).status_code == 200


async def test_only_worker_or_admin_tokens_register(client):
    r = await client.post("/api/v1/workers/register", headers=auth("viewer"), json={"name": "w"})
    assert r.status_code == 403
    r = await client.post("/api/v1/workers/register", headers=auth("admin"), json={"name": "w"})
    assert r.status_code == 200


async def test_full_flow_register_place_sync_run(client, app):
    worker = FakeWorker(client, "w1")
    await worker.register()
    stream = await create_stream(client, "door")
    assert stream["status"] == "pending" and stream["worker_id"] is None
    assert stream["source_url"].startswith("rtsp://user:***@")  # password never leaves the API

    await app.state.scheduler.tick()
    placed = await get_stream(client, stream["stream_id"])
    assert placed["worker_id"] == worker.worker_id
    assert placed["status"] == "starting"

    desired = await worker.desired()
    assert [d["stream_id"] for d in desired] == [stream["stream_id"]]
    assert desired[0]["source_url"].startswith("rtsp://user:secret@")  # the worker gets the real URL
    generation = desired[0]["generation"]

    r = await worker.sync([{"stream_id": stream["stream_id"], "generation": generation, "status": "running",
                            "fps": 7.5}])
    assert r.status_code == 200
    running = await get_stream(client, stream["stream_id"])
    assert running["status"] == "running" and running["observed"]["fps"] == 7.5

    # A report for an old generation is ignored.
    await worker.sync([{"stream_id": stream["stream_id"], "generation": generation - 1, "status": "error"}])
    assert (await get_stream(client, stream["stream_id"]))["status"] == "running"

    view = (await client.get(f"/api/v1/streams/{stream['stream_id']}/view", headers=auth("viewer"))).json()
    assert view["webrtc_url"] == f"http://w1.example:8889/{stream['stream_id']}/"


async def test_worker_sync_auth(client):
    worker = FakeWorker(client, "w1")
    await worker.register()
    wid = worker.worker_id
    r = await client.post(f"/api/v1/workers/{wid}/sync", json={},
                          headers={"Authorization": "Bearer wrong", "X-Watchtower-Token": "worker-token"})
    assert r.status_code == 401  # wrong secret: register again
    r = await client.post(f"/api/v1/workers/{wid}/sync", json={}, headers={"Authorization": f"Bearer {worker.secret}"})
    assert r.status_code == 403  # token missing
    r = await client.post(f"/api/v1/workers/{wid}/sync", json={},
                          headers={"Authorization": f"Bearer {worker.secret}", "X-Watchtower-Token": "revoked"})
    assert r.status_code == 403  # token rejected: stop everything
    r = await client.post(f"/api/v1/workers/{wid}/sync", json={},
                          headers={"Authorization": f"Bearer {worker.secret}", "X-Watchtower-Token": "viewer-token"})
    assert r.status_code == 403  # token without the worker role
    # Registering again rotates the secret.
    old = worker.secret
    r = await client.post("/api/v1/workers/register", headers=auth("worker"), json={"name": "w1", "worker_id": wid})
    assert r.json()["worker_id"] == wid and r.json()["worker_secret"] != old
    r = await client.post(f"/api/v1/workers/{wid}/sync", json={},
                          headers={"Authorization": f"Bearer {old}", "X-Watchtower-Token": "worker-token"})
    assert r.status_code == 401


async def test_stop_and_start(client, app):
    worker = FakeWorker(client, "w1")
    await worker.register()
    stream = await create_stream(client)
    await app.state.scheduler.tick()
    assert len(await worker.desired()) == 1

    r = await client.post(f"/api/v1/streams/{stream['stream_id']}/stop", headers=auth("operator"))
    assert r.json()["status"] == "stopped" and r.json()["worker_id"] is None
    assert await worker.desired() == []
    await app.state.scheduler.tick()
    assert await worker.desired() == []  # stopped streams are never placed

    r = await client.post(f"/api/v1/streams/{stream['stream_id']}/start", headers=auth("operator"))
    assert r.json()["status"] == "pending"
    await app.state.scheduler.tick()
    assert len(await worker.desired()) == 1


async def test_create_stopped_stream(client, app):
    FakeWorker(client, "w1")
    stream = await create_stream(client, start=False)
    assert stream["status"] == "stopped" and stream["desired_state"] == "stopped"


async def test_placement_spreads_and_respects_capacity(client, app):
    w1, w2 = FakeWorker(client, "w1", max_streams=2), FakeWorker(client, "w2", max_streams=2)
    await w1.register()
    await w2.register()
    streams = [await create_stream(client, f"cam{i}") for i in range(5)]
    await app.state.scheduler.tick()
    assert len(await w1.desired()) == 2
    assert len(await w2.desired()) == 2
    statuses = [(await get_stream(client, s["stream_id"]))["status"] for s in streams]
    assert statuses.count("pending") == 1


async def test_offline_worker_streams_move(client, app):
    scheduler = app.state.scheduler
    w1, w2 = FakeWorker(client, "w1"), FakeWorker(client, "w2")
    await w1.register()
    stream = await create_stream(client)
    await scheduler.tick()
    assert (await get_stream(client, stream["stream_id"]))["worker_id"] == w1.worker_id
    await w2.register()

    scheduler.leader_since = datetime.now(UTC) - timedelta(hours=1)  # past the takeover grace
    # w1 stops syncing; w2 keeps going.
    future = later(app.state.settings.worker_offline_seconds + 5)
    await app.state.mongo.db.workers.update_one({"worker_id": w2.worker_id}, {"$set": {"last_seen": future}})
    await scheduler.tick(future)
    w1_doc = (await client.get(f"/api/v1/workers/{w1.worker_id}", headers=auth("viewer"))).json()
    assert w1_doc["state"] == "offline"
    moved = await get_stream(client, stream["stream_id"])
    assert moved["worker_id"] == w2.worker_id and moved["status"] == "starting"
    assert await w1.desired() == []  # w1 syncs again: it no longer owns the stream


async def test_takeover_grace_keeps_workers_suspect(client, app):
    scheduler = app.state.scheduler
    w1 = FakeWorker(client, "w1")
    await w1.register()
    stream = await create_stream(client)
    await scheduler.tick()
    scheduler.leader_since = datetime.now(UTC)  # this replica just became leader
    await scheduler.tick(later(app.state.settings.worker_offline_seconds - 10))
    doc = (await client.get(f"/api/v1/workers/{w1.worker_id}", headers=auth("viewer"))).json()
    assert doc["state"] == "suspect"
    assert (await get_stream(client, stream["stream_id"]))["worker_id"] == w1.worker_id


async def test_error_retries_with_backoff_then_fails(client, app):
    scheduler = app.state.scheduler
    settings = app.state.settings
    db = app.state.mongo.db
    w1 = FakeWorker(client, "w1")
    await w1.register()
    stream = await create_stream(client)
    sid = stream["stream_id"]
    now = datetime.now(UTC)

    async def tick():
        # The test clock runs ahead of real time; keep the worker looking alive on it.
        await db.workers.update_one({"worker_id": w1.worker_id}, {"$set": {"last_seen": now}})
        await scheduler.tick(now)

    for attempt in range(1, settings.max_start_attempts + 1):
        await tick()
        desired = await w1.desired()
        assert len(desired) == 1, f"attempt {attempt}: not placed"
        await w1.sync([{"stream_id": sid, "generation": desired[0]["generation"], "status": "error",
                        "error": "cannot open source"}])
        await tick()
        doc = await get_stream(client, sid)
        assert doc["attempts"] == attempt and doc["last_error"] == "cannot open source"
        if attempt < settings.max_start_attempts:
            assert doc["status"] == "pending"
            await tick()  # still backing off: not placed again yet
            assert await w1.desired() == []
            now = now + timedelta(seconds=settings.retry_backoff_seconds * 2 ** attempt + 1)
    assert doc["status"] == "failed"
    now = now + timedelta(days=1)
    await tick()
    assert await w1.desired() == []

    r = await client.post(f"/api/v1/streams/{sid}/start", headers=auth("operator"))  # manual retry
    assert r.json()["status"] == "pending" and r.json()["attempts"] == 0


async def test_unreported_placement_times_out(client, app):
    scheduler = app.state.scheduler
    w1 = FakeWorker(client, "w1")
    await w1.register()
    stream = await create_stream(client)
    await scheduler.tick()
    future = later(app.state.settings.placement_timeout_seconds + 1)
    await app.state.mongo.db.workers.update_one({"worker_id": w1.worker_id}, {"$set": {"last_seen": future}})
    await scheduler.tick(future)
    doc = await get_stream(client, stream["stream_id"])
    assert doc["attempts"] == 1 and doc["last_error"] == "worker did not start the stream in time"


async def test_drain_moves_streams(client, app):
    scheduler = app.state.scheduler
    w1, w2 = FakeWorker(client, "w1"), FakeWorker(client, "w2")
    await w1.register()
    stream = await create_stream(client)
    await scheduler.tick()
    await w2.register()
    r = await client.post(f"/api/v1/workers/{w1.worker_id}/drain", headers=auth("operator"), json={"draining": True})
    assert r.json()["draining"] is True
    await scheduler.tick()
    assert (await get_stream(client, stream["stream_id"]))["worker_id"] == w2.worker_id

    # A worker can drain itself too (graceful shutdown).
    await w2.sync(draining=True)
    await scheduler.tick()
    doc = await get_stream(client, stream["stream_id"])
    assert doc["worker_id"] is None and doc["status"] == "pending"


async def test_patch_semantics(client, app):
    stream = await create_stream(client, config={"fps": 5})
    sid = stream["stream_id"]
    r = await client.patch(f"/api/v1/streams/{sid}", headers=auth("operator"), json={"name": "renamed"})
    assert r.json()["name"] == "renamed" and r.json()["generation"] == stream["generation"]
    r = await client.patch(f"/api/v1/streams/{sid}", headers=auth("operator"), json={"config": {"fps": 10}})
    body = r.json()
    assert body["config"]["fps"] == 10 and body["config"]["detector_resolution"] == 320
    assert body["generation"] == stream["generation"] + 1  # config change restarts the engine
    r = await client.patch(f"/api/v1/streams/{sid}", headers=auth("operator"),
                           json={"config": {"detector_resolution": 480}})
    assert r.status_code == 422
    r = await client.patch(f"/api/v1/streams/{sid}", headers=auth("operator"), json={"metadata": {"floor": 2}})
    assert r.json()["metadata"] == {"floor": 2}


async def test_rejects_bad_and_duplicate_sources(client):
    bad = {"name": "x", "source_url": "file:///etc/passwd"}
    assert (await client.post("/api/v1/streams", headers=auth("operator"), json=bad)).status_code == 422
    body = {"name": "x", "source_url": "rtsp://cam.example/one"}
    assert (await client.post("/api/v1/streams", headers=auth("operator"), json=body)).status_code == 201
    assert (await client.post("/api/v1/streams", headers=auth("operator"), json=body)).status_code == 409


async def test_idempotent_create(client):
    body = {"name": "x", "source_url": "rtsp://cam.example/idem"}
    headers = {**auth("operator"), "Idempotency-Key": "abc"}
    first = await client.post("/api/v1/streams", headers=headers, json=body)
    second = await client.post("/api/v1/streams", headers=headers, json=body)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["stream_id"] == second.json()["stream_id"]
    # A failed create frees the key for a retry.
    dup = {"name": "y", "source_url": "rtsp://cam.example/idem"}
    headers2 = {**auth("operator"), "Idempotency-Key": "def"}
    assert (await client.post("/api/v1/streams", headers=headers2, json=dup)).status_code == 409
    dup["source_url"] = "rtsp://cam.example/other"
    assert (await client.post("/api/v1/streams", headers=headers2, json=dup)).status_code == 201


async def test_delete_worker_requires_drain_or_force(client, app):
    w1 = FakeWorker(client, "w1")
    await w1.register()
    stream = await create_stream(client)
    await app.state.scheduler.tick()
    assert (await client.delete(f"/api/v1/workers/{w1.worker_id}", headers=auth("admin"))).status_code == 409
    r = await client.delete(f"/api/v1/workers/{w1.worker_id}?force=true", headers=auth("admin"))
    assert r.status_code == 204
    await app.state.scheduler.tick()
    assert (await get_stream(client, stream["stream_id"]))["worker_id"] is None


async def test_stats(client, app):
    w1 = FakeWorker(client, "w1", max_streams=3)
    await w1.register()
    await create_stream(client)
    await create_stream(client, start=False)
    await app.state.scheduler.tick()
    stats = (await client.get("/api/v1/stats", headers=auth("viewer"))).json()
    assert stats["workers"]["online"] == 1
    assert stats["streams"]["starting"] == 1 and stats["streams"]["stopped"] == 1
    assert stats["capacity"] == {"total": 3, "online": 3, "used": 1}


@pytest.mark.parametrize(("body", "allowed"), [
    ({"action": "read", "ip": "127.0.0.1", "path": "s1"}, True),
    ({"action": "api", "ip": "::1"}, True),
    ({"action": "read", "ip": "10.1.2.3", "path": "s1"}, False),
    ({"action": "read", "ip": "10.1.2.3", "path": "s1", "query": "token=viewer-token"}, True),
    ({"action": "read", "ip": "10.1.2.3", "path": "s1", "token": "viewer-token"}, True),
    ({"action": "read", "ip": "10.1.2.3", "path": "s1", "password": "worker-token"}, False),
    ({"action": "publish", "ip": "10.1.2.3", "path": "s1", "token": "admin-token"}, False),
    ({"action": "read", "ip": "10.1.2.3", "path": "s1", "token": "bogus"}, False),
])
async def test_mediamtx_auth_hook(client, body, allowed):
    r = await client.post("/api/v1/mediamtx/auth", json=body)
    assert (r.status_code == 200) is allowed


async def test_me_and_dashboard(client):
    me = (await client.get("/api/v1/me", headers=auth("operator"))).json()
    assert me == {"subject": "static-2", "role": "operator"}
    r = await client.get("/")
    assert r.status_code == 307 and r.headers["location"] == "/ui/"
    page = await client.get("/ui/")
    assert page.status_code == 200 and "Watchtower" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    js = await client.get("/ui/app.js")
    assert js.status_code == 200 and ".innerHTML" not in js.text  # user data only via textContent
    assert "content-security-policy" not in (await client.get("/livez")).headers


async def test_metadata_keys_are_safe_for_mongo(client):
    for bad in ({"$where": "1"}, {"a.b": 1}, {"nested": {"$gt": 1}}, {"list": [{"x.y": 1}]}):
        r = await client.post("/api/v1/streams", headers=auth("operator"),
                              json={"name": "m", "source_url": "rtsp://cam.example/meta", "metadata": bad})
        assert r.status_code == 422, bad
    stream = await create_stream(client, metadata={"floor": 2, "tags": ["entry"]})
    r = await client.patch(f"/api/v1/streams/{stream['stream_id']}", headers=auth("operator"),
                           json={"metadata": {"$set": {"x": 1}}})
    assert r.status_code == 422


async def test_audit_log_records_who_changed_what(client):
    stream = await create_stream(client, "audited")
    await client.post(f"/api/v1/streams/{stream['stream_id']}/stop", headers=auth("operator"))
    assert (await client.get("/api/v1/audit", headers=auth("operator"))).status_code == 403
    entries = (await client.get("/api/v1/audit", headers=auth("admin"))).json()
    assert [e["action"] for e in entries[:2]] == ["stream.stop", "stream.create"]
    create = entries[1]
    assert create["subject"] == "static-2" and create["role"] == "operator"
    assert create["target_id"] == stream["stream_id"]
    assert ":***@" in create["details"]["source_url"]  # passwords never reach the audit log
    filtered = (await client.get("/api/v1/audit?action=stream.stop", headers=auth("admin"))).json()
    assert len(filtered) == 1


async def test_metrics(client, app):
    assert (await client.get("/metrics")).status_code == 401  # needs a token by default; not a failed sign-in
    bad = await client.get("/api/v1/stats", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401  # this one counts
    worker = FakeWorker(client, "w1", max_streams=3)
    await worker.register()
    await create_stream(client)
    await app.state.scheduler.tick()
    text = (await client.get("/metrics", headers=auth("viewer"))).text
    assert 'watchtower_workers{state="online"} 1' in text
    assert 'watchtower_streams{status="starting"} 1' in text
    assert 'watchtower_capacity_slots{kind="used"} 1' in text
    assert f'watchtower_worker_assigned_streams{{worker_id="{worker.worker_id}"}} 1' in text
    assert "watchtower_auth_failures_total 1" in text


async def test_oversized_bodies_are_rejected(client, app):
    limit = app.state.settings.max_request_bytes
    huge = {"action": "read", "ip": "10.0.0.1", "query": "x" * (limit + 10)}
    r = await client.post("/api/v1/mediamtx/auth", json=huge)  # unauthenticated endpoint
    assert r.status_code == 413

    async def chunks():  # no Content-Length: the limit is enforced while reading
        for _ in range(limit // 65536 + 2):
            yield b"x" * 65536

    r = await client.post("/api/v1/mediamtx/auth", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    small = await client.post("/api/v1/mediamtx/auth", json={"action": "read", "ip": "127.0.0.1"})
    assert small.status_code == 200


async def test_idempotency_key_of_a_deleted_stream_says_so(client):
    headers = {**auth("operator"), "Idempotency-Key": "reused"}
    body = {"name": "x", "source_url": "rtsp://cam.example/reused"}
    created = await client.post("/api/v1/streams", headers=headers, json=body)
    await client.delete(f"/api/v1/streams/{created.json()['stream_id']}", headers=auth("operator"))
    again = await client.post("/api/v1/streams", headers=headers, json=body)
    assert again.status_code == 409 and "deleted" in again.json()["detail"]
