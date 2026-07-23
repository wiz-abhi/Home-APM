#!/usr/bin/env bash
# Home APM â€” seeded-token injector (demo mode, feature #7).
#
# Bridges the pre-created HA long-lived token into the sidecar's environment at
# cast time, without baking secrets into casting.yaml or the image.
#
# Flow:
#   1. foundryctl -f casting.yaml -p pours forge   # generates compose
#   2. bash deploy/seed-token.sh                          # <-- this script
#   3. foundryctl -f casting.yaml -p pours cast --no-forge
#
# --no-forge on the cast is REQUIRED: a plain `cast` re-runs forge and would
# wipe the homeapm.env we write into pours/deployment/.
#
# Run from the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="${REPO_ROOT}/.ha-runtime/token.txt"
ENV_MASTER="${REPO_ROOT}/deploy/homeapm.env"
POURS_DIR="${POURS_DIR:-${REPO_ROOT}/pours}"
ENV_TARGET="${POURS_DIR}/deployment/homeapm.env"

# 1. Materialise deploy/homeapm.env.
if [[ -f "${ENV_MASTER}" ]]; then
  echo "seed-token: using existing ${ENV_MASTER} (BYOH or previously seeded)"
elif [[ -f "${TOKEN_FILE}" ]]; then
  TOKEN="$(tr -d '[:space:]' < "${TOKEN_FILE}")"
  if [[ -z "${TOKEN}" ]]; then
    echo "seed-token: ERROR ${TOKEN_FILE} is empty" >&2
    exit 1
  fi
  printf 'HA_TOKEN=%s\n' "${TOKEN}" > "${ENV_MASTER}"
  echo "seed-token: wrote ${ENV_MASTER} from .ha-runtime/token.txt"
else
  echo "seed-token: ERROR no token found." >&2
  echo "  DEMO mode expects ${TOKEN_FILE}" >&2
  echo "  BYOH mode expects ${ENV_MASTER} (see deploy/homeapm.env.example)" >&2
  exit 1
fi

# 2. Copy it next to the generated compose so env_file: homeapm.env resolves.
if [[ ! -d "$(dirname "${ENV_TARGET}")" ]]; then
  echo "seed-token: ERROR ${POURS_DIR}/deployment not found â€” run 'foundryctl -f casting.yaml -p pours forge' first." >&2
  exit 1
fi
cp "${ENV_MASTER}" "${ENV_TARGET}"
echo "seed-token: injected token into ${ENV_TARGET}"
echo "seed-token: now run  foundryctl -f casting.yaml -p pours cast --no-forge"
