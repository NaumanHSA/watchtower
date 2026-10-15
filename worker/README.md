# Watchtower worker

Runs next to cameras. It pulls its assignments from the controller, runs one face
detection engine process per camera (YuNet on onnxruntime, an IoU tracker), delivers
results to your results URL in `realtime` or `reliable` mode, and serves live video
through a MediaMTX that shares its network namespace. It exposes no control API; its
only port serves health, Prometheus metrics and MediaMTX's auth hook.

```bash
docker build -f worker/Dockerfile -t watchtower-worker .   # from the repo root
uv run watchtower-worker                                   # or locally
```

- Configuration: [docs/configuration.md](../docs/configuration.md#worker)
- Results format and delivery: [docs/results.md](../docs/results.md)
- Design and failure handling: [docs/architecture.md](../docs/architecture.md)
- Deploying: [deploy/compose/worker](../deploy/compose/worker)

Code map: `agent.py` sync loop, `supervisor.py` engine processes and MediaMTX paths,
`delivery.py` result queues and spool, `engine/` frame source, detector, tracker and
face snapshots, `health.py` probes, metrics and auth hook.
