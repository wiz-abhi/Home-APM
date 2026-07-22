#!/usr/bin/env python
"""Idempotent apply for Home APM alerting.

Creates (or updates in place) everything the "3x alerts" CORE row needs:

  1. A SigNoz notification channel  -> webhook to Home Assistant
     (every SigNoz v0.132 threshold rule REQUIRES a channel).
  2. The Home Assistant "SigNoz Alert Received" webhook automation, which pops a
     persistent_notification INSIDE Home Assistant  -> the loop-closing demo beat.
  3. Three v5 threshold rules:  Automation failing / Automation gone quiet /
     Low battery.

Everything is match-by-name idempotent: re-running updates the existing objects
(PUT) instead of creating duplicates. Pure stdlib + httpx (already a project dep).

Usage
-----
  .venv\\Scripts\\python.exe tools\\alerts\\apply_alerts.py            # apply all
  .venv\\Scripts\\python.exe tools\\alerts\\apply_alerts.py --dry-run  # print, don't write
  .venv\\Scripts\\python.exe tools\\alerts\\apply_alerts.py --skip-ha  # only SigNoz side

Config via env (defaults target the seeded local demo stack):
  SIGNOZ_BASE   (http://localhost:8080)
  SIGNOZ_EMAIL  (user.abhishek2004@gmail.com)
  SIGNOZ_PASSWORD (SigNoz@Warmup2026)
  SIGNOZ_ORG_ID (019f5768-e00c-7dc4-9376-b2b4a44c5e55)
  HA_BASE       (http://localhost:8123)
  HA_TOKEN      (falls back to ../../.ha-runtime/token.txt)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # .../home-apm

SIGNOZ_BASE = os.environ.get("SIGNOZ_BASE", "http://localhost:8080").rstrip("/")
SIGNOZ_EMAIL = os.environ.get("SIGNOZ_EMAIL", "user.abhishek2004@gmail.com")
SIGNOZ_PASSWORD = os.environ.get("SIGNOZ_PASSWORD", "SigNoz@Warmup2026")
SIGNOZ_ORG_ID = os.environ.get("SIGNOZ_ORG_ID", "019f5768-e00c-7dc4-9376-b2b4a44c5e55")
HA_BASE = os.environ.get("HA_BASE", "http://localhost:8123").rstrip("/")


def ha_token() -> str:
    tok = os.environ.get("HA_TOKEN")
    if tok:
        return tok.strip()
    p = REPO / ".ha-runtime" / "token.txt"
    return p.read_text(encoding="utf-8").strip()


def strip_meta(obj: dict) -> dict:
    """Drop authoring-only keys (anything starting with '_') before sending."""
    return {k: v for k, v in obj.items() if not k.startswith("_")}


def login(client: httpx.Client) -> str:
    body = {"email": SIGNOZ_EMAIL, "password": SIGNOZ_PASSWORD, "orgID": SIGNOZ_ORG_ID}
    r = client.post(f"{SIGNOZ_BASE}/api/v2/sessions/email_password", json=body)
    r.raise_for_status()
    return r.json()["data"]["accessToken"]


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #
def ensure_channel(client: httpx.Client, hdr: dict, dry: bool) -> str:
    spec = strip_meta(json.loads((HERE / "channels.json").read_text(encoding="utf-8")))
    name = spec["name"]
    existing = client.get(f"{SIGNOZ_BASE}/api/v1/channels", headers=hdr).json()["data"] or []
    match = next((c for c in existing if c.get("name") == name), None)
    if dry:
        print(f"[dry] channel '{name}' would { 'update' if match else 'create'}")
        return match["id"] if match else "(new)"
    if match:
        cid = match["id"]
        r = client.put(f"{SIGNOZ_BASE}/api/v1/channels/{cid}", headers=hdr, json=spec)
        r.raise_for_status()
        print(f"channel '{name}' updated  id={cid}")
        return cid
    r = client.post(f"{SIGNOZ_BASE}/api/v1/channels", headers=hdr, json=spec)
    r.raise_for_status()
    cid = r.json()["data"]["id"]
    print(f"channel '{name}' created  id={cid}")
    return cid


# --------------------------------------------------------------------------- #
# Home Assistant webhook automation
# --------------------------------------------------------------------------- #
def ensure_ha_automation(dry: bool) -> None:
    doc = json.loads((HERE / "ha_webhook_automation.json").read_text(encoding="utf-8"))
    aid, config = doc["id"], doc["config"]
    tok = ha_token()
    hdr = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    url = f"{HA_BASE}/api/config/automation/config/{aid}"
    if dry:
        print(f"[dry] HA automation '{aid}' would be POSTed to {url}")
        return
    with httpx.Client(timeout=30) as c:
        r = c.post(url, headers=hdr, json=config)
        r.raise_for_status()
        # reload so the live automation registry picks it up immediately
        c.post(f"{HA_BASE}/api/services/automation/reload", headers=hdr)
    print(f"HA automation '{aid}' applied ({r.json().get('result', r.status_code)}) + reloaded")


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def ensure_rule(client: httpx.Client, hdr: dict, path: Path, dry: bool) -> tuple[str, str]:
    spec = strip_meta(json.loads(path.read_text(encoding="utf-8")))
    name = spec["alert"]
    existing = client.get(f"{SIGNOZ_BASE}/api/v1/rules", headers=hdr).json()["data"]["rules"]
    match = next((r for r in existing if r.get("alert") == name), None)
    if dry:
        print(f"[dry] rule '{name}' would { 'update' if match else 'create'}")
        return (match["id"] if match else "(new)", "dry")
    if match:
        rid = match["id"]
        r = client.put(f"{SIGNOZ_BASE}/api/v1/rules/{rid}", headers=hdr, json=spec)
        r.raise_for_status()
        print(f"rule '{name}' updated  id={rid}")
        return rid, "updated"
    r = client.post(f"{SIGNOZ_BASE}/api/v1/rules", headers=hdr, json=spec)
    r.raise_for_status()
    rid = r.json()["data"]["id"]
    print(f"rule '{name}' created  id={rid}")
    return rid, "created"


def metric_exists(name: str) -> bool:
    """Best-effort ClickHouse check so we can flag low_battery 'pending data'."""
    import subprocess

    q = (
        "SELECT count() FROM signoz_metrics.distributed_time_series_v4 "
        f"WHERE metric_name = '{name}'"
    )
    try:
        out = subprocess.run(
            [
                "docker", "exec", "signoz-telemetrystore-clickhouse-0-0",
                "clickhouse-client", "-q", q,
            ],
            capture_output=True, text=True, timeout=20,
        )
        return out.returncode == 0 and out.stdout.strip() not in ("", "0")
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply Home APM alerts (idempotent).")
    ap.add_argument("--dry-run", action="store_true", help="print planned actions, write nothing")
    ap.add_argument("--skip-ha", action="store_true", help="skip the HA webhook automation")
    args = ap.parse_args()

    print(f"SigNoz: {SIGNOZ_BASE}   HA: {HA_BASE}")
    with httpx.Client(timeout=30) as client:
        jwt = login(client)
        hdr = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

        cid = ensure_channel(client, hdr, args.dry_run)

        if not args.skip_ha:
            try:
                ensure_ha_automation(args.dry_run)
            except Exception as e:  # noqa: BLE001
                print(f"WARNING: HA automation step failed ({e}); SigNoz side still applied.")

        rule_ids: dict[str, str] = {}
        for path in sorted((HERE / "rules").glob("*.json")):
            rid, _ = ensure_rule(client, hdr, path, args.dry_run)
            rule_ids[path.stem] = rid

    print("\n==== SUMMARY ====")
    print(f"channel home-assistant-webhook : {cid}")
    for stem, rid in rule_ids.items():
        note = ""
        if stem == "low_battery" and not args.dry_run:
            note = "" if metric_exists("ha.sensor.value") else "  (APPLIED, PENDING DATA: ha.sensor.value not in signoz_metrics yet)"
        print(f"rule {stem:22s} : {rid}{note}")
    print("=================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
