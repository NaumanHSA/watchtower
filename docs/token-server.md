# Token servers

A *token server* answers one question for the controller: **is this token valid, and what may it do?** Watchtower
ships with one that you can use as it is, but any service that answers the same simple request works, so you can
plug Watchtower into the accounts and API keys you already have.

This page is the reference: the exact request and answer, an example of writing your own, and the commands of the
bundled server. For the bigger picture of signing in, read [Users and sign-in](access.md) first.

## How the controller uses it

With `AUTH_PROVIDER=http`, whenever a request arrives with a token the controller hasn't seen in the last minute,
it calls the address in `TOKEN_SERVER_URL` and passes the token along. It remembers the answer for
`TOKEN_CACHE_SECONDS` (60 by default) so that it doesn't ask again for every request. The controller never stores
tokens itself.

## The request

```http
GET <TOKEN_SERVER_URL>
Authorization: Bearer <token>
watchtower-token: <token>
```

The token is sent twice: in the standard `Authorization` header and, for token servers written for Watchtower 1.x,
in a `watchtower-token` header. Read whichever suits you.

## The answer

Answer `200` with a JSON body:

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
| `isSuccess` | yes | `true` if the token is valid. |
| `role` | no | `admin`, `operator`, `viewer` or `worker`. If missing, the token is treated as `admin`, which is how the original Watchtower behaved; always send it in new token servers. |
| `subject` | no | Who the token belongs to. Shown in the dashboard and written to the audit log. |
| `pub_cov` | no | For worker tokens: a PEM RSA public key that the worker encrypts results with. Leave it out and results are sent unencrypted, unless the worker has `REQUIRE_ENCRYPTION=true`. |
| `kid` | no | A name for that key. It travels with every encrypted result, so your receiver knows which private key to use. |
| `message` | no | A reason, when `isSuccess` is `false`. |

How the controller reads your answer:

- **Valid:** a `2xx` status with `"isSuccess": true`.
- **Invalid:** a `2xx` status with `"isSuccess": false`, or any `4xx` status. The caller gets `401`.
- **Unknown:** a `5xx` status, a timeout, or a body that isn't JSON. The controller can't tell, so API callers get
  `503` and should try again. Workers are treated more gently: they keep syncing with the key they already have,
  so a token server outage never stops the fleet. A positive answer up to twice `TOKEN_CACHE_SECONDS` old is still
  honoured during an outage.

## Writing your own

Anything that can answer one `GET` request works: a route in your existing backend, a serverless function, an API
gateway in front of your identity system. A complete example in Python that accepts API tokens your application
already issues:

```python
from fastapi import FastAPI, Header

app = FastAPI()
RESULTS_PUBLIC_KEY = open("results-public.pem").read()


@app.get("/watchtower/verify")
def verify(authorization: str = Header("")):
    user = my_app.lookup_api_token(authorization.removeprefix("Bearer "))  # your code
    if user is None or not user.active:
        return {"isSuccess": False}
    if user.is_service_account:
        role = "worker"
    elif user.is_staff:
        role = "operator"
    else:
        role = "viewer"
    return {
        "isSuccess": True,
        "role": role,
        "subject": user.email,
        "pub_cov": RESULTS_PUBLIC_KEY if role == "worker" else None,
        "kid": "2026-10",
    }
```

Then, on the controller:

```bash
AUTH_PROVIDER=http
TOKEN_SERVER_URL=https://your-app.example.com/watchtower/verify
```

Create the key pair for results with OpenSSL, give the public half to your token service and the private half to
your results receiver:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out results-private.pem
openssl pkey -in results-private.pem -pubout -out results-public.pem
```

Keep the endpoint reachable only by your controllers: it tells whoever calls it whether a token is valid.

## The bundled token server

The token server in [`token-server/`](../token-server/README.md) implements the contract above and adds what you
need to run it:

- tokens with a role, a subject and an optional expiry date, of which only a SHA-256 hash is stored;
- one key pair for results, shared by all workers, that you can rotate; older keys keep decrypting results that
  were already on their way;
- an optional results receiver that decrypts results, saves their pictures and forwards them to your application;
- a command line and an HTTP admin API.

It answers at `/v1/token/verify`, and also at the addresses older Watchtower versions used.

### Commands

Run them inside the token server's container, for example
`docker compose exec token-server watchtower-token-server <command>`:

| Command | What it does |
|---|---|
| `issue --role <role> --subject <name> [--expires-days N]` | Create a token. It is printed once. |
| `issue ... --token <value>` | Register a token value you chose yourself, for example one kept in a secrets manager. |
| `list` | Show all tokens: id, role, subject, creation and expiry dates. Values are never shown again. |
| `revoke <id>` | Revoke a token. It stops working within `TOKEN_CACHE_SECONDS`. |
| `rotate-key` | Create a new results key pair. Workers receive the new public key within a minute. |
| `export-key [kid]` | Print a private key (the current one by default), for your results receiver. |
| `gen-master-key` | Print a new value for `TOKEN_SERVER_MASTER_KEY`. |
| `serve` | Run the server. This is what the container does by default. |

A running server notices changes made with these commands immediately.

### The admin API

With `TOKEN_SERVER_ADMIN_KEY` set, the same operations are available over HTTP under `/v1/admin/`, with
`Authorization: Bearer <admin key>`. The token server's `/docs` page describes every call.

### The results receiver

With `TOKEN_SERVER_INGEST_ENABLED=true` the token server also accepts results at `/v1/ingest`: it decrypts them with
the right key, writes a line per result to its log, saves the pictures if `TOKEN_SERVER_SAVE_IMAGES_DIR` is set,
and forwards the plain JSON to `TOKEN_SERVER_FORWARD_URL` if that is set. When forwarding fails it answers the
worker with `502`, so workers in reliable mode keep the result and try again. See
[Receiving results](results.md#the-easy-way-let-the-token-server-decrypt-and-forward).

All of its settings are listed in the [configuration reference](configuration.md#token-server).
