# Troubleshooting

Problems are grouped by what you notice. Each entry says what is usually behind it and how to fix it. If you can't
find your problem here, see [Getting help](#getting-help) at the end.

Most answers start with a log. On the machine in question, from the Compose folder you started it from:

```bash
docker compose ps                    # is everything running and healthy?
docker compose logs --tail 100 controller
docker compose logs --tail 100 worker
docker compose logs --tail 100 mediamtx
```

## Signing in

**"That token was not accepted."**
The token is wrong, revoked or expired, or it has the *worker* role, which can't open the dashboard. Check for
spaces copied along with it. With the bundled token server, `watchtower-token-server list` shows whether the token
still exists and when it expires.

**"Too many failed sign-in attempts; try again later."**
Your address failed to sign in 30 times within five minutes. Wait five minutes. If many people share one address
behind a proxy and keep hitting this, the controller is probably seeing the proxy's address instead of theirs: set
`FORWARDED_ALLOW_IPS` to the proxy's address.

**Sign-in fails with a 503 error.**
The controller can't reach the token server, so it can't tell whether your token is valid. Check that the token
server is running and that `TOKEN_SERVER_URL` is right.

**There is no "Sign in with SSO" button.**
`OIDC_CLIENT_ID` isn't set on the controller.

**SSO doesn't work.**

- Your identity provider shows an error about the redirect address: register `https://<controller address>/ui/`
  exactly, including the trailing slash.
- The dashboard says *The identity provider refused the sign-in*: exchanging the sign-in code for a token failed.
  The dashboard's client must be a *public* client (no client secret) that allows the authorization code flow with
  PKCE.
- The dashboard says *You signed in, but Watchtower didn't accept your account*: the provider let the user in, but
  the controller refused the token. Usually the user has no role that maps to a Watchtower role (check
  `OIDC_ROLE_CLAIM` and `OIDC_ROLE_MAP`), or the token's audience doesn't match `OIDC_AUDIENCE`. The controller's
  log says which. See [Users and sign-in](access.md#using-single-sign-on).

**The sidebar says "Reconnecting…" instead of "Live updates on".**
The browser can't keep a WebSocket connection to `/api/v1/events`. A reverse proxy in front of the controller must
allow WebSocket upgrades. The dashboard still works; it just refreshes every 15 seconds instead of instantly.

## Cameras

**A camera stays Pending.**
No online worker has a free slot, or the camera recently failed and is waiting before its next try. Open the
**Workers** page: if every worker is full (*Cameras 4 / 4*), add a worker or raise `MAX_STREAMS` on one that has
processor time to spare. Drained workers don't take cameras either.

**A camera shows Reconnecting with "cannot open source".**
The worker can't open the camera's address. Test the address from the worker machine itself (not from your own
computer) with VLC or `ffprobe`. Common causes: a typing mistake in the address, a wrong password, a password with
special characters that aren't encoded, a firewall between the worker and the camera, or a camera that has run out
of connection slots. [Cameras](cameras.md#when-a-camera-wont-start) has a full checklist.

**A camera is Failed.**
Watchtower gave up after several failed starts and won't try again by itself. Fix the cause, then press **Retry**
(the play icon) on the **Cameras** page or in *Needs attention* on the Overview.

**The FPS column shows less than the camera's setting.**
The worker is overloaded, or the camera sends fewer frames than you asked for. Look at the worker's CPU bar on the
**Workers** page. Lower the camera's frames per second or detector size, or give the worker fewer cameras.

**Adding a camera fails with "A stream with this source_url already exists".**
Another camera already uses that address. Each address can be used once, so a camera is never analysed twice.

**The camera keeps restarting.**
Each change to its source or settings restarts its analysis, which is normal. If nothing was changed, the camera's
connection is unstable: raise its reconnect interval and attempts (see [Cameras](cameras.md#connection)).

## Workers

**A new worker doesn't appear on the Workers page.**
Read the worker's log:

- `worker token rejected (...)` means the token is wrong, revoked, or doesn't have the *worker* role. Issue a new
  worker token and put it in `WATCHTOWER_TOKEN`. The worker checks again every minute, so you don't need to restart it.
- `sync failed (...); retrying in ...` means the worker can't reach `CONTROLLER_URL`. Check the address, DNS and
  firewall from the worker machine: `curl https://watchtower.example.com/livez` should answer
  `{"status": "ok"}`.
- Certificate errors mean the controller's HTTPS certificate isn't trusted by the worker machine. Use a certificate
  from a public authority (the bundled Caddy gets one automatically), or add your company's certificate authority to
  the worker image.

**A worker keeps switching between Online and Suspect.**
Its check-ins are arriving late: an unreliable network link, or a machine so overloaded that the worker process
itself is slow. Check the network between the worker and the controller, and the machine's load.

**A worker shows Offline, but the machine is running.**
The worker has lost contact with the controller for over a minute. If its log shows sync failures, see above. If
the worker was recreated without its `worker-state` volume, it registered as a *new* worker and the old entry stays
offline: forget the old entry on the **Workers** page.

**Two entries for what is really one machine.**
The worker's state volume was deleted or not mounted, so it registered again. Keep the `worker-state` volume, and
forget the offline entry.

## Live view

**"Live view isn't available for this camera right now."**
The camera isn't running, or its worker has no `PUBLIC_WEBRTC_URL` set.

**"You're not allowed to watch this camera."**
Your token was refused when the worker checked it with the controller. Sign out and in again. If it happens for
everyone, the worker can't reach the controller (see its log), or MediaMTX can't reach the worker on port 8081.

**The player says the connection failed, or stays black.**
Your browser can't exchange video with the worker. In order:

1. Is the dashboard on `https://` and the worker's `PUBLIC_WEBRTC_URL` on `http://`? Browsers block that. Give the
   worker an HTTPS address ([how](workers.md#when-the-dashboard-uses-https)).
2. Can your browser reach the worker on 8889/tcp (or 443 with HTTPS)? Open `PUBLIC_WEBRTC_URL` in a new tab; any
   answer from the server, even an error page, means it is reachable.
3. Is 8189/udp open to the worker, and is `PUBLIC_HOST` the address your browser can reach?
4. On very strict networks, add a TURN relay ([how](workers.md#viewers-behind-strict-firewalls)).
5. Is the camera sending H.265? Many browsers can't play it. Switch the camera's stream to H.264.

Analysis doesn't depend on live view: a camera can be *running* and producing results while live view fails.

## Results

**Results delivered stays at 0.**
Look at the worker's counters on the **Workers** page:

- **Sent, Dropped and Failed all 0:** nobody has walked past yet, or faces are too small for the detector. Try a
  larger detector size for wide views.
- **Dropped rising:** no results address is set (set `RESULTS_URL` on the controller), or the worker requires
  encryption but has no key, or, in real-time mode, your receiver is too slow and results expire.
- **Failed rising:** your receiver answers with errors. Check its log.

**Queued keeps growing.**
A worker in reliable mode can't deliver: your receiver is down or answering with errors. Results are kept and sent
in order when it recovers, up to `SPOOL_MAX_MB` per worker.

**My receiver gets the same result twice.**
That is normal in reliable mode, for example when your answer to the worker was lost. Ignore results whose
`eventId` you have already stored.

**My receiver can't decrypt results.**
It has the wrong private key. Each encrypted result names its key in `meta.kid`; export that key with
`watchtower-token-server export-key <kid>`. After `rotate-key`, receivers need the new key as well as the old one for
results that were already on their way.

## The controller

**`/readyz` answers 503 with `"mongo": false`.**
The controller can't reach MongoDB. Check that MongoDB runs and that `MONGO_URI` is right, including the user name
and password.

**The controller refuses to start with `AUTH_PROVIDER=none`.**
That is deliberate. Add `ALLOW_NO_AUTH=true` if you really want no sign-in, which is only safe on a machine nobody
else can reach.

**With several controllers, some dashboards miss updates.**
Set `REDIS_URL` on every controller, so updates reach all dashboards whichever controller they are connected to.

## Getting help

If you're stuck, [open an issue](https://github.com/NaumanHSA/watchtower/issues) with:

- what you did, what you expected and what happened instead;
- the Watchtower version (shown on each worker card, and in the controller's `/openapi.json`);
- the relevant lines from the controller and worker logs, **with tokens, passwords and camera addresses removed**;
- how you deployed (Docker Compose or Kubernetes, and the operating system).

Please report security problems privately, as described in the
[security policy](https://github.com/NaumanHSA/watchtower/blob/main/SECURITY.md), not in a public issue.
