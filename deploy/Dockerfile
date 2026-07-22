# Home APM sidecar image.
# Build context is the REPO ROOT (see deploy/casting.yaml build.context: ../../
# and deploy/docker-compose.fallback.yml). pyproject.toml lives at the repo
# root; the package source is at src/homeapm.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy the build metadata + package source, then install. Copying pyproject and
# README first keeps the dependency layer cacheable across source-only edits.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Installs homeapm plus its runtime deps (websockets, httpx, opentelemetry-sdk,
# opentelemetry-exporter-otlp-proto-http) as declared in pyproject.toml.
RUN pip install .

# homeapm reads HA_URL, HA_TOKEN, OTLP_ENDPOINT from the environment
# (injected by the compose service / env_file).
ENTRYPOINT ["python", "-m", "homeapm"]
