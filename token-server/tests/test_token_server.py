from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet

from watchtower_contracts import Role
from watchtower_contracts.crypto import encrypt_json
from watchtower_controller.auth import AuthError, HttpTokenProvider
from watchtower_token_server.__main__ import bootstrap
from watchtower_token_server.app import create_app
from watchtower_token_server.settings import TokenServerSettings
from watchtower_token_server.store import Store

ADMIN_KEY = "admin-key-for-tests"


def settings(tmp_path, **overrides) -> TokenServerSettings:
    return TokenServerSettings(_env_file=None, data_dir=tmp_path, **overrides)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "store.json")


async def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ts")


async def test_verify_contract(tmp_path, store):
    _, worker = store.issue(Role.WORKER, "edge-1")
    _, viewer = store.issue(Role.VIEWER, "alice")
    async with await client_for(create_app(settings(tmp_path), store)) as c:
        r = (await c.get("/v1/token/verify", headers={"Authorization": f"Bearer {worker}"})).json()
        assert r["isSuccess"] is True and r["role"] == "worker" and r["subject"] == "edge-1"
        assert r["pub_cov"].startswith("-----BEGIN PUBLIC KEY") and r["kid"] == store.current_key().kid
        r = (await c.get("/v1/token/verify", headers={"Authorization": f"Bearer {viewer}"})).json()
        assert r["isSuccess"] is True and r["role"] == "viewer" and "pub_cov" not in r  # viewers get no key
        # The original platform's path and header still work.
        legacy = await c.get("/v1/watchTower/GetWatchTowerPubKey", headers={"watchtower-token": worker})
        assert legacy.json()["isSuccess"] is True
        assert (await c.get("/token/verify", headers={"watchtower-token": "nope"})).json() == {
            "isSuccess": False, "message": "Unknown token"}
        assert (await c.get("/v1/token/verify")).status_code == 400


async def test_revoked_and_expired_tokens_fail(tmp_path, store):
    record, token = store.issue(Role.WORKER, "edge-1")
    _, expired = store.issue(Role.VIEWER, "old", expires_in_days=1)
    store._data.tokens[store.lookup(expired).id].expires_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    store._save()
    async with await client_for(create_app(settings(tmp_path), store)) as c:
        assert (await c.get("/v1/token/verify", headers={"Authorization": f"Bearer {expired}"})).json()[
            "isSuccess"] is False
        # A revoke from the CLI (another Store on the same file) is seen by the running server.
        Store(tmp_path / "store.json").revoke(record.id)
        r = (await c.get("/v1/token/verify", headers={"Authorization": f"Bearer {token}"})).json()
        assert r == {"isSuccess": False, "message": "Token revoked or expired"}


async def test_tokens_are_stored_hashed(tmp_path, store):
    _, token = store.issue(Role.ADMIN, "root")
    assert token.startswith("wt_")
    assert token not in (tmp_path / "store.json").read_text()


async def test_admin_api(tmp_path, store):
    async with await client_for(create_app(settings(tmp_path), store)) as c:
        assert (await c.get("/v1/admin/tokens")).status_code == 404  # disabled without an admin key
    app = create_app(settings(tmp_path, admin_key=ADMIN_KEY), store)
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    async with await client_for(app) as c:
        assert (await c.get("/v1/admin/tokens", headers={"Authorization": "Bearer wrong"})).status_code == 401
        issued = (await c.post("/v1/admin/tokens", headers=admin, json={"role": "operator", "subject": "ops"})).json()
        assert issued["token"].startswith("wt_")
        listed = (await c.get("/v1/admin/tokens", headers=admin)).json()
        assert [t["subject"] for t in listed] == ["ops"] and "token" not in listed[0]
        assert (await c.post(f"/v1/admin/tokens/{issued['id']}/revoke", headers=admin)).json()["revoked"]
        old_kid = store.current_key().kid
        rotated = (await c.post("/v1/admin/keys/rotate", headers=admin)).json()
        keys = (await c.get("/v1/admin/keys", headers=admin)).json()
        assert [k["kid"] for k in keys] == [rotated["kid"], old_kid] and keys[0]["current"]
        exported = (await c.get(f"/v1/admin/keys/{old_kid}/private", headers=admin)).json()
        assert exported["private_pem"].startswith("-----BEGIN PRIVATE KEY")


async def test_master_key_encrypts_private_keys_at_rest(tmp_path):
    master = Fernet.generate_key().decode()
    store = Store(tmp_path / "store.json", master)
    store.issue(Role.WORKER, "edge")
    assert "BEGIN PRIVATE KEY" not in (tmp_path / "store.json").read_text()
    assert store.private_pem(store.current_key()).startswith("-----BEGIN PRIVATE KEY")
    with pytest.raises(RuntimeError):
        Store(tmp_path / "store.json").private_pem(store.current_key())


async def test_ingest_decrypts_by_kid_across_rotation_and_forwards(tmp_path, store):
    store.issue(Role.WORKER, "edge")
    old = store.current_key()
    forwarded: list[dict] = []
    forward_ok = {"value": True}

    def receiver(request: httpx.Request) -> httpx.Response:
        if not forward_ok["value"]:
            return httpx.Response(503)
        forwarded.append(json.loads(request.content))
        return httpx.Response(200)

    event = {"eventId": "e1", "streamId": "s1", "trackId": "1", "photo": "AAEC", "normalized": "AAEC",
             "detectorParamsCore": {"endOfTrack": False, "quality": 0.9, "trackDurationSeconds": 1.0},
             "bbox": {"xyxy": [1, 2, 3, 4]}}
    envelope = encrypt_json({"ok": True, "received": event}, old.public_pem, old.kid)
    store.rotate_key()  # results already in flight were encrypted with the old key

    app = create_app(settings(tmp_path, ingest_enabled=True, forward_url="http://app/hook",
                              save_images_dir=tmp_path / "images"), store,
                     forward_transport=httpx.MockTransport(receiver))
    async with await client_for(app) as c:
        r = await c.post("/v1/ingest", json=envelope, headers={"X-Enc": "v1"})
        assert r.json() == {"ok": True, "mode": "encrypted", "kid": old.kid}
        assert forwarded[0]["received"]["eventId"] == "e1"
        assert len(list((tmp_path / "images" / "s1").glob("*_photo.jpg"))) == 1
        forward_ok["value"] = False
        r = await c.post("/v1/ingest", json=envelope)
        assert r.status_code == 502  # a reliable-mode worker keeps the result and retries
        tampered = {**envelope, "ek": envelope["ek"][::-1]}
        assert (await c.post("/v1/ingest", json=tampered)).status_code == 400


async def test_bootstrap(tmp_path, monkeypatch):
    store = Store(tmp_path / "store.json")
    monkeypatch.setenv("TOKEN_SERVER_BOOTSTRAP_TOKENS", "admin:dev-admin:me,worker:dev-worker")
    bootstrap(settings(tmp_path), store)
    bootstrap(settings(tmp_path), store)  # idempotent
    assert sorted(t.role for t in store.tokens()) == ["admin", "worker"]
    assert store.lookup("dev-admin").subject == "me"
    assert not (tmp_path / "bootstrap-tokens.txt").exists()

    monkeypatch.delenv("TOKEN_SERVER_BOOTSTRAP_TOKENS")
    empty = tmp_path / "fresh"
    fresh = Store(empty / "store.json")
    bootstrap(settings(empty), fresh)
    lines = (empty / "bootstrap-tokens.txt").read_text().splitlines()
    assert [line.split(":")[0] for line in lines] == ["admin", "worker"]
    assert fresh.lookup(lines[0].split(": ")[1]).role == "admin"
    if os.name == "posix":
        assert (empty / "bootstrap-tokens.txt").stat().st_mode & 0o777 == 0o600


async def test_controller_provider_speaks_the_contract(tmp_path, store):
    """The controller's real HttpTokenProvider against the real token server."""
    _, worker = store.issue(Role.WORKER, "edge-1")
    _, viewer = store.issue(Role.VIEWER, "alice")
    app = create_app(settings(tmp_path), store)
    provider = HttpTokenProvider("http://ts/v1/token/verify", cache_seconds=60, timeout=5,
                                 transport=httpx.ASGITransport(app=app))
    try:
        principal = await provider.verify(worker)
        assert principal.role == Role.WORKER and principal.has_role(Role.WORKER)
        assert principal.kid == store.current_key().kid and principal.public_key_pem
        assert (await provider.verify(viewer)).has_role(Role.VIEWER)
        with pytest.raises(AuthError):
            await provider.verify("wt_unknown")
    finally:
        await provider.aclose()
