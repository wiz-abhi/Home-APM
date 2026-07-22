# Home APM - Alerts (`tools/alerts/`)

Three SigNoz alert rules and the notification channel that closes the loop back
into Home Assistant. This is the CORE spec row **"3x v2alpha1 alerts (error-rate,
dead-automation @720h-safe, low-battery) - fire one on demand."**

Everything here is **code**: JSON rule definitions + a channel definition + one
idempotent apply script. Re-running `apply_alerts.py` updates the live objects in
place (no duplicates), so the alerting layer is reproducible from the repo, not
clicked together in the UI.

> **API note.** The spec calls these "v2alpha1" rules. On the actually-deployed
> SigNoz **v0.132.2**, `/api/v2alpha1/rules` is not routed (it falls through to
> the SPA); the live, working alerts API is `POST /api/v1/rules` with the **v5**
> threshold-rule schema (`"version": "v5"`, `condition.compositeQuery`). These
> rules use that verified-live schema. Multiple named thresholds per rule are not
> needed here - each of the three signals is its own single-threshold rule, each
> bound to the required notification channel.

---

## What gets created

| Object | Name / id | What it does |
|---|---|---|
| Channel | `home-assistant-webhook` | Webhook -> `http://host.docker.internal:8123/api/webhook/signoz_alert`. Every SigNoz threshold rule *requires* a channel ("at least one channel is required"). |
| HA automation | `signoz_alert_received` (webhook_id `signoz_alert`) | Turns the alert payload into a `persistent_notification` **inside Home Assistant** - the loop-closer demo beat. |
| Rule | **Automation failing** | `TRACES_BASED`: error-span count (`hasError = true`) `> 0` over 5m. good_night's divide-by-zero template error fires it. |
| Rule | **Automation gone quiet (garage_check)** | `TRACES_BASED`: `alertOnAbsent` on `name = 'Garage Check'` runs, single 10m bucket. Fires when garage_check dies (battery -> 0). |
| Rule | **Low battery** | `METRIC_BASED`: `ha.sensor.value{device_class=battery} < 15` over 5m. |

## Files

```
tools/alerts/
  apply_alerts.py            idempotent create-or-update (login -> channel -> HA automation -> rules)
  channels.json              the webhook notification channel
  ha_webhook_automation.json source the apply script POSTs to HA (/api/config/automation/config)
  ha_webhook_automation.yaml the same automation as YAML (mirrored into ha-config/automations.yaml)
  rules/
    automation_failing.json
    automation_gone_quiet.json
    low_battery.json
  FIRE-ON-DEMAND.md          exact commands to trigger + confirm each alert (for the video)
  README.md                  this file
```

Each `rules/*.json` carries a `_meta` block (op/matchType encoding, demo-vs-prod
window, fire-on-demand command, design rationale). `_meta` and any `_comment`
keys are authoring-only - `apply_alerts.py` strips every `_`-prefixed key before
sending, so they never reach the API.

## Apply

```bash
cd Track3/home-apm
.venv/Scripts/python.exe tools/alerts/apply_alerts.py            # apply all (idempotent)
.venv/Scripts/python.exe tools/alerts/apply_alerts.py --dry-run  # show plan, write nothing
.venv/Scripts/python.exe tools/alerts/apply_alerts.py --skip-ha  # SigNoz side only
```

Config is via env with seeded-demo defaults (`SIGNOZ_BASE`, `SIGNOZ_EMAIL`,
`SIGNOZ_PASSWORD`, `SIGNOZ_ORG_ID`, `HA_BASE`, `HA_TOKEN`). The `HA_TOKEN`
defaults to `.ha-runtime/token.txt`.

## Fire one on demand

See **FIRE-ON-DEMAND.md**. Fastest is **Automation failing**:

```bash
TOKEN=$(cat .ha-runtime/token.txt)
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -X POST http://localhost:8123/api/services/script/turn_on \
  -d '{"entity_id":"script.demo_burst"}'
# ~<=2 min later the rule state is "firing" and a notification pops in HA
```

## Design notes (the honest parts)

- **Where the error lives.** HA runs good_night's failing step with
  `continue_on_error`, so the `ha.automation` **root** span stays green
  (`hasError=false`); the error surfaces on the **child** service-call span
  (`ha.persistent_notification`, statusCode 2). So "Automation failing" counts
  `hasError = true` across the reconstructed trace, not just the root - that is
  the signal that actually reflects a failing automation. In this instance every
  span is an `ha.*` automation span, so `hasError = true` == "an automation is
  failing".
- **Detecting a *dead* automation is not the same as a threshold.** A plain
  "count < 1" can't fire on an *empty* series (nothing to compare), so absence
  needs `alertOnAbsent`. But garage_check runs sparsely (~every 2-3 min), and a
  per-minute count series is mostly empty buckets - `alertOnAbsent` reads those
  as "absent" and **false-fires while the automation is perfectly alive**
  (observed live). The fix shipped here is `stepInterval == evalWindow`: the
  count collapses to a **single bucket** that is non-empty whenever there is
  >=1 run in the window, so `alertOnAbsent` only sees "absent" when garage_check
  is genuinely silent for the full window. Verified: with garage_check alive the
  rule holds `inactive`; on battery-kill it goes `firing`.
- **Demo window vs production window.** "Automation gone quiet" ships with a 10m
  window so it can fire on camera; a real deployment wants `evalWindow: 24h`.
  Only the window/stepInterval/absentFor differ - documented in the rule's
  `_meta` and `labels.production_window`.
- **Reading HA notifications.** HA 2026.7 removed persistent notifications from
  `/api/states`; confirm them via the WS command `persistent_notification/get`
  (snippet in FIRE-ON-DEMAND.md).
