# Fire-on-demand cheat sheet (for the video / a live judge)

Exact commands to trigger each alert on camera, plus how to confirm the alert
fired and how to see the Home Assistant notification pop. Every alert routes to
the **home-assistant-webhook** channel, so a fired alert always ends as a
`persistent_notification` inside Home Assistant (the loop-closer beat).

All commands are copy-paste Git-Bash. HA token lives in
`.ha-runtime/token.txt`. The SigNoz login recipe is at the bottom.

```bash
HA=http://localhost:8123
TOKEN=$(cat .ha-runtime/token.txt)
hh=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
```

---

## 1. "Automation failing"  (fastest - fires in <=2 min)  [PRIMARY on-camera alert]

Rule id `019f8a94-e2cd-7d1d-9600-bf2f1b0317d0` - TRACES_BASED, `hasError = true`
count `> 0` over 5m. `good_night` errors on its own every 2 min, but to force a
fresh batch of error spans right now:

```bash
curl -s "${hh[@]}" -X POST $HA/api/services/script/turn_on \
  -d '{"entity_id":"script.demo_burst"}'
```

`script.demo_burst` triggers all four demo automations; `good_night`'s deliberate
divide-by-zero template error becomes an ERROR span. One eval cycle later
(frequency 1m) the rule flips to `firing`.

## 2. "Low battery"  (fires in ~1-2 min)

Rule id `019f8a94-e377-7861-8681-efa30f39ae8f` - METRIC_BASED,
`ha.sensor.value{device_class=battery} < 15` over 5m. Drop the seeded battery
helper below 15:

```bash
curl -s "${hh[@]}" -X POST $HA/api/services/input_number/set_value \
  -d '{"entity_id":"input_number.garage_battery","value":10}'
```

The sidecar mirrors `input_number.garage_battery -> sensor.garage_door_battery
-> ha.sensor.value{device_class=battery}=10`, which is `< 15` -> fires.
Restore: `... set_value -d '{"entity_id":"input_number.garage_battery","value":55}'`.

## 3. "Automation gone quiet (garage_check)"  (fires ~10-12 min after kill)

Rule id `019f8a94-e327-7670-8298-d71da91aa1f5` - TRACES_BASED, count of
`name = 'Garage Check'` root spans, `alertOnAbsent` over a single 10m bucket.
Kill the battery so the simulator stops nudging it and `garage_check` goes
silent:

```bash
curl -s "${hh[@]}" -X POST $HA/api/services/script/turn_on \
  -d '{"entity_id":"script.kill_garage_battery"}'    # pins battery to 0
```

`garage_check` fires once more on the kill state-change, then never again. ~10 min
later the 10m window empties, `alertOnAbsent` + `absentFor:2` fires ~2 min after
that. (This same kill also fires "Low battery", value 0 < 15.)
Restore: `... script/turn_on -d '{"entity_id":"script.restore_garage_battery"}'`.

> Production note: the shipped rule uses a 10m demo window so it can fire on
> camera. A real deployment would use `evalWindow: 24h` (a health automation
> silent for a day is dead) - only the window/stepInterval/absentFor change.

---

## Confirm an alert FIRED (SigNoz side)

```bash
# get a JWT (see login recipe below), then:
curl -s -H "Authorization: Bearer $JWT" \
  http://localhost:8080/api/v1/rules/<RULE_ID> \
  | python -c "import sys,json;d=json.load(sys.stdin)['data'];r=d.get('rule',d);print('state:',r.get('state'))"
# state: firing   <- fired
```

## Confirm the NOTIFICATION popped (Home Assistant side)

HA 2026.7 does **not** expose persistent notifications in `/api/states`. Read
them over the WebSocket API instead:

```bash
python - "$TOKEN" <<'PY'
import asyncio,json,sys,websockets
async def main():
    async with websockets.connect("ws://localhost:8123/api/websocket") as ws:
        await ws.recv()
        await ws.send(json.dumps({"type":"auth","access_token":sys.argv[1]})); await ws.recv()
        await ws.send(json.dumps({"id":1,"type":"persistent_notification/get"}))
        while True:
            m=json.loads(await ws.recv())
            if m.get("id")==1:
                for n in m["result"]:
                    if str(n.get("notification_id","")).startswith("signoz"):
                        print(n["title"], "->", n["notification_id"])
                return
asyncio.run(main())
PY
```

In the HA UI the same notification appears in the bell / notification drawer as
e.g. **"SigNoz [FIRING]: Automation failing"**.

---

## SigNoz login recipe (JWT for the REST API)

```bash
JWT=$(curl -s -X POST http://localhost:8080/api/v2/sessions/email_password \
  -H "Content-Type: application/json" \
  -d '{"email":"<your-signoz-email>","password":"<your-signoz-password>","orgID":"<your-org-id>"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")
```

`apply_alerts.py` uses this exact recipe internally, so you rarely need it by
hand.
