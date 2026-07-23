# Home APM â€” Demo-day runbook (one ordered sequence)

Everything needed to run the ~3.5-minute demo end to end, in order, in **one**
document. Each storyboard beat (spec Â§4, 0:00 â†’ 3:25) lists its exact command or
URL and the expected on-screen result. Consolidates:
`tools/alerts/FIRE-ON-DEMAND.md`, `tools/views/DEMO-LINKS.md`, `tools/ask/README.md`,
and the sidecar start/stop flow.

> **Two shells.** Alert/reset commands are copy-paste **Git-Bash** (they use
> `curl` + heredocs). `ask.py` and Python helpers are shown with the venv
> interpreter. Run from the repo root
> `C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm`.

> **Trace IDs age out** of SigNoz retention. The `/trace/<id>` links below are
> the values at last generation â€” **regenerate right before recording** with
> `python tools/views/make_demo_links.py`, which rewrites
> `tools/views/DEMO-LINKS.md` with fresh IDs. The Explorer/dashboard/service-map
> URLs are stable and do not need regenerating. When in doubt, open the matching
> **saved view** (Beat 0) and click the newest run instead of a hard link.

---

## 0. Pre-flight (do this before you hit record)

**0.1 â€” Confirm the stack is up.**
```bash
docker ps --format '{{.Names}}' | grep -E 'signoz-signoz-0|signoz-ingester|signoz-mcp|home-apm' || echo "STACK NOT FULLY UP"
```
Expected: SigNoz (`:8080`), ingester (`:4318`), `signoz-mcp` (`:8000`), and the
HA container are all listed.

**0.2 â€” Confirm the sidecar is running** (it is the bridge; if it is down, no
new traces flow). It runs as `python -m homeapm`, logging to `sidecar.log`.
```bash
# Is it alive?
ps -W 2>/dev/null | grep -i python | grep -q homeapm && echo "sidecar UP" || echo "sidecar DOWN"
# Tail its narration (one 'converted run ... -> trace ...' line per automation run):
tail -n 5 sidecar.log
```
If **DOWN**, start it (BYOH env already set for the live house):
```bash
# from repo root, backgrounded, logging to sidecar.log
.venv/Scripts/python.exe -m homeapm >> sidecar.log 2>&1 &
```
Give it ~5 s, then re-tail `sidecar.log` for `ws connected` + a `converted run`
line. **Do not stop the sidecar during the demo.**

**0.3 â€” Log in to both UIs** (once; keeps every deep-link one click away).
- SigNoz: <http://localhost:8080> â€” `<your-signoz-email>` / `<your-signoz-password>`
- Home Assistant: <http://localhost:8123> â€” `homeapm` / `<your-ha-password>`

**0.4 â€” Regenerate fresh deep-links + set the time picker.**
```bash
.venv/Scripts/python.exe tools/views/make_demo_links.py   # refreshes /trace/<id> links
```
Set the SigNoz time-range picker to **Last 3 hours** for every Explorer /
Services / Service-map view so all seeded runs are in frame.

**0.5 â€” Warm the data** (guarantees a fresh run of all four automations is in
the last few minutes, so nothing looks stale on camera):
```bash
HA=http://localhost:8123
TOKEN=$(cat .ha-runtime/token.txt)
hh=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
curl -s "${hh[@]}" -X POST $HA/api/services/script/turn_on -d '{"entity_id":"script.demo_burst"}'
```
`script.demo_burst` fires all four demo automations; watch `sidecar.log` for four
fresh `converted run` lines. (This same burst is also how Beat 5's error alert is
armed â€” see Â§Beat 5.)

**0.6 â€” Pre-test the MCP beat** (it is the one flaky beat; test it now so you can
decide to keep or cut Beat 3). Needs `GEMINI_API_KEY` (already set in env):
```bash
.venv/Scripts/python.exe tools/ask/ask.py "why did my hallway lights turn on at 3am?"
```
Expected: one English sentence naming the silently-passing branch + a `trace_id`
and flame-graph URL, in ~2â€“4 s. If it errors or stalls, **cut Beat 3** at record
time â€” the video is unaffected and UX still lands at 9.

---

## Beat 0:00 â€” Cold open + split-screen (#1)

*Line:* "My hallway lights turned on at 3am and I have no idea why."

- **LEFT (the pain):** Home Assistant's own trace view.
  <http://localhost:8123> â†’ Settings â†’ Automations â†’ **Hallway Lights 3AM** â†’
  â‹® â†’ **Traces**. Show the cryptic node-path rows
  (`conditions/0/conditions/1/conditions/0`).
- **RIGHT (the fix):** the same run as a named SigNoz waterfall. Open the
  **saved views hub** and pick **3am mystery**:
  <http://localhost:8080/traces/saved-views>

Expected: side by side, cryptic strings on the left vs named, clickable spans on
the right.

## Beat 0:30 â€” The reveal

*Line:* "There it is."

- Pre-filtered Trace Explorer, **3am mystery** view
  (`automation.name = 'Hallway Lights 3AM' AND kind_string = 'Server'`). Either
  click the saved view from Beat 0, or the newest specific run:
  <http://localhost:8080/trace/bdbb84531d9f821da530eb8922c76fb2>
  *(regenerate; open the newest "Hallway Lights 3AM" run if the ID aged out).*

Expected: the 3am run â€” a sun trigger fired a `choose` branch whose condition
**silently passed**. Point at the taken branch.

## Beat 1:00 â€” Logs â†” traces (#13, keep if landed)

*Line:* "The log line and the trace are one click apart."

- Sidecar logs saved view: <http://localhost:8080/logs/saved-views> â†’ **Home APM
  sidecar logs**, or the pre-filtered Logs Explorer (`service.name = 'ha.sidecar'`)
  linked in `tools/views/DEMO-LINKS.md` (Beat 1.5).

Expected: `converted run <run_id> -> trace <trace_id>` INFO lines â€” the bridge
narrating itself â€” with the `trace_id` linking back to the flame graph.

## Beat 1:20 â€” Latency (Beat 2)

*Line:* the villain is visually obvious.

- Slowest **Morning Routine** run:
  <http://localhost:8080/trace/bd50a563ef4ad6e4442fc2cbe5aeb873>
  *(regenerate if aged out).*

Expected: a `wait_for_trigger` span **~47 s wide and red**, dominating the
waterfall.

## Beat 1:45 â€” Parallel / repeat + error (Beat 2.5, #12b)

*Line:* "This is a real tracer â€” this is the #1 question every naive HA-trace
reader gets wrong."

- **Good Night** run *with* the error span:
  <http://localhost:8080/trace/63ee82df13d5059791705f6150795099>
  *(regenerate; or query the newest `good_night` run with an ERROR span).*

Expected: the `parallel` block = two **overlapping bars** (one visibly slower); a
`repeat` loop = stacked iteration spans; the template action = a red **ERROR**
span (`ZeroDivisionError: division by zero`).

## Beat 2:15 â€” Ask your house (Beat 3, #8) â€” CUTTABLE

*Line:* type the question; get one English sentence, then cut to the flame graph.

```bash
.venv/Scripts/python.exe tools/ask/ask.py "why did my hallway lights turn on at 3am?"
```
Optional follow-ups (all verified):
```bash
.venv/Scripts/python.exe tools/ask/ask.py "why is my morning routine slow?"
.venv/Scripts/python.exe tools/ask/ask.py "did anything fail tonight?"
```
Expected: one grounded sentence naming the silently-passing `choose` branch
(hallway) / the ~53 s `wait_for_trigger` (morning) / the `good_night` template
error (did-anything-fail), plus a `trace_id` + flame-graph URL. **If it stalled
in Â§0.6, skip this beat entirely.**

## Beat 2:45 â€” The board + house service map (Beat 4, #9 / #15)

*Line:* pick a room, every panel refocuses; click the slow bar, drop into its
flame graph.

- Home APM dashboard:
  <http://localhost:8080/dashboard/019f8a8f-d7f4-77dd-a5b8-b69d2a7fad3b>
  â€” English panel titles; change the **`$room`** variable (e.g. to **Bedroom**)
  and show every panel refocus; click the slow-automation bar â†’ its flame graph.
- Services (RED metrics, all 7 `ha.*` services): <http://localhost:8080/services>
- House service map: <http://localhost:8080/service-map>
  (set range **Last 3h**; the `ha.automation â†’ ha.persistent_notification` edge
  shows ~70% errors).

## Beat 3:10 â€” The alert (Beat 5)

*Line:* "Garage automation dead 26h" â€” the loop closes back inside Home Assistant.

**Primary on-camera alert = "Automation failing"** (fastest, fires â‰¤2 min). Arm
it with the same burst from Â§0.5 (do it ~1â€“2 min before this beat):
```bash
curl -s "${hh[@]}" -X POST $HA/api/services/script/turn_on -d '{"entity_id":"script.demo_burst"}'
```
`good_night`'s deliberate template error becomes ERROR spans; rule
`019f8a94-e2cd-7d1d-9600-bf2f1b0317d0` (traces-based, `hasError`, eval 1m) flips
to `firing` one cycle later.

- Show it firing: <http://localhost:8080/alerts?tab=Triggered%20Alerts>
- Show the notification **inside Home Assistant**: the bell / notification drawer
  at <http://localhost:8123> shows **"SigNoz [FIRING]: Automation failing"**
  (every alert routes to the `home-assistant-webhook` channel â†’ a
  `persistent_notification` in HA).

*Alternate alerts (if you want the literal "dead automation" story on camera â€”
needs ~10â€“12 min lead time, so pre-arm before recording):*
```bash
# kills the garage battery so garage_check goes silent -> alertOnAbsent fires ~10-12 min later
curl -s "${hh[@]}" -X POST $HA/api/services/script/turn_on -d '{"entity_id":"script.kill_garage_battery"}'
```
Full alert catalogue, rule IDs, and confirmation commands: `tools/alerts/FIRE-ON-DEMAND.md`.

## Beat 3:25 â€” Close (#6)

*Line:* "Home Assistant has always had traces â€” it just never let anyone see
them. SigNoz does."

- Flash the one-command install and the deep-link it prints. The
  tested/canonical install path (repo root):
  ```bash
  foundryctl -f casting.yaml -p pours forge
  bash deploy/seed-token.sh
  foundryctl -f casting.yaml -p pours cast --no-forge
  ```
  Then open the pre-filtered dashboard deep-link (Beat 4 URL) â€” it is already
  alive. *(Do **not** run a real `cast` against the live demo stack mid-record;
  this beat is the command + the already-live dashboard, per `deploy/NOTES.md`.)*

---

## Reset / teardown (after recording)

Restore anything the demo perturbed:
```bash
HA=http://localhost:8123
TOKEN=$(cat .ha-runtime/token.txt)
hh=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

# restore the garage battery (undoes Beat 5 alternate + low-battery)
curl -s "${hh[@]}" -X POST $HA/api/services/script/turn_on -d '{"entity_id":"script.restore_garage_battery"}'
# or set it directly back above threshold
curl -s "${hh[@]}" -X POST $HA/api/services/input_number/set_value -d '{"entity_id":"input_number.garage_battery","value":55}'
```
Leave the sidecar and the stack running (they self-heal and keep producing fresh
demo data). Nothing in this runbook re-casts or reconfigures the live SigNoz
stack.

---

## Appendix â€” confirmation & login recipes

**Confirm an alert fired (SigNoz REST).** Get a JWT, then read the rule state:
```bash
JWT=$(curl -s -X POST http://localhost:8080/api/v2/sessions/email_password \
  -H "Content-Type: application/json" \
  -d '{"email":"<your-signoz-email>","password":"<your-signoz-password>","orgID":"<your-org-id>"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")

curl -s -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/rules/<RULE_ID> \
  | python -c "import sys,json;d=json.load(sys.stdin)['data'];r=d.get('rule',d);print('state:',r.get('state'))"
# state: firing   <- fired
```

**Confirm the HA notification popped** (HA 2026.7 hides persistent notifications
from `/api/states` â€” read them over the WebSocket API): see the ready-to-run
Python snippet in `tools/alerts/FIRE-ON-DEMAND.md` ("Confirm the NOTIFICATION
popped").

**Credentials & endpoints** (also in the project context):
| Surface | URL | Login |
|---|---|---|
| SigNoz UI | http://localhost:8080 | <your-signoz-email> / <your-signoz-password> |
| Home Assistant | http://localhost:8123 | homeapm / <your-ha-password> |
| SigNoz MCP | http://localhost:8000/mcp | service-account key baked into `signoz-mcp` |
| HA token (for curl) | â€” | `.ha-runtime/token.txt` |

**Source docs this runbook consolidates:** `tools/alerts/FIRE-ON-DEMAND.md`
(alerts) Â· `tools/views/DEMO-LINKS.md` (deep-links) Â· `tools/ask/README.md`
(ask.py) Â· `deploy/NOTES.md` (install/close).
