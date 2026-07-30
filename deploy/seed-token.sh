#!/usr/bin/env bash
# Home APM - Home Assistant token injector (feature #7).
#
# Bridges an HA long-lived access token into the sidecar/console environment at
# cast time, without baking secrets into casting.yaml or into the image.
#
# Flow:
#   1. foundryctl -f casting.yaml -p pours forge      # generates compose
#   2. bash deploy/seed-token.sh                      # <-- this script
#   3. foundryctl -f casting.yaml -p pours cast --no-forge
#
# --no-forge on the cast is REQUIRED: a plain `cast` re-runs forge and would
# wipe the homeapm.env we write into pours/deployment/.
#
# THIS SCRIPT NEVER FAILS THE DEPLOYMENT (always exits 0 on the happy and the
# bootstrap paths). A fresh clone has NO token: `.ha-runtime/` is gitignored and
# ha-config/.storage/ is gitignored too, so the seeded house has no user account
# and no token can exist until Home Assistant has been onboarded by hand. The
# token can only be minted from a RUNNING HA, and HA only runs after `cast` -
# so aborting here would deadlock the deployment. Instead we write a PLACEHOLDER
# env file (so the stack can start) and print the recovery steps.
#
# Run from the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="${REPO_ROOT}/.ha-runtime/token.txt"
ENV_MASTER="${REPO_ROOT}/deploy/homeapm.env"
POURS_DIR="${POURS_DIR:-${REPO_ROOT}/pours}"
ENV_TARGET="${POURS_DIR}/deployment/homeapm.env"

bootstrapped=0

# ---------------------------------------------------------------------------
# 1. Materialise deploy/homeapm.env.
# ---------------------------------------------------------------------------
if [[ -f "${ENV_MASTER}" ]]; then
  echo "seed-token: using existing ${ENV_MASTER} (BYOH or previously seeded)"
elif [[ -f "${TOKEN_FILE}" ]]; then
  TOKEN="$(tr -d '[:space:]' < "${TOKEN_FILE}")"
  if [[ -z "${TOKEN}" ]]; then
    echo "seed-token: WARNING ${TOKEN_FILE} is empty - bootstrapping instead." >&2
    bootstrapped=1
  else
    printf 'HA_TOKEN=%s\n' "${TOKEN}" > "${ENV_MASTER}"
    echo "seed-token: wrote ${ENV_MASTER} from .ha-runtime/token.txt"
  fi
else
  bootstrapped=1
fi

if [[ "${bootstrapped}" -eq 1 ]]; then
  # BOOTSTRAP: no token available. Write a placeholder so that Compose can parse
  # the project and bring the stack (SigNoz, ClickHouse, MCP, HA) up. The
  # sidecar will start but stay unauthenticated until a real token is supplied.
  cat > "${ENV_MASTER}" <<'PLACEHOLDER'
# Home APM - PLACEHOLDER env file written by deploy/seed-token.sh.
# No Home Assistant token was available yet (a fresh clone never has one).
# The stack can start with this file in place; the sidecar will log HA auth
# failures until you replace the empty value below with a real long-lived
# access token. See the numbered steps printed by seed-token.sh, or
# deploy/NOTES.md -> "Seeded-token flow".
HA_TOKEN=
PLACEHOLDER
  echo "seed-token: no token found - wrote PLACEHOLDER ${ENV_MASTER}"
  cat >&2 <<'RECOVERY'

  ============================================================================
  seed-token: BOOTSTRAP - Home APM will start, but WITHOUT a Home Assistant
  token the sidecar cannot read the house. "Seeded" means YAML config only:
  ha-config/.storage/ is gitignored, so this Home Assistant has NO user account
  yet. Mint a token by hand - it takes about a minute:

    1. Bring the stack up:
         foundryctl -f casting.yaml -p pours cast --no-forge
    2. Open Home Assistant:
         http://localhost:8123
    3. Complete the HA onboarding wizard (create your user account).
    4. In HA: click your user name (bottom left) -> Security tab ->
       "Long-lived access tokens" -> Create token. Copy it once; HA never
       shows it again.
    5. Write it into the env master (from the repo root):
         echo "HA_TOKEN=<paste-token-here>" > deploy/homeapm.env
    6. Re-run this script to publish it next to the generated compose:
         bash deploy/seed-token.sh
    7. Restart the sidecar to pick it up:
         docker restart home-apm-sidecar
       (also `docker restart home-apm-console` if you added GEMINI_API_KEY)

  BYOH users: copy deploy/homeapm.env.example to deploy/homeapm.env and fill in
  a token from your own Home Assistant, then start at step 6.
  ============================================================================

RECOVERY
fi

# ---------------------------------------------------------------------------
# 2. Copy it next to the generated compose so `env_file: homeapm.env` resolves.
#    Non-fatal if pours/ is absent: the fallback path
#    (deploy/docker-compose.fallback.yml) never runs `forge`, and reads
#    deploy/homeapm.env directly from its own project directory, so there is
#    nothing to copy and nothing to fail.
# ---------------------------------------------------------------------------
if [[ -d "$(dirname "${ENV_TARGET}")" ]]; then
  cp "${ENV_MASTER}" "${ENV_TARGET}"
  if [[ "${bootstrapped}" -eq 1 ]]; then
    echo "seed-token: copied PLACEHOLDER env to ${ENV_TARGET} (no token yet)"
  else
    echo "seed-token: injected token into ${ENV_TARGET}"
  fi
  echo "seed-token: now run  foundryctl -f casting.yaml -p pours cast --no-forge"
else
  echo "seed-token: NOTE ${POURS_DIR}/deployment not found - skipping the copy." >&2
  echo "  This is EXPECTED on the fallback path (docker-compose.fallback.yml reads" >&2
  echo "  deploy/homeapm.env directly). ${ENV_MASTER} is ready:" >&2
  echo "    docker compose -f deploy/docker-compose.fallback.yml up -d --build" >&2
  echo "  For the full cast, run 'foundryctl -f casting.yaml -p pours forge' first," >&2
  echo "  then re-run this script." >&2
fi

exit 0
