from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from controller_helpers import MONGO_URI, make_settings, requires_mongo
from cryptography.hazmat.primitives.asymmetric import rsa
from pymongo import MongoClient

from watchtower_contracts import Role
from watchtower_controller.app import create_app
from watchtower_controller.auth import AuthError, AuthUnavailable, StaticTokenProvider
from watchtower_controller.oidc import ChainProvider, OidcProvider, claim_values, parse_role_map

ISSUER = "https://idp.example/realms/wt"


class FakeIdP:
    def __init__(self):
        self.keys = {"k1": rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.jwks_calls = 0
        self.down = False

    def jwk(self, kid: str) -> dict:
        data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.keys[kid].public_key()))
        return {**data, "kid": kid, "use": "sig", "alg": "RS256"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("idp down")
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/certs"})
        if request.url.path.endswith("/certs"):
            self.jwks_calls += 1
            return httpx.Response(200, json={"keys": [self.jwk(k) for k in self.keys]})
        return httpx.Response(404)

    def token(self, kid: str = "k1", **claims) -> str:
        now = int(time.time())
        body = {"iss": ISSUER, "aud": "watchtower", "sub": "u-1", "iat": now, "exp": now + 300,
                "preferred_username": "alice", "realm_access": {"roles": ["wt-operator", "offline_access"]}}
        body.update(claims)
        return jwt.encode(body, self.keys[kid], algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


def provider_for(idp: FakeIdP, **overrides) -> OidcProvider:
    options = dict(issuer=ISSUER, audience="watchtower", jwks_url=None, role_claim="realm_access.roles",
                   role_map=parse_role_map("wt-admin:admin,wt-operator:operator,wt-viewer:viewer"),
                   subject_claims=["preferred_username", "sub"], algorithms=["RS256"], key_pem=None, timeout=2,
                   transport=httpx.MockTransport(idp.handler))
    options.update(overrides)
    return OidcProvider(**options)


async def test_valid_token_maps_keycloak_roles(idp):
    provider = provider_for(idp)
    principal = await provider.verify(idp.token())
    assert principal.role == Role.OPERATOR and principal.subject == "alice"
    principal = await provider.verify(idp.token(realm_access={"roles": ["wt-viewer", "wt-admin"]}))
    assert principal.role == Role.ADMIN  # the highest mapped role wins
    assert idp.jwks_calls == 1  # keys are cached


@pytest.mark.parametrize("claims", [
    {"exp": int(time.time()) - 3600},
    {"aud": "some-other-app"},
    {"iss": "https://evil.example"},
    {"realm_access": {"roles": ["offline_access"]}},
])
async def test_rejected_tokens(idp, claims):
    with pytest.raises(AuthError):
        await provider_for(idp).verify(idp.token(**claims))


async def test_garbage_and_unsigned_tokens_are_rejected(idp):
    provider = provider_for(idp)
    for token in ("not-a-jwt", "a.b.c"):
        with pytest.raises(AuthError):
            await provider.verify(token)
    unsigned = jwt.encode({"iss": ISSUER, "aud": "watchtower", "exp": int(time.time()) + 60,
                           "realm_access": {"roles": ["wt-admin"]}}, None, algorithm="none")
    with pytest.raises(AuthError):
        await provider.verify(unsigned)


async def test_key_rotation_refetches_at_most_once_a_minute(idp):
    provider = provider_for(idp)
    await provider.verify(idp.token())
    idp.keys["k2"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider._fetched_at -= 120  # pretend the last fetch was two minutes ago
    assert (await provider.verify(idp.token(kid="k2"))).role == Role.OPERATOR
    calls = idp.jwks_calls
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"iss": ISSUER, "aud": "watchtower", "exp": int(time.time()) + 60}, stranger,
                        algorithm="RS256", headers={"kid": "k9"})
    for _ in range(3):
        with pytest.raises(AuthError):
            await provider.verify(forged)
    assert idp.jwks_calls == calls  # unknown kids can't make us hammer the IdP


async def test_idp_down_is_unavailable_not_invalid(idp):
    idp.down = True
    with pytest.raises(AuthUnavailable):
        await provider_for(idp).verify(idp.token())


def test_claim_paths():
    claims = {"https://example.com/roles": ["admin"], "a": {"b": ["x", "y"]}, "scope": "openid viewer"}
    assert claim_values(claims, "https://example.com/roles") == ["admin"]
    assert claim_values(claims, "a.b") == ["x", "y"]
    assert claim_values(claims, "scope") == ["openid", "viewer"]
    assert claim_values(claims, "missing.path") == []
    with pytest.raises(ValueError):
        parse_role_map("no-separator")


async def test_unmapped_roles_use_watchtower_names(idp):
    provider = provider_for(idp, role_claim="roles", role_map={})
    principal = await provider.verify(idp.token(roles=["viewer", "something-else"]))
    assert principal.role == Role.VIEWER


async def test_chain_people_via_oidc_workers_via_static(idp):
    chain = ChainProvider([provider_for(idp), StaticTokenProvider("worker:w" * 1 + "-" * 30, None, "PEM")])
    worker_token = "w" + "-" * 30
    assert (await chain.verify(worker_token)).role == Role.WORKER
    assert (await chain.verify(idp.token())).role == Role.OPERATOR
    with pytest.raises(AuthError):
        await chain.verify("nobody")
    idp.down = True
    fresh = ChainProvider([provider_for(idp), StaticTokenProvider("worker:" + worker_token, None, None)])
    with pytest.raises(AuthUnavailable):  # can't tell: the IdP is down
        await fresh.verify(idp.token())
    assert (await fresh.verify(worker_token)).role == Role.WORKER  # static still works


def test_settings_for_chains():
    assert make_settings(auth_provider="oidc,static", oidc_issuer=ISSUER).providers == ["oidc", "static"]
    with pytest.raises(ValueError):
        make_settings(auth_provider="oidc")  # needs OIDC_ISSUER
    with pytest.raises(ValueError):
        make_settings(auth_provider="none,static", allow_no_auth=True)
    with pytest.raises(ValueError):
        make_settings(auth_provider="ldap")


@requires_mongo
async def test_auth_config_endpoint():
    settings = make_settings(auth_provider="oidc,static", oidc_issuer=ISSUER, oidc_client_id="dashboard")
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                body = (await c.get("/api/v1/auth/config")).json()
        assert body == {"providers": ["oidc", "static"],
                        "oidc": {"issuer": ISSUER, "client_id": "dashboard", "scope": "openid profile email"}}
    finally:
        MongoClient(MONGO_URI).drop_database(settings.mongo_db)
