#!/usr/bin/with-contenv bashio
# Home APM add-on entrypoint.
# Reads the user's options (Supervisor writes them to /data/options.json and
# bashio exposes them) and maps them to the environment variables the sidecar
# reads, then execs the sidecar.
#
# UNTESTED ON HAOS/SUPERVISOR (see addon/README.md). `bashio` only exists inside
# the HA base image under Supervisor; this script has not been run there.
set -e

export OTLP_ENDPOINT="$(bashio::config 'otlp_endpoint')"
export HA_URL="$(bashio::config 'ha_url')"
export HOMEAPM_MODE="$(bashio::config 'mode')"

# Token: if the user left ha_token blank, fall back to the Supervisor-provided
# token (available as $SUPERVISOR_TOKEN when homeassistant_api: true), which is
# the preferred HAOS path — no long-lived token to paste.
HA_TOKEN_OPT="$(bashio::config 'ha_token')"
if [ -n "${HA_TOKEN_OPT}" ]; then
  export HA_TOKEN="${HA_TOKEN_OPT}"
else
  export HA_TOKEN="${SUPERVISOR_TOKEN}"
fi

bashio::log.info "Starting Home APM sidecar (mode=${HOMEAPM_MODE}, otlp=${OTLP_ENDPOINT})"
exec python3 -m homeapm
