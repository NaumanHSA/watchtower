"""Token verification. The controller never stores user tokens; it asks a provider.

Providers:
  http    a token server (see docs/token-server.md and token-server/ in this repo)
  static  tokens from configuration, "role:token" entries
  oidc    JWTs from an OpenID Connect identity provider (oidc.py)
  none    everything is admin (development only)
Several can be chained ("oidc,static"); see oidc.ChainProvider.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import ValidationError

from watchtower_contracts import Role, TokenVerification

from .settings import Settings

log = logging.getLogger(__name__)

MIN_STATIC_TOKEN_LENGTH = 24

# Higher rank includes everything a lower rank may do. Workers are outside the ladder.
ROLE_RANK = {Role.VIEWER: 1, Role.OPERATOR: 2, Role.ADMIN: 3, Role.WORKER: 0}


class AuthError(Exception):
    """The token is missing, unknown, revoked or lacks the required role."""


class AuthUnavailable(Exception):
    """The token provider could not be reached; the token's validity is unknown."""


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    token_fingerprint: str
    public_key_pem: str | None = None  # results encryption key that goes with this token
    kid: str | None = None

    def has_role(self, minimum: Role) -> bool:
        if minimum == Role.WORKER:
            return self.role in (Role.WORKER, Role.ADMIN)
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _parse_role(value: str | None) -> Role:
    if value is None:
        return Role.ADMIN  # original Watchtower token servers have no roles: one org, full access
    try:
        return Role(value.lower())
    except ValueError:
        raise AuthError(f"token server returned unknown role {value!r}") from None


class TokenProvider(Protocol):
    async def verify(self, token: str) -> Principal: ...

    async def aclose(self) -> None: ...


def _read_key_file(path: Path | None) -> str | None:
    return path.read_text(encoding="ascii") if path else None


class HttpTokenProvider:
    """Asks a token server. Answers are cached for `cache_seconds` (failures for 10s)."""

    NEGATIVE_CACHE_SECONDS = 10

    def __init__(self, url: str, cache_seconds: int, timeout: float, fallback_key_pem: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url = url
        self.cache_seconds = cache_seconds
        self.fallback_key_pem = fallback_key_pem
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cache: dict[str, tuple[float, Principal | AuthError]] = {}

    async def verify(self, token: str) -> Principal:
        fp = fingerprint(token)
        cached = self._cache.get(fp)
        if cached and cached[0] > time.monotonic():
            if isinstance(cached[1], AuthError):
                raise cached[1]
            return cached[1]
        try:
            result: Principal | AuthError = await self._ask(token, fp)
        except AuthUnavailable:
            # Keep serving a recent positive answer through a short provider outage.
            if cached and not isinstance(cached[1], AuthError) and cached[0] + self.cache_seconds > time.monotonic():
                return cached[1]
            raise
        ttl = self.cache_seconds if isinstance(result, Principal) else self.NEGATIVE_CACHE_SECONDS
        self._cache[fp] = (time.monotonic() + ttl, result)
        if len(self._cache) > 10_000:
            self._cache.clear()
        if isinstance(result, AuthError):
            raise result
        return result

    async def _ask(self, token: str, fp: str) -> Principal | AuthError:
        headers = {"Authorization": f"Bearer {token}", "watchtower-token": token}
        try:
            response = await self._client.get(self.url, headers=headers)
        except httpx.HTTPError as e:
            raise AuthUnavailable(f"token server unreachable: {e}") from e
        if response.status_code >= 500:
            raise AuthUnavailable(f"token server returned HTTP {response.status_code}")
        if response.status_code >= 400:
            return AuthError("invalid token")
        try:
            body = TokenVerification.model_validate(response.json())
        except (ValueError, ValidationError) as e:
            raise AuthUnavailable(f"token server returned an unreadable answer: {e}") from e
        if not body.isSuccess:
            return AuthError(body.message or "invalid token")
        try:
            role = _parse_role(body.role)
        except AuthError as e:
            return e
        return Principal(
            subject=body.subject or f"token:{fp}",
            role=role,
            token_fingerprint=fp,
            public_key_pem=body.pub_cov or self.fallback_key_pem,
            kid=body.kid,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class StaticTokenProvider:
    def __init__(self, entries: str, file: Path | None, key_pem: str | None):
        self.key_pem = key_pem
        self._tokens: list[tuple[str, Role, str]] = []  # (token, role, label)
        lines = [e for e in entries.split(",")]
        if file:
            lines += file.read_text(encoding="utf-8").splitlines()
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            role_name, sep, token = line.partition(":")
            if not sep or not token:
                raise ValueError("static tokens must look like role:token")
            role = Role(role_name.strip().lower())
            self._tokens.append((token.strip(), role, f"static-{len(self._tokens) + 1}"))
            if len(token.strip()) < MIN_STATIC_TOKEN_LENGTH:
                log.warning("a static %s token is shorter than %d characters; use long random tokens "
                            "(openssl rand -base64 32)", role.value, MIN_STATIC_TOKEN_LENGTH)
        if not self._tokens:
            raise ValueError("no static tokens configured")

    async def verify(self, token: str) -> Principal:
        match = None
        for known, role, label in self._tokens:  # compare against all to keep timing flat
            if hmac.compare_digest(known.encode(), token.encode()):
                match = (role, label)
        if match is None:
            raise AuthError("invalid token")
        return Principal(subject=match[1], role=match[0], token_fingerprint=fingerprint(token),
                         public_key_pem=self.key_pem)

    async def aclose(self) -> None:
        return None


class NoAuthProvider:
    def __init__(self, key_pem: str | None):
        self.key_pem = key_pem

    async def verify(self, token: str) -> Principal:
        return Principal(subject="anonymous", role=Role.ADMIN, token_fingerprint=fingerprint(token or "-"),
                         public_key_pem=self.key_pem)

    async def aclose(self) -> None:
        return None


def build_provider(settings: Settings) -> TokenProvider:
    from .oidc import ChainProvider, OidcProvider, parse_role_map

    key = _read_key_file(settings.result_public_key_file)
    providers: list[TokenProvider] = []
    for name in settings.providers:
        if name == "http":
            assert settings.token_server_url
            providers.append(HttpTokenProvider(settings.token_server_url, settings.token_cache_seconds,
                                               settings.request_timeout_seconds, fallback_key_pem=key))
        elif name == "static":
            providers.append(StaticTokenProvider(settings.static_tokens, settings.static_tokens_file, key))
        elif name == "oidc":
            assert settings.oidc_issuer
            providers.append(OidcProvider(
                issuer=settings.oidc_issuer, audience=settings.oidc_audience, jwks_url=settings.oidc_jwks_url,
                role_claim=settings.oidc_role_claim, role_map=parse_role_map(settings.oidc_role_map),
                subject_claims=[c.strip() for c in settings.oidc_subject_claims.split(",") if c.strip()],
                algorithms=[a.strip() for a in settings.oidc_algorithms.split(",") if a.strip()],
                key_pem=key, timeout=settings.request_timeout_seconds,
            ))
        else:
            log.warning("AUTH_PROVIDER=none: every request is treated as admin. Never use this outside development.")
            providers.append(NoAuthProvider(key))
    return providers[0] if len(providers) == 1 else ChainProvider(providers)
