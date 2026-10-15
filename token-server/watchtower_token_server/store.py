"""Tokens and result-encryption keys, persisted as one JSON file.

Tokens are stored only as SHA-256 hashes; the value is shown once, when issued.
The results keypair is organisation-wide: every worker token hands out the current
public key, and older keys are kept so results encrypted before a rotation still decrypt.
Private keys can be encrypted at rest with a Fernet master key (TOKEN_SERVER_MASTER_KEY).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from watchtower_contracts import Role

TOKEN_PREFIX = "wt_"  # noqa: S105 - makes leaked tokens easy for secret scanners to spot
RSA_KEY_BITS = 2048


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class TokenRecord:
    id: str
    token_hash: str
    role: str
    subject: str
    created_at: str
    expires_at: str | None = None
    revoked: bool = False

    @property
    def active(self) -> bool:
        if self.revoked:
            return False
        return self.expires_at is None or datetime.fromisoformat(self.expires_at) > datetime.now(UTC)


@dataclass
class KeyRecord:
    kid: str
    created_at: str
    public_pem: str
    private_pem: str  # Fernet token when the store has a master key, else PEM


@dataclass
class StoreData:
    tokens: dict[str, TokenRecord] = field(default_factory=dict)
    keys: list[KeyRecord] = field(default_factory=list)  # newest first


class Store:
    def __init__(self, path: Path, master_key: str | None = None):
        self.path = path
        self._fernet = Fernet(master_key.encode()) if master_key else None
        self._lock = threading.RLock()
        self._signature: tuple[int, int] | None = None  # (mtime_ns, size) of the file we loaded
        self._data = StoreData()
        self._load()

    # ---- persistence: reload when another process (the CLI) changed the file ----

    def _load(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._data, self._signature = StoreData(), None
            return
        if self._signature == (stat.st_mtime_ns, stat.st_size):
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._data = StoreData(
            tokens={k: TokenRecord(**v) for k, v in raw.get("tokens", {}).items()},
            keys=[KeyRecord(**k) for k in raw.get("keys", [])],
        )
        self._signature = (stat.st_mtime_ns, stat.st_size)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"tokens": {k: asdict(v) for k, v in self._data.tokens.items()},
                   "keys": [asdict(k) for k in self._data.keys]}
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)  # atomic: a reader never sees half a file
        stat = self.path.stat()
        self._signature = (stat.st_mtime_ns, stat.st_size)

    # ---- tokens ----

    def issue(self, role: Role, subject: str, *, value: str | None = None,
              expires_in_days: int | None = None) -> tuple[TokenRecord, str]:
        with self._lock:
            self._load()
            value = value or TOKEN_PREFIX + secrets.token_urlsafe(32)
            digest = hash_token(value)
            if any(t.token_hash == digest for t in self._data.tokens.values()):
                raise ValueError("this token already exists")
            expires = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat(timespec="seconds") \
                if expires_in_days else None
            record = TokenRecord(id=digest[:12], token_hash=digest, role=Role(role).value, subject=subject,
                                 created_at=now_iso(), expires_at=expires)
            self._data.tokens[record.id] = record
            if not self._data.keys:
                self._data.keys.insert(0, self._new_key())
            self._save()
            return record, value

    def lookup(self, token: str) -> TokenRecord | None:
        with self._lock:
            self._load()
            record = self._data.tokens.get(hash_token(token)[:12])
            if record and secrets.compare_digest(record.token_hash, hash_token(token)):
                return record
            return None

    def tokens(self) -> list[TokenRecord]:
        with self._lock:
            self._load()
            return list(self._data.tokens.values())

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            self._load()
            record = self._data.tokens.get(token_id)
            if record is None:
                return False
            record.revoked = True
            self._save()
            return True

    # ---- keys ----

    def _new_key(self) -> KeyRecord:
        private = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_BITS)
        private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()).decode("ascii")
        public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        stored = self._fernet.encrypt(private_pem.encode()).decode() if self._fernet else private_pem
        return KeyRecord(kid=uuid.uuid4().hex[:12], created_at=now_iso(), public_pem=public_pem, private_pem=stored)

    def rotate_key(self) -> KeyRecord:
        with self._lock:
            self._load()
            key = self._new_key()
            self._data.keys.insert(0, key)
            self._save()
            return key

    def current_key(self) -> KeyRecord | None:
        with self._lock:
            self._load()
            return self._data.keys[0] if self._data.keys else None

    def keys(self) -> list[KeyRecord]:
        with self._lock:
            self._load()
            return list(self._data.keys)

    def private_pem(self, key: KeyRecord) -> str:
        if key.private_pem.startswith("-----BEGIN"):
            return key.private_pem
        if self._fernet is None:
            raise RuntimeError("private keys are encrypted; set TOKEN_SERVER_MASTER_KEY")
        return self._fernet.decrypt(key.private_pem.encode()).decode("ascii")

    def private_pems(self, kid: str | None = None) -> list[str]:
        """Private keys to try for an envelope: the one with `kid` first, then the rest."""
        keys = self.keys()
        keys.sort(key=lambda k: k.kid != kid)
        return [self.private_pem(k) for k in keys]
