# Contributing

Thanks for helping. Bug reports, fixes, docs and new features are all welcome.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree on the
  approach.
- Security problems: don't open an issue; follow [SECURITY.md](SECURITY.md).

## Making a change

1. Set up as in [docs/development.md](docs/development.md) (`uv sync --all-packages`,
   a local MongoDB for the controller tests).
2. Install the hooks: `pip install pre-commit && pre-commit install` (ruff, gitleaks,
   whitespace).
3. Keep changes focused, and add or update tests. CI runs `ruff check .`, `pytest`
   against a real MongoDB, Docker builds, a secret scan and a dependency audit.
4. Protocol changes go in `packages/contracts`; read the conventions in
   [docs/development.md](docs/development.md#conventions) first.
5. Update the docs and `CHANGELOG.md` when behaviour changes.

## Pull requests

Describe what changed and why, how you tested it, and any change to configuration,
the API or the protocol. Small, reviewable PRs get merged faster.

## License

Watchtower is licensed under the [Apache License 2.0](LICENSE). By contributing, you
agree that your contributions are licensed under the same terms.

## Conduct

Be kind and assume good intent. Harassment or personal attacks aren't tolerated;
maintainers may remove comments or contributors that cross that line.
