# Home APM Console

A small web **front door** for Home APM. Home APM deliberately makes SigNoz the
observability UI (flame graphs, dashboards, service map, alerts); this console is
the friendly landing page in front of it:

- **Ask your house** — type *"why did my hallway lights turn on at 3am?"* and get
  one plain-English answer grounded in real trace data (the same
  [`tools/ask`](../ask) pipeline, over HTTP instead of a terminal).
- **Live house** — a table of recent automation runs (name, duration, status,
  a link to each flame graph), auto-refreshing every 15s.
- **Open in SigNoz** — one-click deep links into the dashboard, saved views,
  service map, alerts, and Home Assistant itself.

It is a dependency-light stdlib `http.server` (no web framework) that reuses the
`ask` pipeline, so there is a single source of truth for "ask your house".

## Run it

```bash
# from the repo root, with the venv active and SigNoz + the sidecar running
python tools/console/server.py
# open http://localhost:8090
```

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `CONSOLE_PORT` | `8090` | Console listen port |
| `SIGNOZ_PUBLIC_URL` | `http://localhost:8080` | Browser-facing SigNoz origin for deep links — set to `http://<vm-ip>:8080` on a VM |
| `HA_PUBLIC_URL` | `http://localhost:8123` | Browser-facing Home Assistant origin |
| `MCP_URL` | `http://localhost:8000/mcp` | SigNoz MCP endpoint the server calls |
| `GEMINI_API_KEY` | — | Optional; enables the best natural-language answers (a deterministic heuristic is used without it) |
| `HOMEAPM_REPO_URL` | this repo | Footer link |

## Deploy

The console ships in the one-command install: `casting.yaml` patches a
`homeapm-console` service (built from [`deploy/Dockerfile.console`](../../deploy/Dockerfile.console))
onto the generated compose, exposed on `:8090`. The fallback compose
([`deploy/docker-compose.fallback.yml`](../../deploy/docker-compose.fallback.yml))
includes it too. On a public VM, set `SIGNOZ_PUBLIC_URL` / `HA_PUBLIC_URL` to the
VM's address so the deep links resolve for visitors. This page is the front door
of a deployed install: you land here, ask a question, and jump straight into live
SigNoz data.
