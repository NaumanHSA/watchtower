# Changelog

## 2.0.0 (unreleased)

A redesign of how the controller and workers cooperate. Not compatible with 1.x; see
[docs/upgrading-from-v1.md](docs/upgrading-from-v1.md).

### Added
- Open-source license: Apache-2.0.
- Pull-based worker protocol (register once, then sync desired state); workers need no
  inbound control port.
- Leader-elected scheduler with worker liveness, leases, failover, draining, retries
  with backoff and a `failed` state.
- Pluggable authentication: OpenID Connect (JWT validation, role-claim mapping,
  dashboard SSO with PKCE), any token server answering a small contract, static tokens,
  or none for development; providers can be chained. Roles: viewer, operator, admin,
  worker.
- Reference token server (`token-server/`): roles, expiry, hashed tokens, rotatable
  results keys with optional encryption at rest, admin API, CLI, optional decrypting
  results receiver with forwarding.
- Result delivery modes `realtime` and `reliable` (on-disk spool), per camera.
- Web dashboard (light theme, no build step, strict CSP): an overview with fleet health and what needs
  attention, camera management with search and filters, a live video wall, worker cards with load and
  delivery counters, live activity and an audit log. Live updates over WebSocket and in-browser WebRTC live
  view (TURN relays supported).
- Per-camera processing settings through the API.
- Health, readiness and Prometheus metrics for the controller and workers.
- Audit log of every change (`GET /api/v1/audit`, dashboard Audit tab) and a per-address
  limit on failed sign-ins.
- Docker images (non-root, health checks), Compose stacks for development, the control
  plane and worker hosts, a Helm chart for Kubernetes, CI with tests, image builds, secret scanning and dependency
  audit, and image publishing on release tags.
- A complete manual (getting started, cameras, workers, results, sign-in, production,
  Kubernetes, monitoring, troubleshooting) and reference pages (configuration, API, token
  servers, architecture, privacy), published as a website on GitHub Pages:
  https://naumanhsa.github.io/watchtower/
- Optional HTTPS for live view on worker hosts (`--profile tls` in `deploy/compose/worker`),
  needed when the dashboard is served over HTTPS.
- The development stack can run a second worker (`--profile second-worker`) and offers
  three fake camera addresses, to try placement and failover on one machine.
- The controller logs why a sign-in was refused (never the token).

### Fixed during 2.0 development
- ONNX Runtime 1.31 sent usage telemetry to Microsoft from camera hosts even after
  `disable_telemetry_events()`; workers now set `ORT_DISABLE_TELEMETRY=1` before it loads
  (verified with a packet capture).
- SSO sign-ins failed for about a minute after a machine booted (the signing-key cache
  looked fresh at boot).
- A user who signed in through SSO without a Watchtower role was returned to the sign-in
  page without an explanation.

### Fixed (relative to 1.x)
- Results were never sent.
- Workers shut themselves down when the controller was briefly unreachable.
- The worker API accepted commands without authentication; MediaMTX accepted anyone.
- Token verification was disabled.
- Several scheduling bugs (one worker overloaded on recovery, metadata lost, PATCH
  reassigning streams) and an IoU tracker that could merge different faces.
