# Home APM add-on image.
# Unlike deploy/Dockerfile (python:3.11-slim, built from the repo root for the
# Compose/casting path), a Supervisor add-on builds FROM Home Assistant's own
# base image, which Supervisor substitutes per architecture via BUILD_FROM.
# Spec: https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
#
# UNTESTED ON HAOS/SUPERVISOR (see addon/README.md). Never built by Supervisor.
ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# HA base images are Alpine-based; ensure Python 3.11 + pip are present.
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

# The add-on build context is expected to include the packaged sidecar source.
# In a real release this repo subtree ships pyproject.toml + src/ alongside the
# add-on (or installs homeapm from a published wheel/index). For structure
# purposes we mirror deploy/Dockerfile's install.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --break-system-packages .

# run.sh reads Supervisor's /data/options.json and maps the user's options to
# the env vars homeapm expects (HA_URL, HA_TOKEN, OTLP_ENDPOINT, HOMEAPM_MODE),
# then execs `python -m homeapm`.
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
