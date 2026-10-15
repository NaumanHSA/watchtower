# Watchtower token server

The reference implementation of the [token-server contract](../docs/token-server.md).
The controller asks a token server two things about every token it sees: *is it valid,
and what may it do?* For worker tokens the answer also carries the public key that
workers encrypt detection results with.

Use this server as-is, or replace it with your own (an existing identity service, an API
gateway, a few lines in your own backend) that answers the same endpoint.

What it does:

- issues tokens with a role (`admin`, `operator`, `viewer`, `worker`) and an optional
  expiry, and stores only their SHA-256 hashes;
- answers `GET /v1/token/verify` (plus the original Watchtower paths, so older
  controllers work too);
- keeps one organisation-wide results keypair, rotatable; older keys keep decrypting
  results that were already in flight;
- optionally receives results itself (`/v1/ingest`): decrypts them, logs them, saves the
  images, and forwards the plaintext to your application.

## Run it

```bash
uv run watchtower-token-server            # from the repo root; listens on 127.0.0.1:9000
```

On first start it issues an admin token and a worker token, prints them once, and saves
them to `data/bootstrap-tokens.txt`. Or with Docker:

```bash
docker build -f token-server/Dockerfile -t watchtower-token-server .
docker run -p 9000:9000 -v wt-tokens:/data watchtower-token-server
```

## Manage tokens

```bash
watchtower-token-server issue --role worker --subject edge-site-1
watchtower-token-server issue --role viewer --subject alice --expires-days 90
watchtower-token-server list
watchtower-token-server revoke <id>
watchtower-token-server rotate-key           # new results keypair
watchtower-token-server export-key [kid]     # private key for your own results receiver
```

The running server notices CLI changes immediately. Controllers cache answers for
`TOKEN_CACHE_SECONDS` (60 by default), so a revoked token stops working within a minute.

With `TOKEN_SERVER_ADMIN_KEY` set, the same operations are available over HTTP under
`/v1/admin/*` (send `Authorization: Bearer <admin key>`); see `/docs` for the schema.

## Configuration

All settings are environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `TOKEN_SERVER_HOST` / `TOKEN_SERVER_PORT` | `127.0.0.1` / `9000` | Listen address. Use `0.0.0.0` in containers. |
| `TOKEN_SERVER_DATA_DIR` | `data` | Where `store.json` lives. Mount a volume here. |
| `TOKEN_SERVER_ADMIN_KEY` | unset | Enables the admin HTTP API. |
| `TOKEN_SERVER_MASTER_KEY` | unset | Fernet key that encrypts private keys at rest. Generate with `gen-master-key`. |
| `TOKEN_SERVER_BOOTSTRAP` | `true` | Issue an admin and a worker token on an empty store. |
| `TOKEN_SERVER_BOOTSTRAP_TOKENS` | unset | `role:token[:subject],...` issued at start if missing. Handy for Compose/Kubernetes secrets. |
| `TOKEN_SERVER_INGEST_ENABLED` | `false` | Also act as a results receiver at `/v1/ingest`. |
| `TOKEN_SERVER_FORWARD_URL` | unset | Forward decrypted results to this URL. If forwarding fails the worker gets a 502, so `reliable` delivery retries. |
| `TOKEN_SERVER_SAVE_IMAGES_DIR` | unset | Save received photos and face crops here. |

## Security notes

- Token values are shown once and never stored; losing one means issuing a new one.
- `store.json` holds the private results keys. It's written with mode 0600; set
  `TOKEN_SERVER_MASTER_KEY` to encrypt the keys inside it as well.
- The verify endpoint tells callers whether a token is valid, so don't expose the token
  server more widely than your controllers and receivers need. Put TLS in front of it
  when it's reachable over a network.
