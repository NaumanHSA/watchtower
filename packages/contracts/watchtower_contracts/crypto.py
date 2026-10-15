"""Result envelope: RSA-OAEP(SHA-256) wraps a random AES-256 key, AES-GCM encrypts the JSON.

    {"alg": "RSA-OAEP-256 + AES-256-GCM", "ek": b64, "n": b64, "c": b64, "t": b64,
     "meta": {"v": 1, "kid": "<key id or null>"}}

`ek` is the wrapped key, `n` the 12-byte nonce, `c` the ciphertext, `t` the 16-byte tag.
Requires the `crypto` extra.
"""
from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALGORITHM = "RSA-OAEP-256 + AES-256-GCM"
ENVELOPE_HEADER = "X-Enc"  # sent with value "v1" alongside encrypted bodies
_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


@lru_cache(maxsize=64)
def _public_key(pem: str) -> RSAPublicKey:
    key = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(key, RSAPublicKey):
        raise ValueError("result key must be an RSA public key")
    return key


@lru_cache(maxsize=64)
def _private_key(pem: str) -> RSAPrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("expected an RSA private key")
    return key


def is_envelope(body: Any) -> bool:
    return isinstance(body, dict) and {"ek", "n", "c", "t"} <= body.keys()


def encrypt_json(payload: Any, public_key_pem: str, kid: str | None = None) -> dict[str, Any]:
    plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    data_key = os.urandom(32)
    nonce = os.urandom(12)
    sealed = AESGCM(data_key).encrypt(nonce, plaintext, None)  # ciphertext || 16-byte tag
    return {
        "alg": ALGORITHM,
        "ek": _b64e(_public_key(public_key_pem).encrypt(data_key, _OAEP)),
        "n": _b64e(nonce),
        "c": _b64e(sealed[:-16]),
        "t": _b64e(sealed[-16:]),
        "meta": {"v": 1, "kid": kid},
    }


def decrypt_json(envelope: dict[str, Any], private_key_pems: Iterable[str]) -> tuple[Any, int]:
    """Decrypt with the first key that unwraps the data key.

    Returns (payload, index of the key used). Raises ValueError when no key fits and
    cryptography's InvalidTag when the ciphertext was tampered with.
    """
    wrapped = _b64d(envelope["ek"])
    for index, pem in enumerate(private_key_pems):
        try:
            data_key = _private_key(pem).decrypt(wrapped, _OAEP)
        except ValueError:
            continue
        plaintext = AESGCM(data_key).decrypt(_b64d(envelope["n"]), _b64d(envelope["c"]) + _b64d(envelope["t"]), None)
        return json.loads(plaintext), index
    raise ValueError("none of the given keys can unwrap this envelope")
