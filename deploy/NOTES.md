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
| `seed-token.sh` | HA token injector (feature #7). Demo / BYOH / bootstrap modes; never exits non-zero. LF endings, no BOM — it must run under Linux `bash`. |
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
| **Linux / macOS** (shipped default) | `deployment/compose.yaml` | `deployment/compose.yaml` |
| **Windows** | `deployment\compose.yaml` | `'deployment\compose.yaml'` |

**`casting.yaml` and `casting.yaml.lock` now ship the forward-slash
(Linux/macOS) form**, because that is what a judge cloning the repo will run.
**Windows users must flip it back** to the single-quoted backslash form
(single-quoted so YAML keeps the literal `\`) in *both* files:
`casting.yaml` `spec.patches[0].target` and `casting.yaml.lock`
`spec.patches[0].target` (~line 393).

There is **no portable single pattern**, for two independent reasons:

1. Go's `filepath.Match` never lets `*` cross a separator, so no glob spans
   both `deployment/compose.yaml` and `deployment\compose.yaml`.
2. On non-Windows, `filepath.Match` treats `\` as an **escape character**, so
   the pattern `deployment\compose.yaml` parses as the literal
   `deploymentcompose.yaml` and matches nothing.

A non-matching target aborts `forge` **fatally**
(`patch target ... did not match any generated material`), so the two forms
also can't be listed side by side — the flip is mandatory, not optional. This
is the single most important portability caveat. Probed directly:
`'deployment\compose.yaml'` and `'deployment\*.yaml'` match on Windows;
`compose.yaml`, `deployment/compose.yaml`, `*compose.yaml`, `**/compose.yaml`
all fail on Windows.

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

`foundryctl -f casting.yaml -p <tempdir>/pours forge` produced
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
    - path: homeapm.env
      required: false
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
`casting.yaml` + `casting.yaml.lock` pair from `foundryctl gen examples`).

**What the lock does and does not guarantee.** It pins the *resolved topology
and config* — every service, its env, its config body, its addresses, and the
image reference each molding resolved to. It does **not** give bit-identical
replays: the committed lock carries **no `checksum` field** (grep it — there is
none), and **image tags track upstream** rather than being pinned by digest:

| image | tag | moves? |
|-------|-----|--------|
| `signoz/signoz` | `latest` | yes |
| `signoz/signoz-otel-collector` | `latest` | yes |
| `signoz/signoz-mcp-server` | `latest` | yes |
| `homeassistant/home-assistant` | `stable` | yes |
| `postgres` | `16` | tracks the 16.x line |
| `clickhouse/clickhouse-keeper` | `25.12.5` | pinned |
| `clickhouse/clickhouse-server` | `25.12.5` | pinned |

So a replay months from now reproduces the same **shape** of install, on
whatever upstream images those tags then point at. Pin by digest if you need
true byte-for-byte reproducibility.

**We do not hand-write the lock** — it is produced by running `foundryctl`
against `casting.yaml`; commit the generated pair for replicability. The one
exception is the `target:` separator flip described above, which must be
applied to *both* files by hand (or re-forged on the target OS).

## Seeded-token flow (feature #7)

> **Read this first if you are cloning the repo.** "Seeded" means **YAML config
> only**. `ha-config/` ships `configuration.yaml`, `automations.yaml`,
> `scripts.yaml`, `input_boolean.yaml`, `input_number.yaml` — the simulated
> house. It does **not** ship `ha-config/.storage/`, which is gitignored (see
> `ha-config/.gitignore`) because it holds the HA auth database. **A fresh
> clone therefore has no user account and no token**, and `.ha-runtime/` (where
> the demo token lives) is gitignored too. You must onboard Home Assistant and
> mint a token by hand. See "First-run bootstrap" below.

The demo-mode token lives at `.ha-runtime/token.txt` (183 bytes, created at
demo-record time, **not committed**). It must reach the sidecar's `HA_TOKEN` env
at cast time **without** being baked into `casting.yaml` or the image.

Mechanism — an **env file referenced by the patch**:

1. The sidecar and console patches set
   `env_file: [{path: homeapm.env, required: false}]`. Docker Compose resolves
   the path relative to the compose file's directory (`pours/deployment/`), so
   it looks for `pours/deployment/homeapm.env`.
2. `deploy/seed-token.sh` (run after `forge`, before `cast --no-forge`):
   - **Demo mode**: reads `.ha-runtime/token.txt`, writes
     `deploy/homeapm.env` as `HA_TOKEN=<token>`.
   - **BYOH mode**: user has already created `deploy/homeapm.env` from
     `homeapm.env.example`; the script reuses it.
   - **Bootstrap mode** (fresh clone, no token anywhere): writes a
     **placeholder** `deploy/homeapm.env` with an empty `HA_TOKEN=` and prints
     the recovery steps. It exits **0** — see below for why.
   - Copies it to `pours/deployment/homeapm.env` next to the generated compose,
     or warns and continues if `pours/deployment/` does not exist (the fallback
     path never runs `forge`).
3. `cast --no-forge` brings the stack up with the token in the sidecar env.

### Why `required: false` and why the script never exits 1

Two failure modes used to deadlock the whole deployment, and both are now
closed:

- **A missing `env_file` is a fatal config-parse error for the ENTIRE Compose
  project**, not just the service that references it. With the old
  `env_file: [homeapm.env]` short syntax, an absent `deploy/homeapm.env` stopped
  **SigNoz, ClickHouse, the OTel collector and the MCP server** from starting —
  not merely the sidecar. The Compose v2.24+ long syntax
  (`- path: homeapm.env` / `required: false`) makes the file optional, so a
  missing token can only degrade the sidecar, never take down the stack.
- **The token can only be minted from a RUNNING Home Assistant**, and HA only
  runs after a successful `cast`. If `seed-token.sh` aborted with `exit 1` when
  no token was found, you could never reach the state in which a token becomes
  obtainable. Hence bootstrap-and-continue instead of abort.

### First-run bootstrap — minting the token by hand

Exactly what a judge on a clean Linux clone has to do:

1. Cast the stack (no token needed now):
   ```bash
   foundryctl -f casting.yaml -p pours forge
   bash deploy/seed-token.sh            # writes the placeholder, exits 0
   foundryctl -f casting.yaml -p pours cast --no-forge
   ```
2. Open Home Assistant at <http://localhost:8123>.
3. Complete the HA **onboarding wizard** — create the user account. (This is
   what populates `ha-config/.storage/`, which is why it cannot be shipped.)
4. In HA, click your user name (bottom left) → **Security** tab →
   **Long-lived access tokens** → **Create token**. Copy it immediately; HA
   shows it exactly once.
5. From the repo root:
   ```bash
   echo "HA_TOKEN=<paste-token-here>" > deploy/homeapm.env
   ```
6. Re-run the injector to publish it next to the generated compose:
   ```bash
   bash deploy/seed-token.sh
   ```
7. Restart the sidecar so it picks up the new env:
   ```bash
   docker restart home-apm-sidecar
   ```
   (also `docker restart home-apm-console` if you added `GEMINI_API_KEY`).

Until step 7 the sidecar starts but logs HA authentication failures; everything
else — SigNoz, ClickHouse, the MCP server, HA itself — is up and healthy.

BYOH users skip steps 1–4: copy `deploy/homeapm.env.example` to
`deploy/homeapm.env`, paste a token from their own Home Assistant, then start
at step 6.

`deploy/homeapm.env` contains a secret and **is git-ignored**: the root
`.gitignore` carries `*.env` with a `!*.env.example` negation, so the seeded
token file is ignored while `deploy/homeapm.env.example` stays tracked. (The
bare `.env` rule alone would not have covered it — it matches only a file named
exactly `.env`.) The fallback compose reads `deploy/homeapm.env` directly (its
project dir is `deploy/`), which is why `seed-token.sh` treats a missing
`pours/deployment/` as a warning rather than an error.

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
2. **Cross-platform patch target** (see above) — the shipped default is the
   Linux/macOS form `deployment/compose.yaml`; **Windows** casts must flip both
   files to `'deployment\compose.yaml'`.
3. **HA container writing into `ha-config`.** The seeded `ha-config` is bind
   mounted read-write; a real cast confirms HA boots against it and that
   host↔container file permissions work under Docker Desktop.
4. **Sidecar image build** — the Dockerfile `pip install .` was not built here
   (no real cast). Confirm the build succeeds and `python -m homeapm` starts and
   connects to `homeassistant:8123` / `ingester:4318` on first boot.
5. **`depends_on: ingester`** assumes the patched sidecar and the generated
   ingester share one compose project — true under `cast` (same file), and the
   render confirms both services coexist, but only a live `up` proves ordering.
6. **`.lock` replay fidelity** — the lock is generated and committed; confirm a
   second machine reproduces the same resolved topology from it. Note this is
   *topology* reproducibility, not bit-identical: four of the seven images ride
   floating `latest`/`stable` tags and the lock carries no checksum (see
   "`casting.yaml.lock` — how it is generated").
7. **`required: false` env_file support** — the long syntax needs **Docker
   Compose v2.24+**. On older Compose the key is rejected as an unknown field.
   Check with `docker compose version`; if it is older, either upgrade or
   revert the six `env_file:` blocks (`casting.yaml` ×2,
   `docker-compose.fallback.yml` ×2, `casting.yaml.lock` ×2) to the short
   `- homeapm.env` form *and* make sure `deploy/homeapm.env` always exists.

## Safety

Nothing here started, stopped, reconfigured, or re-cast the live SigNoz stack.
All `forge`/`gen` runs wrote only to temp/scratchpad dirs. `src/`, `ha-config/`,
`fixtures/`, and `.ha-runtime/` were read-only; only `deploy/` was written.
