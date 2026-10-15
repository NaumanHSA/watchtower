from __future__ import annotations

import json
import queue
import threading
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from watchtower_contracts import DeliveryMode, ResultKey
from watchtower_contracts.crypto import decrypt_json
from watchtower_worker.delivery import DeliveryService, Route, Spool


class Receiver:
    def __init__(self):
        self.status = 200
        self.down = False
        self.bodies: list[dict] = []
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("receiver down")
        if self.status == 200:
            with self.lock:
                self.bodies.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx.Response(self.status)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def receiver():
    return Receiver()


def make_service(tmp_path, receiver, mode=DeliveryMode.REALTIME, **kwargs) -> DeliveryService:
    service = DeliveryService(results_queue=queue.Queue(), spool_path=tmp_path / "spool.sqlite3",
                              default_route=Route("http://receiver/in", mode),
                              transport=httpx.MockTransport(receiver.handler), **kwargs)
    service.start()
    return service


def event(n: int) -> dict:
    return {"eventId": f"e{n}", "n": n}


def test_realtime_delivers_plaintext(tmp_path, receiver):
    service = make_service(tmp_path, receiver)
    try:
        service.results_queue.put({"stream_id": "s1", "generation": 1, "event": event(1)})
        assert wait_for(lambda: receiver.bodies)
        sent = receiver.bodies[0]
        assert sent["body"] == {"ok": True, "received": event(1)}
        assert sent["headers"]["x-watchtower-event-id"] == "e1"
        assert wait_for(lambda: service.stats().sent == 1)
    finally:
        service.stop()


def test_realtime_drops_stale_and_overflow(tmp_path, receiver):
    receiver.down = True
    service = make_service(tmp_path, receiver, realtime_queue_size=3, max_age_seconds=0.2, realtime_senders=0)
    try:
        for n in range(5):
            service.accept("s1", event(n))
        stats = service.stats()
        assert stats.dropped == 2  # queue holds the newest 3
        assert [item[3] for item in service._realtime] and len(service._realtime) == 3
    finally:
        service.stop()


def test_realtime_never_delivers_late(tmp_path, receiver):
    service = make_service(tmp_path, receiver, max_age_seconds=0.05, realtime_senders=0)
    service.accept("s1", event(1))
    time.sleep(0.1)
    sender = threading.Thread(target=service._send_realtime, daemon=True)
    sender.start()
    try:
        assert wait_for(lambda: service.stats().dropped == 1)
        assert receiver.bodies == []
    finally:
        service.stop()


def test_reliable_survives_receiver_outage_in_order(tmp_path, receiver):
    receiver.down = True
    service = make_service(tmp_path, receiver, mode=DeliveryMode.RELIABLE)
    try:
        for n in range(5):
            service.accept("s1", event(n))
        assert wait_for(lambda: service.stats().spooled == 5)
        receiver.down = False
        assert wait_for(lambda: len(receiver.bodies) == 5, timeout=10)
        assert [b["body"]["received"]["n"] for b in receiver.bodies] == [0, 1, 2, 3, 4]
        assert service.stats().spooled == 0
    finally:
        service.stop()


def test_reliable_drops_permanently_rejected_results(tmp_path, receiver):
    receiver.status = 400
    service = make_service(tmp_path, receiver, mode=DeliveryMode.RELIABLE)
    try:
        service.accept("s1", event(1))
        assert wait_for(lambda: service.stats().failed == 1)
        assert service.stats().spooled == 0
    finally:
        service.stop()


def test_spool_survives_restart_and_evicts_oldest(tmp_path):
    spool = Spool(tmp_path / "s.sqlite3", max_bytes=25)
    assert spool.put("u", {}, b"0123456789") == 0
    assert spool.put("u", {}, b"abcdefghij") == 0
    assert spool.put("u", {}, b"KLMNOPQRST") == 1  # over 25 bytes: oldest evicted
    spool.close()
    reopened = Spool(tmp_path / "s.sqlite3", max_bytes=25)
    assert reopened.count == 2 and reopened.size_bytes == 20
    assert reopened.peek()[3] == b"abcdefghij"
    reopened.close()


def test_per_stream_routes_override_default(tmp_path, receiver):
    service = make_service(tmp_path, receiver)
    try:
        service.set_route("s2", Route("http://receiver/other", DeliveryMode.REALTIME))
        service.accept("s2", event(1))
        assert wait_for(lambda: receiver.bodies)
    finally:
        service.stop()


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


def test_encrypts_when_a_key_is_set(tmp_path, receiver):
    private, public = _keypair()
    service = make_service(tmp_path, receiver)
    try:
        service.set_key(ResultKey(kid="k1", public_key_pem=public))
        service.accept("s1", event(7))
        assert wait_for(lambda: receiver.bodies)
        sent = receiver.bodies[0]
        assert sent["headers"]["x-enc"] == "v1" and sent["body"]["meta"]["kid"] == "k1"
        payload, _ = decrypt_json(sent["body"], [private])
        assert payload == {"ok": True, "received": event(7)}
    finally:
        service.stop()


def test_require_encryption_refuses_plaintext(tmp_path, receiver):
    service = make_service(tmp_path, receiver, require_encryption=True)
    try:
        service.accept("s1", event(1))
        assert service.stats().dropped == 1
        time.sleep(0.2)
        assert receiver.bodies == []
    finally:
        service.stop()


def test_no_results_url_drops(tmp_path, receiver):
    service = DeliveryService(results_queue=queue.Queue(), spool_path=tmp_path / "x.sqlite3",
                              transport=httpx.MockTransport(receiver.handler))
    service.accept("s1", event(1))
    assert service.stats().dropped == 1
    service.spool.close()
