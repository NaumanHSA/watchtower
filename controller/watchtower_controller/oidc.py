"""OpenID Connect: validate JWT access tokens issued by your identity provider.

Keys come from the provider's JWKS (found through OpenID discovery unless
OIDC_JWKS_URL is set) and are cached; an unknown key id triggers one refetch per
minute at most, which picks up key rotation. Roles come from a claim (dot path,
e.g. `realm_access.roles` for Keycloak), optionally renamed with OIDC_ROLE_MAP.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import httpx
import jwt

from watchtower_contracts import Role

from .auth import ROLE_RANK, AuthError, AuthUnavailable, Principal, fingerprint

log = logging.getLogger(__name__)

JWKS_TTL_SECONDS = 600
REFETCH_MIN_SECONDS = 60
CLOCK_SKEW_SECONDS = 30


def parse_role_map(spec: str) -> dict[str, Role]:
    """"idp-role:watchtower-role,..." -> {idp-role: Role}"""
    mapping: dict[str, Role] = {}
    for entry in filter(None, (e.strip() for e in spec.split(","))):
        source, sep, target = entry.rpartition(":")
        if not sep or not source:
            raise ValueError(f"OIDC_ROLE_MAP entries look like idp-role:watchtower-role, got {entry!r}")
        mapping[source.strip()] = Role(target.strip().lower())
    return mapping


def claim_values(claims: dict[str, Any], path: str) -> list[str]:
    node: Any = claims
    if path in claims:  # claim names may themselves contain dots (Auth0 namespaced claims)
        node = claims[path]
    else:
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
    if isinstance(node, str):
        return node.split()  # a space-separated string, like `scope`
    if isinstance(node, list):
        return [str(v) for v in node]
    return []


class OidcProvider:
    def __init__(self, *, issuer: str, audience: str | None, jwks_url: str | None, role_claim: str,
                 role_map: dict[str, Role], subject_claims: list[str], algorithms: list[str],
                 key_pem: str | None, timeout: float, transport: httpx.AsyncBaseTransport | None = None):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url
        self.role_claim = role_claim
        self.role_map = role_map
        self.subject_claims = subject_claims
        self.algorithms = algorithms
        self.key_pem = key_pem
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None  # monotonic time of the last fetch; None = never
        self._lock = asyncio.Lock()
        if not audience:
            log.warning("OIDC_AUDIENCE is not set: any token from %s with a Watchtower role is accepted, "
                        "whatever application it was issued for", self.issuer)

    async def verify(self, token: str) -> Principal:
        if token.count(".") != 2:
            raise AuthError("not a JWT")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError(f"malformed token: {e}") from None
        key = await self._key(header.get("kid"))
        try:
            claims = jwt.decode(
                token, key, algorithms=self.algorithms, issuer=self.issuer, audience=self.audience,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iss"], "verify_aud": self.audience is not None},
            )
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid token: {e}") from None
        role = self._role(claims)
        subject = next((str(claims[c]) for c in self.subject_claims if claims.get(c)), str(claims.get("sub", "?")))
        return Principal(subject=subject, role=role, token_fingerprint=fingerprint(token), public_key_pem=self.key_pem)

    def _role(self, claims: dict[str, Any]) -> Role:
        roles = []
        for value in claim_values(claims, self.role_claim):
            if value in self.role_map:
                roles.append(self.role_map[value])
            elif not self.role_map:
                try:
                    roles.append(Role(value.lower()))
                except ValueError:
                    pass
        if not roles:
            raise AuthError(f"token has no Watchtower role in claim {self.role_claim!r}")
        human = [r for r in roles if r != Role.WORKER]
        return max(human, key=ROLE_RANK.__getitem__) if human else Role.WORKER

    def _keys_age(self) -> float:
        # Not "now - 0": the monotonic clock starts near zero at boot, so on a freshly started
        # machine that would look like a recent fetch and block loading the keys for a minute.
        return math.inf if self._fetched_at is None else time.monotonic() - self._fetched_at

    async def _key(self, kid: str | None) -> Any:
        fresh = self._keys_age() < JWKS_TTL_SECONDS
        if fresh and (kid in self._keys or (kid is None and len(self._keys) == 1)):
            return self._keys[kid] if kid else next(iter(self._keys.values()))
        async with self._lock:
            # Unknown kid: refetch (key rotation), but never more than once a minute.
            if not fresh or self._keys_age() >= REFETCH_MIN_SECONDS:
                await self._fetch()
        if kid in self._keys:
            return self._keys[kid]
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise AuthError("token signed with an unknown key")

    async def _fetch(self) -> None:
        try:
            url = self.jwks_url
            if not url:
                discovery = await self._client.get(f"{self.issuer}/.well-known/openid-configuration")
                discovery.raise_for_status()
                url = discovery.json()["jwks_uri"]
            response = await self._client.get(url)
            response.raise_for_status()
            keys = {}
            for jwk in response.json().get("keys", []):
                if jwk.get("use", "sig") != "sig":
                    continue
                try:
                    keys[jwk.get("kid")] = jwt.PyJWK(jwk).key
                except jwt.PyJWTError:
                    continue  # an algorithm we don't support
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise AuthUnavailable(f"cannot load the identity provider's keys: {e}") from e
        self._keys = keys
        self._fetched_at = time.monotonic()
        log.info("loaded %d signing keys from %s", len(keys), url)

    async def aclose(self) -> None:
        await self._client.aclose()


class ChainProvider:
    """Tries providers in order; the first that accepts the token wins.

    Typical: `oidc,static` — people sign in through the identity provider, workers use
    long-lived worker tokens.
    """

    def __init__(self, providers: list):
        self.providers = providers

    async def verify(self, token: str) -> Principal:
        unavailable: AuthUnavailable | None = None
        last_error: AuthError | None = None
        for provider in self.providers:
            try:
                return await provider.verify(token)
            except AuthError as e:
                last_error = e
            except AuthUnavailable as e:
                unavailable = e
        if unavailable is not None:
            raise unavailable
        raise last_error or AuthError("invalid token")

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()
