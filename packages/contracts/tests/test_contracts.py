import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from watchtower_contracts import StreamConfig, SyncResponse, TokenVerification
from watchtower_contracts.crypto import decrypt_json, encrypt_json, is_envelope


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private, public


def test_envelope_round_trip_picks_matching_key():
    old_private, _ = _keypair()
    new_private, new_public = _keypair()
    envelope = encrypt_json({"ok": True, "n": 1}, new_public, kid="k2")
    assert is_envelope(envelope)
    assert envelope["meta"] == {"v": 1, "kid": "k2"}
    payload, index = decrypt_json(envelope, [old_private, new_private])
    assert payload == {"ok": True, "n": 1}
    assert index == 1


def test_envelope_rejects_wrong_key_and_tampering():
    private, public = _keypair()
    other_private, _ = _keypair()
    envelope = encrypt_json({"x": 1}, public)
    with pytest.raises(ValueError):
        decrypt_json(envelope, [other_private])
    envelope["c"] = envelope["c"][:-4] + ("AAAA" if not envelope["c"].endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidTag):
        decrypt_json(envelope, [private])


def test_stream_config_validation():
    assert StreamConfig().detector_resolution == 320
    with pytest.raises(ValidationError):
        StreamConfig(detector_resolution=480)
    with pytest.raises(ValidationError):
        StreamConfig(results_url="ftp://example.com/x")
    assert StreamConfig(results_url="https://example.com/in").results_url == "https://example.com/in"


def test_models_ignore_unknown_fields():
    # A newer controller may add fields; an older worker must still parse the response.
    resp = SyncResponse.model_validate({"sync_interval_ms": 5000, "lease_seconds": 60, "future_field": 1})
    assert resp.desired == []


def test_legacy_token_server_response_parses():
    legacy = {"isSuccess": True, "pub_cov": "-----BEGIN PUBLIC KEY-----..."}
    parsed = TokenVerification.model_validate(legacy)
    assert parsed.role is None and parsed.pub_cov
