# Home APM dashboard

The **Home APM** SigNoz dashboard, defined as code so it ships and re-applies
reproducibly. English question-titles, seven panels, and a `$room` selector.

## One command

```bash
# from the repo root, with the project venv
python tools/dashboard/apply_dashboard.py --verify
```

That builds the dashboard, writes [`dashboard.json`](./dashboard.json), creates it
(or updates it in place if it already exists â€” idempotent, keyed by title), and then
runs every trace panel through the SigNoz `query_range` API and prints row counts.

Open it at `http://<signoz>/dashboard/<uuid>` (the script prints the UUID).

## Configuration (env, all optional)

| var | default | meaning |
|-----|---------|---------|
| `SIGNOZ_URL` | `http://localhost:8080` | SigNoz base URL |
| `SIGNOZ_EMAIL` | `<your-signoz-email>` | login email |
| `SIGNOZ_PASSWORD` | `<your-signoz-password>` | login password |
| `SIGNOZ_ORG_ID` | `019f5768-â€¦-b2b4a44c5e55` | org id (this instance) |

Login recipe: `POST /api/v2/sessions/email_password` with `{email, password, orgID}`
â†’ `data.accessToken` used as `Bearer`. Dashboards go through the **v1** REST API
(`/api/v1/dashboards`), which is the version verified working end-to-end on this
instance; panel verification uses **v5** `query_range`.

## Flags

- `--dry-run` â€” build + write `dashboard.json` only, no network.
- `--verify` â€” after applying, execute each trace panel via `query_range`.
- (no flag) â€” build + write + create/update.

## Panels

1. **How often are my automations running?** â€” count of root spans over time, by `automation.name`.
2. **Which automations are slowest?** â€” p95 run duration by `automation.name` (bar).
3. **Are any automations failing?** â€” error-count over time (`has_error = true`), by `automation.name`.
4. **What is my house doing right now?** â€” recent automation runs (name, duration, status).
5. **Room climate** â€” `ha.sensor.value` where `device_class = temperature`.
6. **Battery health** â€” `ha.sensor.value` where `device_class = battery`, with a 20% low-battery line.
7. **Is the bridge healthy?** â€” `homeapm.ws.connected` + `homeapm.traces.converted.total`.

Trace panels (1â€“4) filter `serviceName = ha.automation` (root automation-run spans).
Metric panels (5â€“7) are built against the frozen Home APM metric contract and read
**no data** until the sidecar starts emitting â€” that is expected, not a bug.

## The `$room` variable

A **DYNAMIC** dashboard variable sourced from the `automation.room` span attribute
(`dynamicVariablesSource: Traces`). Trace panels carry `automation.room IN [$room]`
in their filter, so picking a room refocuses every trace panel at once.
