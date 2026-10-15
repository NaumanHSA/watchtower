# Changelog

## 2.0.0 — unreleased

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
- Web dashboard with live updates and in-browser WebRTC live view (TURN relays supported).
- Per-camera processing settings through the API.
- Health, readiness and Prometheus metrics for the controller and workers.
- Audit log of every change (`GET /api/v1/audit`, dashboard Audit tab) and a per-address
  limit on failed sign-ins.
- Docker images (non-root, health checks), Compose stacks for development, the control
  plane and worker hosts, a Helm chart for Kubernetes, CI with tests, image builds, secret scanning and dependency
  audit, and image publishing on release tags.
- Documentation for architecture, deployment, configuration, API, token servers,
  results, privacy and development.

### Fixed (relative to 1.x)
- Results were never sent.
- Workers shut themselves down when the controller was briefly unreachable.
- The worker API accepted commands without authentication; MediaMTX accepted anyone.
- Token verification was disabled.
- Several scheduling bugs (one worker overloaded on recovery, metadata lost, PATCH
  reassigning streams) and an IoU tracker that could merge different faces.
