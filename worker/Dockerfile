# syntax=docker/dockerfile:1.7
# Build from the repo root:  docker build -f worker/Dockerfile -t watchtower-worker .
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/contracts/pyproject.toml packages/contracts/README.md packages/contracts/
COPY controller/pyproject.toml controller/README.md controller/
COPY worker/pyproject.toml worker/README.md worker/
COPY token-server/pyproject.toml token-server/README.md token-server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package watchtower-worker
COPY packages/contracts packages/contracts
COPY worker worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package watchtower-worker

FROM ${PYTHON_IMAGE}
# onnxruntime needs OpenMP at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home watchtower \
 && install -d -o watchtower -g watchtower /var/lib/watchtower
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 STATE_DIR=/var/lib/watchtower
USER watchtower
VOLUME /var/lib/watchtower
EXPOSE 8081
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8081/livez', timeout=2).status != 200)"]
CMD ["watchtower-worker"]
