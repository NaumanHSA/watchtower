# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/NaumanHSA/watchtower/security/advisories/new)
for this repository. Don't open a public issue.

Include what is affected, how to reproduce it, and the impact you expect. We aim to
acknowledge reports within a few working days and to agree on a disclosure date with
you.

## Supported versions

Security fixes go into the latest release. Version 1.x is no longer supported; see
[docs/upgrading-from-v1.md](docs/upgrading-from-v1.md).

## Hardening checklist for operators

- Serve the controller over HTTPS (`--profile tls`, or your own proxy).
- Never run `AUTH_PROVIDER=none` outside a development machine.
- Use long random tokens, give people the lowest role that works (`viewer`), and
  revoke tokens when they are no longer needed.
- Set `TOKEN_SERVER_MASTER_KEY` and back up the token server's volume securely.
- Set `REQUIRE_ENCRYPTION=true` on workers when results must never travel in plaintext.
- Keep the worker's port 8081 local (the default) and expose only the WebRTC ports.
- Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy only; review the audit
  log (`GET /api/v1/audit`) after incidents.
- Rotate camera passwords that were ever stored in a stream's `source_url` if the
  controller's database might have been exposed.
