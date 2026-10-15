# Watchtower controller

The control plane: keeps the desired state of every camera in MongoDB, places cameras
on workers (one leader-elected scheduler), serves the REST API, the live event stream
and the web dashboard.

```bash
docker build -f controller/Dockerfile -t watchtower-controller .   # from the repo root
uv run watchtower-controller                                       # or locally
```

- Configuration: [docs/configuration.md](../docs/configuration.md#controller)
- API: [docs/api.md](../docs/api.md), or `/docs` on a running controller
- Design: [docs/architecture.md](../docs/architecture.md)
- Deploying: [deploy/compose/controller](../deploy/compose/controller)

Code map: `app.py` wiring and dashboard, `scheduler.py` placement and liveness,
`auth.py` token providers, `deps.py` request auth, `routers/` HTTP endpoints,
`events.py` WebSocket fan-out, `static/` the dashboard.
