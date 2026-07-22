# Home APM add-on

Exports your Home Assistant automation runs to SigNoz (or any OpenTelemetry OTLP
backend) as span-tree flame graphs. The cryptic `conditions/0/conditions/1`
node paths in Home Assistant's built-in trace view become named, clickable
spans; parallel and repeat blocks render correctly; a `wait_for_trigger` that
took 47 seconds is a wide red bar you can see at a glance.

> ⚠️ **UNTESTED ON HOME ASSISTANT OS / SUPERVISOR.** This add-on's structure was
> authored to the documented add-on spec but has **not** been installed or run
> on a real Supervisor. See `addon/README.md`. The tested install path for Home
> APM is the Docker Compose / `foundryctl` cast in `deploy/`. Treat everything
> below as the intended flow, to be validated on a real HAOS box before relying
> on it.

## What you need

- Home Assistant OS or Supervised (add-ons do not exist on Container/Core).
- A reachable SigNoz (or other OTLP/HTTP) endpoint. Self-hosted SigNoz exposes
  OTLP on port `4318`.

## Install (intended flow — validate on HAOS first)

1. **Settings → Add-ons → Add-on Store → ⋮ (top right) → Repositories.**
2. Add this repository's URL and close.
3. Find **Home APM** in the store and click **Install**.
4. Open the **Configuration** tab and set the options (below).
5. **Start** the add-on. Within a few seconds of the next automation run, a
   flame graph appears in SigNoz under service `ha.automation`.

## Options

| Option | Meaning | Example |
|--------|---------|---------|
| `otlp_endpoint` | Where spans are sent (OTLP/HTTP). | `http://a0d7b954-signoz-ingester:4318` |
| `ha_url` | Home Assistant WebSocket/base URL. On HAOS, `http://supervisor/core` uses the add-on's own access. | `http://supervisor/core` |
| `ha_token` | Long-lived access token. **Leave blank** to use the add-on's Supervisor-provided token (preferred). | *(empty)* |
| `mode` | `byoh` for your own house; `seeded` only for the bundled demo volume. | `byoh` |
| `log_level` | Sidecar log verbosity. | `info` |

### Authentication

On HAOS the add-on has `homeassistant_api: true`, so leaving `ha_token` blank
makes `run.sh` use `$SUPERVISOR_TOKEN` — no long-lived token to create or paste.
If you point the add-on at a Home Assistant **outside** this Supervisor, create a
long-lived token (Profile → Security → Long-lived access tokens) and paste it
into `ha_token`.

## Verifying it works

- In SigNoz, open **Traces → Explorer** and filter
  `service.name = 'ha.automation'`. Trigger any automation; its run appears as a
  span waterfall within seconds.
- The add-on **Log** tab prints one `converted run <run_id> -> trace <trace_id>`
  line per automation run — that is the bridge narrating itself.

## What this does and does not measure

Home APM reconstructs each run from Home Assistant's own trace record using the
**real per-element start timestamps** Home Assistant records. Home Assistant does
**not** store a per-step *end* time, so each step's end is inferred as the start
of the next step in scope (and a leaf/terminal step bounds to its parent/run
finish). That is correctly-scoped inference, not a measured end — durations of
individual steps are close but not exact where a step is the last in its scope.
Run-level start/finish and ordering are exact.

## Troubleshooting

- **No traces appear:** check the Log tab for connection errors; confirm
  `otlp_endpoint` is reachable from the add-on and that an automation actually
  ran (Home APM only emits on automation/script runs).
- **Auth errors:** if using an external HA, the pasted `ha_token` may be expired.
- **This is pre-HAOS-test software:** if the install itself fails, that is
  expected until the HAOS validation described in `addon/README.md` is done.
