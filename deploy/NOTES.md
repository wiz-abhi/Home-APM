# Home APM — Deploy Pack Notes

The deploy pack casts a standard SigNoz self-host (docker compose) **plus** the
SigNoz MCP server, and JSON-patches two extra services (Home Assistant + the
Home APM sidecar) onto the generated `compose.yaml`. This file documents the
verified mechanics, the seeded-token flow, and everything still unverified until
the clean-machine cast test (risk #5).

Tool: `foundryctl v0.2.11` (installed). Live stack: SigNoz `v0.132.2`.

## Files

| File | Purpose |
|------|---------|
| `casting.yaml` | Main install: compose SigNoz + MCP enabled + `spec.patches` add HA & sidecar. |
| `Dockerfile` | Sidecar image: `python:3.11-slim`, `pip install .` from repo root, `python -m homeapm`. |
| `docker-compose.fallback.yml` | Fallback (risk #5): HA + sidecar only, OTLP to `host.docker.internal:4318`, for users with existing SigNoz. |
| `seed-token.sh` | Demo-mode token injector (feature #7). |
| `homeapm.env.example` | BYOH token template (copy to `homeapm.env`). |

## Canonical invocation (demo mode) — run from the REPO ROOT

```bash
foundryctl -f casting.yaml -p pours forge         # generate pours/deployment/compose.yaml
bash deploy/seed-token.sh                                 # inject HA token into pours/deployment/homeapm.env
foundryctl -f casting.yaml -p pours cast --no-forge
```

`--no-forge` on the cast is **required**: a plain `cast` re-runs `forge` and
regenerates `pours/`, wiping the injected `homeapm.env`.

## Verified `spec.patches` schema (from `foundryctl gen schemas`)

`spec.patches` is an array of **PatchEntry**. Each entry:

- `target` (string, required) — matched against a generated material via
  `filepath.Match(target, material.Path())`. Exact paths and globs supported.
- `type` (enum `"" | "jsonpatch"`, default `jsonpatch`).
- `operations` (array, `minItems: 1`, required) — RFC 6902 **PatchOperation**s:
  - `op` (required): `add | remove | replace | move | copy | test`
  - `path` (required): RFC 6901 JSON Pointer, e.g. `/services/homeassistant`
  - `value`: value for `add`/`replace`/`test`
  - `from`: JSON Pointer source for `move`/`copy`

Quoted schema description of the target field:

> "target": { "description": "Target output file to patch", "examples":
> ["compose.yaml", "signoz/deployment.yaml", "values.yaml",
> "telemetrystore/telemtrystore-clickhouse-0-*.yaml"] }

`spec.mcp.spec.enabled: true` is verified from the generated
`casting.yaml.lock` (default is `false`) and from the render below — it injects
the `signoz-mcp` service (`signoz/signoz-mcp-server:latest`, port `8000`,
`container_name: signoz-mcp`).

### Cross-platform patch target (IMPORTANT)

`material.Path()` for the compose file is
`filepath.Join(DeploymentDir, "compose.yaml")` — **OS-separator dependent**:

| OS | material path | required `target` |
|----|---------------|-------------------|
| **Windows** (this machine) | `deployment\compose.yaml` | `'deployment\compose.yaml'` |
| **Linux / macOS** | `deployment/compose.yaml` | `deployment/compose.yaml` |

`casting.yaml` currently uses the **Windows backslash** form (single-quoted, so
YAML keeps the literal `\`), because the live stack, `foundryctl`, and the
planned Jul-26 clean-machine test are all Windows/Docker-Desktop. A non-matching
target aborts `forge` fatally (`patch target ... did not match any generated
material`), so both forms can't be listed together — **flip the two `target:`
lines to `deployment/compose.yaml` when casting on Linux/macOS** (e.g. a judge
cloning on Linux). This is the single most important portability caveat. Probed
directly: `'deployment\compose.yaml'` and `'deployment\*.yaml'` match on
Windows; `compose.yaml`, `deployment/compose.yaml`, `*compose.yaml`,
`**/compose.yaml` all fail on Windows.

## Real names discovered from the RUNNING stack

From `docker inspect signoz-ingester-1` labels and the on-disk generated compose
at `C:\Users\abhis\signoz-selfhost\pours\deployment\compose.yaml`:

- Compose **project**: `signoz`
- **Network**: `signoz-network` (compose key and `name:` both `signoz-network`)
- **Ingester service name**: `ingester` (container `signoz-ingester-1`, network
  alias `signoz-ingester`, ports `4317:4317`, `4318:4318`). The sidecar uses
  `OTLP_ENDPOINT=http://ingester:4318`; the alias `http://signoz-ingester:4318`
  resolves equally on `signoz-network`.
- MCP: container `signoz-mcp` on `0.0.0.0:8000->8000`, image
  `signoz/signoz-mcp-server:latest`.
- Other services (for reference): `signoz-signoz-0` (:8080),
  `signoz-metastore-postgres-0`, `signoz-telemetrystore-clickhouse-0-0`,
  `signoz-telemetrykeeper-clickhousekeeper-0`.

## Render proof (forge into a TEMP dir — did NOT touch the running stack)

`foundryctl forge -f casting.yaml -p <tempdir>/pours` produced
`pours/deployment/compose.yaml` containing the patched services:

```yaml
  homeapm-sidecar:
    build:
      context: ../../
      dockerfile: deploy/Dockerfile
    container_name: home-apm-sidecar
    depends_on:
    - homeassistant
    - ingester
    env_file:
    - homeapm.env
    environment:
    - HA_URL=http://homeassistant:8123
    - OTLP_ENDPOINT=http://ingester:4318
    networks:
    - signoz-network
    restart: unless-stopped
  homeassistant:
    container_name: home-apm-homeassistant
    image: homeassistant/home-assistant:stable
    networks:
    - signoz-network
    ports:
    - 8123:8123
    restart: unless-stopped
    volumes:
    - ../../ha-config:/config
```

`signoz-mcp` (`:8000`) and `ingester` (`4317`/`4318`) are also present in the
same rendered file. `forge` only writes files; it never runs `docker compose`
or touches containers (only `cast` does — never run here).

## `casting.yaml.lock` — how it is generated

The `.lock` is the fully-resolved installation: `foundryctl` expands the terse
`casting.yaml` into every molding's concrete `spec` **and** a `status` block
(resolved images, versions, config bodies, and service addresses such as
`otlp: [tcp://signoz-ingester:4318]`). It is emitted next to `casting.yaml` by
the forge/cast pipeline (the example `docs/examples/docker/compose/` ships a
`casting.yaml` + `casting.yaml.lock` pair from `foundryctl gen examples`). The
schema also carries `status.checksum` ("Checksum of the casting file"), so the
lock pins the exact resolved install for bit-identical replays. **We do not
hand-write the lock** — it is produced by running `foundryctl` against
`casting.yaml`; commit the generated pair for replicability.

## Seeded-token flow (feature #7)

The pre-created HA long-lived token lives at `.ha-runtime/token.txt` (183 bytes,
created at demo-record time). It must reach the sidecar's `HA_TOKEN` env at cast
time **without** being baked into `casting.yaml` or the image.

Mechanism — an **env file referenced by the patch**:

1. The sidecar patch sets `env_file: [homeapm.env]`. Docker Compose resolves
   this relative to the compose file's directory (`pours/deployment/`), so it
   looks for `pours/deployment/homeapm.env`.
2. `deploy/seed-token.sh` (run after `forge`, before `cast --no-forge`):
   - **Demo mode**: reads `.ha-runtime/token.txt`, writes
     `deploy/homeapm.env` as `HA_TOKEN=<token>`.
   - **BYOH mode**: user has already created `deploy/homeapm.env` from
     `homeapm.env.example`; the script reuses it.
   - Copies it to `pours/deployment/homeapm.env` next to the generated compose.
3. `cast --no-forge` brings the stack up with the token in the sidecar env.

`homeapm.env` should be git-ignored (contains a secret). The fallback compose
reads `deploy/homeapm.env` directly (its project dir is `deploy/`).

## Unverified until the clean-machine cast test (risk #5)

1. **Relative-path resolution at runtime.** `foundryctl` runs
   `docker compose -f pours/deployment/compose.yaml up -d` with **no**
   `--project-directory` and no explicit cwd. Compose v2's documented default
   makes the project directory the compose file's dir (`pours/deployment/`), so
   `../../ha-config`, `context: ../../`, and `env_file: homeapm.env` resolve
   correctly **only when cast from the repo root with `-p pours`**. If a given
   Compose version resolves relative paths against the caller's cwd instead, the
   `../../` prefixes would need to change. **Verify with a real cast on the
   clean machine.** The paths were verified to render, not to mount.
2. **Cross-platform patch target** (see above) — must flip to
   `deployment/compose.yaml` on Linux/macOS.
3. **HA container writing into `ha-config`.** The seeded `ha-config` is bind
   mounted read-write; a real cast confirms HA boots against it and that
   host↔container file permissions work under Docker Desktop.
4. **Sidecar image build** — the Dockerfile `pip install .` was not built here
   (no real cast). Confirm the build succeeds and `python -m homeapm` starts and
   connects to `homeassistant:8123` / `ingester:4318` on first boot.
5. **`depends_on: ingester`** assumes the patched sidecar and the generated
   ingester share one compose project — true under `cast` (same file), and the
   render confirms both services coexist, but only a live `up` proves ordering.
6. **`.lock` bit-identical replay** — generate and commit the lock, then confirm
   a second machine reproduces it (the Repl.→10 proof).

## Safety

Nothing here started, stopped, reconfigured, or re-cast the live SigNoz stack.
All `forge`/`gen` runs wrote only to temp/scratchpad dirs. `src/`, `ha-config/`,
`fixtures/`, and `.ha-runtime/` were read-only; only `deploy/` was written.
