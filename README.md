<!-- CI badge: replace OWNER/REPO once a remote exists. -->
<!-- ![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg) -->

# Home APM

**Home Assistant has always had automation traces — it just never let anyone
see them.** Home APM is a Python sidecar that subscribes to Home Assistant's
WebSocket API, pulls each automation run's raw `trace/get` payload, reconstructs
it into an OpenTelemetry span tree (with real per-element start timestamps), and
exports it to a self-hosted SigNoz over OTLP. Every automation run becomes a
legible flame graph — the cryptic `conditions/0/conditions/1/conditions/0` node
paths turn into named, clickable spans — plus a handful of sensor gauges,
dashboards, and alerts. Home Assistant has **no native OpenTelemetry trace
export**; this is that missing export.

## Architecture

```
 Home Assistant                     Home APM sidecar (this repo)                 SigNoz
 ┌──────────────┐   WS auth +    ┌───────────────────────────────────┐   OTLP  ┌──────────┐
 │ automation   │  subscribe_    │  ws_client  ── raw trace/get ──▶   │  HTTP   │ traces   │
 │ engine       │  events +      │      │                            │  :4318  │ metrics  │
 │  trace/get   │──trace/get────▶│      ▼                            │────────▶│ logs     │
 │ state_changed│                │  trace_reconstruct  (PURE fn:      │         │ dashboards│
 └──────────────┘                │    payload dict → [SpanSpec])      │         │ alerts   │
                                 │      │                            │         │ service  │
                                 │      ▼                            │         │   map    │
                                 │  otlp_emit  (mints run_id→trace_id,│         └──────────┘
                                 │   CLIENT/SERVER kinds → service map)│
                                 │  metrics · selfobs                 │
                                 └───────────────────────────────────┘
```

The reconstruction (`src/homeapm/trace_reconstruct.py`) is a pure, I/O-free
function: `trace/get` payload dict → `list[SpanSpec]`. That is what makes it
golden-testable offline — **clone and `pytest`, no house required.**

## Quickstart

> Placeholder — finalized once the casting/onboarding agents land.

```bash
# 1. install (Python 3.11)
pip install -e ".[dev]"

# 2. run the offline golden tests (no Home Assistant needed)
pytest

# 3. seeded demo (zero-config) — TODO: casting.yaml install path
#    BYOH: set HA_URL / HA_TOKEN / OTLP_ENDPOINT and `python -m homeapm`
```

Configuration is environment-driven: `HA_URL`, `HA_TOKEN`, `OTLP_ENDPOINT`,
`HOMEAPM_MODE` (`seeded` | `byoh`). Secrets never get committed — tokens live in
the gitignored `.ha-runtime/` directory or the environment.

## Development

```bash
ruff check src tests      # lint
ruff format src tests     # format
mypy                      # strict type-check
pytest                    # tests (skip cleanly with no fixtures)
```

CI runs all four on every push (`.github/workflows/ci.yml`).

## AI-usage disclosure

This project was built solo with heavy AI assistance and that is disclosed by
design, per the hackathon rules (mandatory — omission is a disqualification).
Anthropic's Claude (Claude Code) was used to: research the Home Assistant trace
internals and prior art, draft this repository scaffold and code skeletons,
implement and test the span-reconstruction algorithm, and draft documentation
and the submission blog. Every AI-produced artifact was reviewed, edited, and
verified by the author against primary sources and a live SigNoz stack before
inclusion. The design decisions, the frozen span schema, and all claims about
what the tool does are the author's own and were checked against real payloads.

## Prior art (cite, don't hide)

Home APM is not the first project to move Home Assistant data toward
observability tooling, and it deliberately does something different:

- **`ha-kafka-net`** — bridges Home Assistant to .NET via Kafka for building
  automations in code. Adjacent in spirit (get HA data into real infrastructure)
  but a different problem: it is an automation framework, not a trace exporter,
  and does not reconstruct HA's native automation traces into OTel spans.
- **`detektr`** — a home-monitoring / detection project in the HA ecosystem.
  Adjacent domain (observing the home) but not an OpenTelemetry export of the
  automation *trace* engine.

Home APM's specific contribution is reconstructing Home Assistant's own
`trace/get` node-path payloads — the data the built-in trace viewer already has
but renders unusably — into standards-compliant OTLP span trees on SigNoz.

## License

MIT.
