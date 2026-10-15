# Token servers

The controller never stores user tokens. For every token it sees, it asks a **token
server** one question, and caches the answer for `TOKEN_CACHE_SECONDS` (60 by default).
You can run the bundled one ([token-server/](../token-server/README.md)), or answer the
question from a service you already have.

## The contract

```
GET <TOKEN_SERVER_URL>
Authorization: Bearer <token>
watchtower-token: <token>        (same value; sent for older token servers)
```

Answer `200` with JSON:

```json
{
  "isSuccess": true,
  "role": "operator",
  "subject": "alice@example.com",
  "pub_cov": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
  "kid": "2026-10"
}
```

| Field | Required | Meaning |
|---|---|---|
| `isSuccess` | yes | `false` (or any 4xx status) means the token is invalid. |
| `role` | no | `admin`, `operator`, `viewer` or `worker`. Missing means `admin`, which is how the original Watchtower platform behaved. |
| `subject` | no | Who the token belongs to; shown in the dashboard and logs. |
| `pub_cov` | no | PEM RSA public key. For worker tokens: workers encrypt results with it. Without it, results are sent in plaintext unless the controller has `RESULT_PUBLIC_KEY_FILE`, or the worker refuses (`REQUIRE_ENCRYPTION=true`). |
| `kid` | no | Identifier of that key; travels in each encrypted result so receivers know which private key to use. |
| `message` | no | Reason, when `isSuccess` is false. |

How the controller reads your answer:

- `2xx` with `isSuccess: true` → valid.
- `2xx` with `isSuccess: false`, or any `4xx` → invalid (`401` to the caller).
- `5xx`, a timeout or an unreadable body → *unknown*: API callers get `503`, while
  workers keep syncing with the key they already have, so a token-server outage never
  stops the fleet. A positive answer up to twice `TOKEN_CACHE_SECONDS` old is still
  honoured during an outage.

Roles:

| Role | May |
|---|---|
| `viewer` | Read cameras, workers, stats, live events; watch live video |
| `operator` | Everything a viewer may, plus add/edit/start/stop/delete cameras and drain workers |
| `admin` | Everything, plus forget workers; may also register workers |
| `worker` | Only register and run a worker. Cannot use the dashboard or API. |

## Writing your own

Anything that can answer one GET request works: a route in your existing backend, a
serverless function, an API gateway in front of your identity provider. A minimal
example that accepts tokens your app already issues:

```python
from fastapi import FastAPI, Header

app = FastAPI()
RESULTS_PUBLIC_KEY = open("results-public.pem").read()

@app.get("/watchtower/verify")
def verify(authorization: str = Header("")):
    user = my_app.lookup_api_token(authorization.removeprefix("Bearer "))  # your code
    if user is None or not user.active:
        return {"isSuccess": False}
    role = "worker" if user.is_service_account else ("operator" if user.is_staff else "viewer")
    return {"isSuccess": True, "role": role, "subject": user.email,
            "pub_cov": RESULTS_PUBLIC_KEY if role == "worker" else None, "kid": "2026-10"}
```

Then set on the controller:

```
AUTH_PROVIDER=http
TOKEN_SERVER_URL=https://your-app.example.com/watchtower/verify
```

Keep the endpoint private to your controllers: it tells callers whether a token is valid.

## Single sign-on (OpenID Connect)

With `AUTH_PROVIDER=oidc`, the controller validates JWT access tokens from your identity
provider itself (signature against the provider's published keys, issuer, audience,
expiry), so people sign in with the accounts they already have. The dashboard shows a
**Sign in with SSO** button (authorization code + PKCE) when `OIDC_CLIENT_ID` is set.

Workers can't use short-lived JWTs from a static setting, so chain providers: the first
one that accepts a token wins.

```
AUTH_PROVIDER=oidc,static                  # people via SSO, workers via static tokens
STATIC_TOKENS=worker:<long random>
# or AUTH_PROVIDER=oidc,http with a token server for worker tokens

OIDC_ISSUER=https://idp.example.com/realms/watchtower
OIDC_AUDIENCE=watchtower                   # the `aud` your provider puts in access tokens
OIDC_CLIENT_ID=watchtower-dashboard        # a public client, redirect URI https://<controller>/ui/
OIDC_ROLE_CLAIM=realm_access.roles
OIDC_ROLE_MAP=wt-admin:admin,wt-operator:operator,wt-viewer:viewer
```

| Provider | `OIDC_ROLE_CLAIM` | Notes |
|---|---|---|
| Keycloak | `realm_access.roles` (realm roles) or `resource_access.<client>.roles` | Add an *audience* mapper so access tokens carry `OIDC_AUDIENCE`. |
| Microsoft Entra ID | `roles` (app roles) | Issuer `https://login.microsoftonline.com/<tenant>/v2.0`; audience is the API's client id. |
| Auth0 | your namespaced claim, e.g. `https://watchtower.example.com/roles` | Add the claim with an Action; audience is the API identifier. |
| Okta | `groups` | Add a groups claim to the authorization server. |

Without `OIDC_ROLE_MAP`, claim values must be the role names themselves (`admin`,
`operator`, `viewer`). A token with no matching role is refused; with several, the
highest wins. The provider's keys are cached for ten minutes and refetched (at most
once a minute) when a token names an unknown key, so key rotation needs no restart.
Results encryption with OIDC uses `RESULT_PUBLIC_KEY_FILE` (or the token server's key
when chained with `http`).

## Without a token server

For a small installation, the controller can read tokens from its own configuration:

```
AUTH_PROVIDER=static
STATIC_TOKENS=admin:<long random>,operator:<long random>,worker:<long random>
RESULT_PUBLIC_KEY_FILE=/run/secrets/results-public.pem   # optional
```

Generate tokens with `openssl rand -base64 32 | tr '+/' '-_'`. Changing them needs a
controller restart, and there is no per-user revocation; use a token server when that
matters.

`AUTH_PROVIDER=none` turns authentication off entirely. The controller refuses to start
with it unless `ALLOW_NO_AUTH=true` is also set. Never use it on a reachable host.
