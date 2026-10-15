# Development

## Setup

You need [uv](https://docs.astral.sh/uv/) and Docker. uv installs the right Python
(3.12, from `.python-version`) by itself.

```bash
uv sync --all-packages          # one .venv with every package, editable
docker run -d --name wt-test-mongo -p 127.0.0.1:27018:27017 mongo:7
uv run ruff check .
uv run pytest                   # controller tests are skipped if MongoDB isn't reachable
```

`TEST_MONGO_URI` points the tests at another MongoDB.

## Layout

```
packages/contracts/watchtower_contracts   protocol models + result envelope crypto
controller/watchtower_controller          app.py (wiring), scheduler.py, auth.py, routers/, static/ (dashboard)
worker/watchtower_worker                   agent.py (sync loop), supervisor.py (engines), delivery.py,
                                           engine/ (source, detector, tracker, faces), health.py
token-server/watchtower_token_server       store.py, app.py, __main__.py (CLI)
deploy/                                    Compose stacks, Helm chart, MediaMTX config
```

## Running pieces locally

```bash
# controller against a local MongoDB, no token server
AUTH_PROVIDER=static STATIC_TOKENS=admin:dev,worker:devw MONGO_URI=mongodb://localhost:27018 \
  uv run watchtower-controller

# a worker without MediaMTX (analysis only, no live view)
CONTROLLER_URL=http://localhost:7000 WATCHTOWER_TOKEN=devw MEDIAMTX_ENABLED=false \
  STATE_DIR=.state RESULTS_URL=http://localhost:9000/v1/ingest uv run watchtower-worker

# a token server that also prints received results
TOKEN_SERVER_INGEST_ENABLED=true uv run watchtower-token-server
```

Cameras must be network sources (`rtsp://`, `http://`…). To test with a video file,
publish it with any RTSP server, such as the dev stack's fake camera
(`deploy/compose/dev/camera/`). Usually the whole stack in Docker is easiest:
`docker compose -f deploy/compose/dev/compose.yml up --build`. The worker's frame
reader also plays local files in real time and loops them, which the engine tests rely on.

## Dashboard and documentation

The dashboard is plain HTML, CSS and JavaScript in `controller/watchtower_controller/static/`:
no build step and no third-party code. Run the controller with `uv run watchtower-controller`
and reload the page after editing. Everything user-controlled must be inserted with
`textContent`; the page is served under a strict Content Security Policy.

This documentation is a [MkDocs](https://www.mkdocs.org/) site with the Material theme,
configured in `mkdocs.yml` and published to GitHub Pages by `.github/workflows/docs.yml` on
every push to `main` that touches it. Preview it while you write:

```bash
uv run --only-group docs mkdocs serve           # http://127.0.0.1:8000/watchtower/
uv run --only-group docs mkdocs build --strict  # what CI runs: broken links fail the build
```

Link to other guides with relative paths (`[Results](results.md#encryption)`). Links to
files outside `docs/` (`../deploy/compose`) work on GitHub as written; on the site a small
hook (`docs/.hooks/github_links.py`) points them at the same file on GitHub. New pages must
be added to `nav` in `mkdocs.yml`, or the strict build fails.

## Conventions

- The protocol lives in `watchtower_contracts`. Add fields as optional with defaults;
  never remove or rename one without bumping `PROTOCOL_VERSION`.
- The controller never calls workers. Anything a worker should do goes into the sync
  response.
- Only the scheduler assigns streams to workers; API handlers change desired state and
  wake it.
- Tests use real MongoDB, real spawned processes and in-process HTTP
  (`httpx.ASGITransport`, `httpx.MockTransport`) rather than mocks of our own code.
- Line length 120, ruff for lint and import order.

## Releasing

Update `CHANGELOG.md` and the versions in the four `pyproject.toml` files, then push a
tag `vX.Y.Z`. The release workflow publishes multi-arch images to
`ghcr.io/<owner>/watchtower-{controller,worker,token-server}`.
