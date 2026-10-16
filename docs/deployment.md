# Deploying to production

This guide takes you from empty machines to a running installation that real users and real cameras depend on. It
uses Docker Compose; for Kubernetes, read this page for the overall plan and then
[Running on Kubernetes](kubernetes.md).

## The shape of an installation

A production installation has three kinds of machines:

- **One control plane host** runs the controller, MongoDB and (usually) the bundled token server, behind HTTPS.
  Everyone and everything talks to it: people through the dashboard, scripts through the API, workers through
  their syncs. It needs little processing power; 2 processor cores and 4 GB of memory are plenty for hundreds of
  cameras.
- **Worker hosts**, as many as your cameras need, placed where they can reach the cameras. They only make outgoing
  connections, so they can sit at remote sites behind NAT.
- **Your results receiver**, the application that receives detections. Watchtower doesn't host it for you.

The control plane and each worker host are deployed separately, from `deploy/compose/controller` and
`deploy/compose/worker`.

## Before you start

Decide and prepare these things first; they are much harder to change later.

**A DNS name for the control plane**, for example `watchtower.example.com`, pointing at the control plane host.
Workers and people will use it.

**Firewall rules:**

| Machine | Allow in | Why |
|---|---|---|
| Control plane | 80/tcp and 443/tcp from everywhere that uses Watchtower | HTTPS (port 80 is used to obtain the certificate) |
| Worker hosts | 8889/tcp, 8189/udp, 8189/tcp from viewers (or 80 and 443 with HTTPS live view) | Live video only |
| Worker hosts | Outgoing to the control plane (443), the cameras (usually 554) and your results receiver | Normal operation |

MongoDB and the token server are never exposed; they are reachable only inside the control plane's Docker network.

**How people will sign in**: the bundled token server, your single sign-on, or a fixed list of tokens. See
[Users and sign-in](access.md). This guide uses the token server; the other options only change a few settings.

**Where results go**: your application's address, or the token server acting as a decrypting receiver. See
[Receiving results](results.md).

## Step 1: Prepare the control plane host

Install Docker with the Compose plugin, then get the deployment files:

```bash
git clone https://github.com/NaumanHSA/watchtower.git
cd watchtower/deploy/compose/controller
cp .env.example .env
```

## Step 2: Configure it

Open `.env` and work through it:

```bash
# Sign-in: ask the bundled token server
AUTH_PROVIDER=http
TOKEN_SERVER_URL=http://token-server:9000/v1/token/verify

# Where workers send results by default
RESULTS_URL=https://your-app.example.com/watchtower/events

# Token server keys (generate both, keep them secret)
TOKEN_SERVER_ADMIN_KEY=<output of: openssl rand -hex 32>
TOKEN_SERVER_MASTER_KEY=<output of: docker compose run --rm token-server gen-master-key>

# HTTPS with an automatic certificate
DOMAIN=watchtower.example.com
CONTROLLER_BIND=127.0.0.1        # only Caddy and this host reach the controller directly
FORWARDED_ALLOW_IPS=*            # safe because of the line above; lets rate limits see real addresses

LOG_FORMAT=json
```

What these do:

- `AUTH_PROVIDER` and `TOKEN_SERVER_URL` make the controller ask the bundled token server about every token.
- `RESULTS_URL` is where workers send detections unless a camera says otherwise.
- `TOKEN_SERVER_ADMIN_KEY` enables the token server's admin API; `TOKEN_SERVER_MASTER_KEY` encrypts the results
  private keys inside the token server's storage. Without the master key they are stored unencrypted (but readable
  only by the token server's user).
- `DOMAIN` is the name Caddy, the bundled HTTPS proxy, gets a certificate for. Ports 80 and 443 must reach this
  host from the internet for the certificate to be issued.
- `CONTROLLER_BIND=127.0.0.1` stops the controller's own port 7000 from being reachable from the network, so all
  traffic goes through HTTPS. `FORWARDED_ALLOW_IPS=*` then tells the controller to trust the client address Caddy
  passes on, which the sign-in rate limit and the audit log use.

Every other controller setting is optional and explained in the [configuration reference](configuration.md#controller).

!!! tip "Using your own reverse proxy"
    Leave out `DOMAIN` and the `tls` profile, put your proxy in front of port 7000, and set `FORWARDED_ALLOW_IPS` to
    the proxy's address. The proxy must pass WebSocket connections through (the dashboard's live updates use
    `/api/v1/events`).

## Step 3: Start the control plane

```bash
docker compose --profile token-server --profile tls up -d --build
docker compose ps
```

All services should be running, and the controller `healthy`. From any machine, check
`https://watchtower.example.com/readyz`: it answers with `"status": "ok"` once the controller is connected to
MongoDB.

## Step 4: Sign in and create real tokens

On its first start the token server creates an admin token and a worker token. Show them:

```bash
docker compose exec token-server cat /data/bootstrap-tokens.txt
```

Open `https://watchtower.example.com`, sign in with the admin token, and create personal tokens for yourself and
your team, then revoke the bootstrap ones once you no longer need them:

```bash
docker compose exec token-server watchtower-token-server issue --role admin --subject you@example.com
docker compose exec token-server watchtower-token-server issue --role viewer --subject guard-desk
docker compose exec token-server watchtower-token-server list
docker compose exec token-server watchtower-token-server revoke <id of the bootstrap admin token>
```

To use single sign-on instead, follow [Users and sign-in](access.md#using-single-sign-on) now.

## Step 5: Add workers

Create one worker token per site or machine:

```bash
docker compose exec token-server watchtower-token-server issue --role worker --subject site-a
```

Then set up each worker host as described in [Workers: adding a worker](workers.md#adding-a-worker-step-by-step).
Because the dashboard uses HTTPS, give each worker an HTTPS address for live view as well
([how](workers.md#when-the-dashboard-uses-https)).

## Step 6: Connect your results receiver

Make sure the address in `RESULTS_URL` accepts results, and that it can decrypt them: export the private key with
`docker compose exec token-server watchtower-token-server export-key` and give it to your receiver. [Receiving
results](results.md) has a complete example receiver.

## Step 7: Add cameras

Add your cameras in the dashboard or through the API, as described in [Cameras](cameras.md). Watch the **Overview**
page: each camera should be *running* within seconds, and *Results delivered* should start counting once people
walk past.

## Production checklist

Go through this list before you rely on the installation:

- [ ] The dashboard and API are only reachable over HTTPS, and port 7000 isn't reachable from the network.
- [ ] The bootstrap tokens are revoked, and every person has their own token (or uses single sign-on).
- [ ] Each site has its own worker token.
- [ ] Workers have `REQUIRE_ENCRYPTION=true`, and only your receiver holds the results private key.
- [ ] `TOKEN_SERVER_MASTER_KEY` and `TOKEN_SERVER_ADMIN_KEY` are set and stored somewhere safe.
- [ ] Live view uses HTTPS addresses on the workers, and only the live view ports are open on worker hosts.
- [ ] Backups of MongoDB and the token server data are scheduled and have been restored once as a test
      ([how](operations.md#backups)).
- [ ] Monitoring alerts you when a worker goes offline or cameras fail ([how](operations.md#what-to-alert-on)).
- [ ] You have read [Privacy](privacy.md) and decided how long your receiver keeps results.

## Growing the installation

**More cameras.** Add worker hosts, or raise `MAX_STREAMS` on workers that have processor time to spare. New
cameras are spread over workers automatically.

**High availability for the controller.** Run two or more controllers behind a load balancer, all using the same
MongoDB, and set `REDIS_URL` on all of them so that live dashboard updates reach every user whichever controller
they are connected to. Only one controller schedules at a time; if it fails, another takes over within seconds.
Use a MongoDB replica set (or a managed MongoDB service) so the database isn't a single point of failure either.
On workers, consider `ON_LEASE_EXPIRY=stop` in this setup (see [Workers](workers.md#what-each-worker-setting-does)).

**Many sites.** Each site needs workers that can reach its cameras. Watchtower can't pin a camera to a site's
worker, so either make every worker able to reach every camera (a routed or VPN network), or run one Watchtower
installation per site.

## Keeping it running

[Monitoring and maintenance](operations.md) covers health checks, metrics, alerts, backups and upgrades.
