"""watchtower-token-server CLI.

  serve                         run the HTTP server (default)
  issue --role R --subject S    issue a token (printed once)
  list | revoke ID              manage tokens
  rotate-key | export-key KID   manage the results keypair
  gen-master-key                print a key for TOKEN_SERVER_MASTER_KEY
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from cryptography.fernet import Fernet

from watchtower_contracts import Role

from .settings import TokenServerSettings
from .store import Store


def _store(settings: TokenServerSettings) -> Store:
    return Store(settings.data_dir / "store.json", settings.master_key)


def bootstrap(settings: TokenServerSettings, store: Store) -> None:
    """Seed tokens from TOKEN_SERVER_BOOTSTRAP_TOKENS, or on an empty store issue an admin
    and a worker token and write them to <data_dir>/bootstrap-tokens.txt."""
    for entry in filter(None, (e.strip() for e in os.environ.get("TOKEN_SERVER_BOOTSTRAP_TOKENS", "").split(","))):
        role, _, rest = entry.partition(":")
        value, _, subject = rest.partition(":")
        if value and store.lookup(value) is None:
            store.issue(Role(role), subject or f"bootstrap-{role}", value=value)
    if store.tokens() or not settings.bootstrap:
        return
    lines = []
    for role in (Role.ADMIN, Role.WORKER):
        record, value = store.issue(role, f"bootstrap-{role.value}")
        lines.append(f"{role.value}: {value}")
    path = settings.data_dir / "bootstrap-tokens.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print("First start: issued an admin and a worker token (shown once, also saved to "
          f"{path}):\n\n  " + "\n  ".join(lines) + "\n", flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="watchtower-token-server", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the server (default)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    issue = sub.add_parser("issue", help="issue a token")
    issue.add_argument("--role", required=True, choices=[r.value for r in Role])
    issue.add_argument("--subject", required=True, help="who or what the token is for")
    issue.add_argument("--expires-days", type=int)
    issue.add_argument("--token", help="register this exact value instead of generating one")
    sub.add_parser("list", help="list tokens (values are never shown again)")
    revoke = sub.add_parser("revoke", help="revoke a token by id")
    revoke.add_argument("id")
    sub.add_parser("rotate-key", help="new results keypair; old keys still decrypt")
    export = sub.add_parser("export-key", help="print a private key, for your results receiver")
    export.add_argument("kid", nargs="?", help="default: the current key")
    sub.add_parser("gen-master-key", help="print a new TOKEN_SERVER_MASTER_KEY value")

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0].startswith("--") and argv[0] not in ("-h", "--help"):
        argv = ["serve", *argv]
    args = parser.parse_args(argv)
    settings = TokenServerSettings()

    if args.command == "gen-master-key":
        print(Fernet.generate_key().decode())
        return
    store = _store(settings)
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        logging.basicConfig(level=settings.log_level.upper(),
                            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        bootstrap(settings, store)
        host, port = args.host or settings.host, args.port or settings.port
        if not settings.admin_key:
            print("Admin API disabled (no TOKEN_SERVER_ADMIN_KEY); manage tokens with the CLI.", flush=True)
        uvicorn.run(create_app(settings, store), host=host, port=port, log_config=None)
    elif args.command == "issue":
        try:
            record, value = store.issue(Role(args.role), args.subject, value=args.token,
                                        expires_in_days=args.expires_days)
        except ValueError as e:
            sys.exit(str(e))
        print(f"Issued {record.role} token {record.id} for {record.subject!r} (shown once):\n\n  {value}\n")
    elif args.command == "list":
        tokens = store.tokens()
        if not tokens:
            print("No tokens.")
        for t in tokens:
            state = "active" if t.active else ("revoked" if t.revoked else "expired")
            print(f"{t.id}  {t.role:<8}  {state:<7}  {t.subject}  created {t.created_at}"
                  + (f"  expires {t.expires_at}" if t.expires_at else ""))
        current = store.current_key()
        if current:
            print(f"\nresults key: {current.kid} (current) + {len(store.keys()) - 1} older")
    elif args.command == "revoke":
        if not store.revoke(args.id):
            sys.exit(f"No token with id {args.id}")
        print(f"Revoked {args.id}. Controllers stop accepting it within their token cache time.")
    elif args.command == "rotate-key":
        key = store.rotate_key()
        print(f"New results key {key.kid}. Older keys still decrypt.")
    elif args.command == "export-key":
        keys = store.keys()
        key = next((k for k in keys if k.kid == args.kid), None) if args.kid else (keys[0] if keys else None)
        if key is None:
            sys.exit("No such key")
        print(store.private_pem(key), end="")


if __name__ == "__main__":
    main()
