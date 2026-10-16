# Users and sign-in

Watchtower doesn't keep user accounts of its own. Every request, from a person in the dashboard, a script or a
worker, carries a **token**, and the controller asks an identity service what that token is allowed to do. This
guide explains the roles, then shows the three ways to give people access: the bundled token server, your
company's single sign-on, or a fixed list of tokens.

## Roles

| Role | Can |
|---|---|
| **viewer** | See cameras, workers and statistics, follow live activity, and watch live video. |
| **operator** | Everything a viewer can, plus add, edit, start, stop and delete cameras, and drain workers. |
| **admin** | Everything, plus forget workers and read the audit log. |
| **worker** | Register and sync a worker. Nothing else: a worker token can't open the dashboard or use the API. |

Give people the lowest role that lets them do their job. Most people who only watch cameras need **viewer**.

## Choosing how people sign in

The controller's `AUTH_PROVIDER` setting decides where tokens are checked:

| Option | Best for | Setting |
|---|---|---|
| The bundled token server | Most installations; you issue and revoke tokens with one command | `AUTH_PROVIDER=http` |
| Single sign-on (OpenID Connect) | Organisations with Keycloak, Entra ID, Auth0, Okta or similar | `AUTH_PROVIDER=oidc`, usually chained: `oidc,http` or `oidc,static` |
| A fixed list of tokens | Very small or offline installations | `AUTH_PROVIDER=static` |
| Your own service | You already have API tokens or an identity backend | `AUTH_PROVIDER=http` pointing at your service |

You can **chain** providers with commas. The controller tries each in turn and the first one that accepts the
token wins. The most common chain is people through single sign-on and workers through the token server or static
tokens, because workers need long-lived tokens that SSO doesn't hand out.

## Using the bundled token server

The token server ships with Watchtower and runs next to the controller (the `token-server` profile of the control
plane Compose file). It stores only a hash of each token, so a token is shown once, when you create it.

All commands below run on the control plane machine, from `deploy/compose/controller`.

**Create a token for a person:**

```bash
docker compose exec token-server watchtower-token-server issue --role operator --subject alice@example.com
```

`--subject` is the name shown in the dashboard and written to the audit log, so use something that identifies the
person. Add `--expires-days 90` for a token that stops working after 90 days. Send the printed token to the person
over a private channel; they paste it on the sign-in page.

**Create a token for a worker:**

```bash
docker compose exec token-server watchtower-token-server issue --role worker --subject site-a
```

**List tokens** (values are never shown again, only their ids, roles, subjects and expiry dates):

```bash
docker compose exec token-server watchtower-token-server list
```

**Revoke a token** when someone leaves or a token may have leaked:

```bash
docker compose exec token-server watchtower-token-server revoke <id>
```

The controller remembers answers about a token for up to a minute (`TOKEN_CACHE_SECONDS`), so a revoked token stops
working within a minute. A revoked worker token makes that worker stop all its cameras at its next sync.

**The first admin token.** When the token server starts with an empty store, it creates an admin token and a
worker token, prints them once in its log and saves them to `bootstrap-tokens.txt` in its data volume. Read them
with `docker compose logs token-server`, sign in with the admin token, then create personal tokens and revoke the
bootstrap ones. Alternatively, set `TOKEN_SERVER_BOOTSTRAP_TOKENS` to tokens of your own choosing (useful with
secrets managers): `admin:<value>:<subject>,worker:<value>:<subject>`.

**Managing tokens over HTTP.** Set `TOKEN_SERVER_ADMIN_KEY` to a long random value and the same operations become
available under `/v1/admin/` on the token server (send `Authorization: Bearer <admin key>`). The schema is at the
token server's `/docs` page.

## Using single sign-on

With single sign-on, people use the accounts they already have, and the dashboard shows a **Sign in with SSO**
button. The controller checks each sign-in itself, against your identity provider's published keys; no token server
is involved for people.

### What you need from your identity provider

1. **An API (resource) with an audience**, for example `watchtower`, so that access tokens for Watchtower carry
   `"aud": "watchtower"`.
2. **Roles for your users**, delivered in a claim of the access token, for example Keycloak realm roles named
   `wt-admin`, `wt-operator` and `wt-viewer`.
3. **A public client for the dashboard** (authorization code flow with PKCE, no client secret), with the redirect
   URI `https://<your controller>/ui/`.

### Controller settings

```bash
AUTH_PROVIDER=oidc,http                    # people via SSO, workers via the token server
OIDC_ISSUER=https://idp.example.com/realms/watchtower
OIDC_AUDIENCE=watchtower
OIDC_CLIENT_ID=watchtower-dashboard        # shows the "Sign in with SSO" button
OIDC_ROLE_CLAIM=realm_access.roles         # where the roles are in the token
OIDC_ROLE_MAP=wt-admin:admin,wt-operator:operator,wt-viewer:viewer
```

`OIDC_ROLE_MAP` translates your provider's role names into Watchtower roles. Without it, the roles in the token must
be called `admin`, `operator` and `viewer` exactly. A user with none of the mapped roles is refused; a user with
several gets the highest.

Where the roles usually are:

| Provider | `OIDC_ROLE_CLAIM` | Good to know |
|---|---|---|
| Keycloak | `realm_access.roles` (realm roles) or `resource_access.<client>.roles` (client roles) | Add an *audience* mapper so access tokens carry `OIDC_AUDIENCE`. |
| Microsoft Entra ID | `roles` (app roles) | The issuer is `https://login.microsoftonline.com/<tenant>/v2.0`; the audience is the API's client id. |
| Auth0 | Your namespaced claim, for example `https://watchtower.example.com/roles` | Add the claim with an Action; the audience is the API identifier. |
| Okta | `groups` | Add a groups claim to the authorization server. |

The name shown in the dashboard and the audit log is taken from the first of `preferred_username`, `email` and `sub`
that the token contains (`OIDC_SUBJECT_CLAIMS`). The provider's signing keys are cached, and refreshed
automatically when your provider rotates them.

Scripts can use SSO too: obtain an access token from your provider (for example with the client credentials flow)
and send it as `Authorization: Bearer <token>`.

## Using a fixed list of tokens

For a small installation without a token server, list the tokens in the controller's configuration:

```bash
AUTH_PROVIDER=static
STATIC_TOKENS=admin:<long random>,operator:<long random>,worker:<long random>
```

Or put one `role:token` per line in a file and set `STATIC_TOKENS_FILE` to its path. Generate tokens with
`openssl rand -base64 32 | tr '+/' '-_'`. Changing the list means restarting the controller, and there is no way to
revoke one person's access without changing a token everyone shares, so use the token server once more than a
couple of people are involved.

To encrypt results without a token server, also give the controller a public key with `RESULT_PUBLIC_KEY_FILE`.

## Using your own identity service

If you already have API tokens in your own system, you can let it answer the controller's question directly: a
single `GET` endpoint that says whether a token is valid and which role it has. [Token servers](token-server.md)
describes the exact request and answer, with a complete example.

## Turning sign-in off

`AUTH_PROVIDER=none` disables authentication completely, and the controller refuses to start with it unless you
also set `ALLOW_NO_AUTH=true`. Use it only for experiments on a machine nobody else can reach.

## Protection against guessing

Each client address may fail to sign in 30 times in five minutes (`AUTH_FAILURE_LIMIT`,
`AUTH_FAILURE_WINDOW_SECONDS`). After that, it gets *429 Too Many Requests* until the window has passed. Failed
worker sign-ins count too. If the controller sits behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy's
address so that the limit applies to real client addresses rather than to the proxy.
