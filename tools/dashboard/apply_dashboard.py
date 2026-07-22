#!/usr/bin/env python3
"""Build + apply the "Home APM" SigNoz dashboard (idempotent create-or-update).

This is the *replicable* dashboard artifact for Home APM: the dashboard is defined
as code here, exported to ``dashboard.json`` for the record, and pushed to a live
SigNoz instance over the (verified-working) v1 dashboards REST API.

Panels are English question-titles. Trace panels filter ``serviceName = ha.automation``
(root automation-run spans). Metric panels are built against the FROZEN METRIC
CONTRACT (``ha.sensor.value`` / ``ha.entity.state`` / ``homeapm.ws.connected`` /
``homeapm.traces.converted.total``) and may read "no data" until the sidecar emits.

A ``$room`` DYNAMIC dashboard variable (sourced from the ``automation.room`` span
attribute) is wired into every trace panel.

Environment (all optional, sane defaults for the local hackathon stack):
  SIGNOZ_URL       default http://localhost:8080
  SIGNOZ_EMAIL     default user.abhishek2004@gmail.com
  SIGNOZ_PASSWORD  default SigNoz@Warmup2026
  SIGNOZ_ORG_ID    default 019f5768-e00c-7dc4-9376-b2b4a44c5e55  (this instance's org)

Usage:
  python apply_dashboard.py            # build -> write dashboard.json -> create/update live
  python apply_dashboard.py --dry-run  # build -> write dashboard.json only (no network)
  python apply_dashboard.py --verify   # after apply, run each trace panel via query_range
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DASHBOARD_JSON = HERE / "dashboard.json"

URL = os.environ.get("SIGNOZ_URL", "http://localhost:8080").rstrip("/")
EMAIL = os.environ.get("SIGNOZ_EMAIL", "user.abhishek2004@gmail.com")
PASSWORD = os.environ.get("SIGNOZ_PASSWORD", "SigNoz@Warmup2026")
ORG_ID = os.environ.get("SIGNOZ_ORG_ID", "019f5768-e00c-7dc4-9376-b2b4a44c5e55")

TITLE = "Home APM"
AUTOMATION_SERVICE = "ha.automation"

# Stable namespace so widget / variable IDs are deterministic across rebuilds.
# Deterministic IDs make dashboard.json reproducible AND let the update path be a
# clean in-place edit instead of a delete+recreate (SigNoz forbids multi-panel delete).
_NS = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _det(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))

# ---------------------------------------------------------------------------
# Query-builder fragments
# ---------------------------------------------------------------------------


def _trace_group_by(*keys: str) -> list[dict]:
    return [
        {"dataType": "string", "isColumn": False, "key": k, "type": "tag"} for k in keys
    ]


def trace_query(
    name: str,
    filter_expr: str,
    legend: str,
    aggregation_expr: str,
    group_by: list[str] | None = None,
    order_by: list[dict] | None = None,
) -> dict:
    """A v5 trace query in the shape the v1 dashboard widget schema stores."""
    return {
        "dataSource": "traces",
        "queryName": name,
        "aggregations": [{"expression": aggregation_expr}],
        "filter": {"expression": filter_expr},
        "filters": {"items": [], "op": "AND"},
        "expression": name,
        "disabled": False,
        "stepInterval": 60,
        "having": [],
        "limit": None,
        "orderBy": order_by or [],
        "groupBy": _trace_group_by(*group_by) if group_by else [],
        "legend": legend,
        "reduceTo": "avg",
        "functions": [],
    }


def metric_query(
    name: str,
    metric_name: str,
    filter_expr: str,
    legend: str,
    time_aggregation: str = "avg",
    space_aggregation: str = "avg",
    group_by: list[str] | None = None,
) -> dict:
    return {
        "dataSource": "metrics",
        "queryName": name,
        "aggregations": [
            {
                "metricName": metric_name,
                "spaceAggregation": space_aggregation,
                "temporality": "unspecified",
                "timeAggregation": time_aggregation,
            }
        ],
        "filter": {"expression": filter_expr},
        "filters": {"items": [], "op": "AND"},
        "expression": name,
        "disabled": False,
        "stepInterval": 60,
        "having": {"expression": ""},
        "limit": None,
        "orderBy": [],
        "groupBy": _trace_group_by(*group_by) if group_by else [],
        "legend": legend,
        "reduceTo": "avg",
        "functions": [],
    }


def widget(
    title: str,
    description: str,
    query_data: list[dict],
    panel_type: str = "graph",
    unit: str = "",
    thresholds: list[dict] | None = None,
    is_stacked: bool = False,
    columns: list[dict] | None = None,
) -> dict:
    wid = _det("widget", title)
    w = {
        "id": wid,
        "title": title,
        "description": description,
        "panelTypes": panel_type,
        "isStacked": is_stacked,
        "nullZeroValues": "zero",
        "opacity": "1",
        "fillSpans": False,
        "yAxisUnit": unit,
        "softMax": None,
        "softMin": None,
        "selectedLogFields": [],
        "selectedTracesFields": columns or [],
        "thresholds": thresholds or [],
        "query": {
            "queryType": "builder",
            "builder": {"queryData": query_data, "queryFormulas": []},
            "promql": [{"name": "A", "query": "", "legend": "", "disabled": False}],
            "clickhouse_sql": [
                {"name": "A", "legend": "", "disabled": False, "query": ""}
            ],
            "id": _det("query", title),
        },
        "timePreferance": "GLOBAL_TIME",
    }
    return w


def threshold(value: float, label: str, unit: str, color: str = "#F5325B") -> dict:
    return {
        "index": _det("threshold", label),
        "keyIndex": 0,
        "moveThreshold": False,
        "selectedGraph": "graph",
        "thresholdColor": color,
        "thresholdFormat": "Text",
        "thresholdLabel": label,
        "thresholdOperator": "<",
        "thresholdTableOptions": "",
        "thresholdUnit": unit,
        "thresholdValue": value,
    }


# ---------------------------------------------------------------------------
# The dashboard definition
# ---------------------------------------------------------------------------

# The $room DYNAMIC variable clause, appended to every trace panel filter.
# When a room is selected the frontend expands $room into a quoted list.
ROOT = f"serviceName = '{AUTOMATION_SERVICE}'"
ROOT_ROOM = f"{ROOT} AND automation.room IN [$room]"
# Errors surface on service-call CHILD spans (e.g. ha.persistent_notification), not the
# ha.automation root, so the failing panel matches any ha.* span. Children carry
# automation.name / automation.room too, so grouping and the $room filter still work.
ANY_HA_ROOM = "serviceName LIKE 'ha.%' AND automation.room IN [$room]"


def build_variables() -> dict:
    vid = _det("variable", "room")
    return {
        vid: {
            "id": vid,
            "key": vid,
            "name": "room",
            "description": "Filter every panel by room (from the automation.room span attribute).",
            "type": "DYNAMIC",
            "dynamicVariablesAttribute": "automation.room",
            "dynamicVariablesSource": "Traces",
            "sort": "ASC",
            "multiSelect": True,
            "showALLOption": True,
            "allSelected": True,
            "selectedValue": None,
            "customValue": "",
            "queryValue": "",
            "textboxValue": "",
            "defaultValue": "",
            "order": 0,
            "modificationUUID": _det("variable-mod", "room"),
        }
    }


def build_widgets() -> tuple[list[dict], list[dict]]:
    widgets: list[dict] = []

    specs = [
        # (title, description, panel_type, unit, query_data, thresholds, columns)
        (
            "How often are my automations running?",
            "Count of automation runs (root spans) over time, grouped by automation.",
            "graph",
            "",
            [trace_query("A", ROOT_ROOM, "{{automation.name}}", "count()",
                         group_by=["automation.name"])],
            None,
            None,
        ),
        (
            "Which automations are slowest?",
            "p95 end-to-end run duration by automation.",
            "bar",
            "ns",
            [trace_query("A", ROOT_ROOM, "{{automation.name}}", "p95(duration_nano)",
                         group_by=["automation.name"])],
            None,
            None,
        ),
        (
            "Are any automations failing?",
            "Error-count over time (spans with status ERROR), by automation.",
            "graph",
            "",
            [trace_query("A", f"{ANY_HA_ROOM} AND has_error = true",
                         "{{automation.name}}", "count()", group_by=["automation.name"])],
            None,
            None,
        ),
        (
            "What is my house doing right now?",
            "Most recent automation runs: name, duration and status.",
            "list",
            "",
            [trace_query(
                "A", ROOT_ROOM, "recent runs", "count()",
                order_by=[{"columnName": "timestamp", "order": "desc"}],
            )],
            None,
            [
                {"name": "automation.name", "type": "tag", "dataType": "string"},
                {"name": "durationNano", "type": "", "dataType": ""},
                {"name": "hasError", "type": "", "dataType": ""},
            ],
        ),
        (
            "Room climate",
            "Temperature sensors (ha.sensor.value where device_class = temperature).",
            "graph",
            "celsius",
            [metric_query("A", "ha.sensor.value", "device_class = 'temperature'",
                          "{{friendly_name}}", group_by=["friendly_name", "room"])],
            None,
            None,
        ),
        (
            "Battery health",
            "Battery levels (ha.sensor.value where device_class = battery). Line at 20%.",
            "graph",
            "percent",
            [metric_query("A", "ha.sensor.value", "device_class = 'battery'",
                          "{{friendly_name}}", group_by=["friendly_name"])],
            [threshold(20, "Low battery", "percent")],
            None,
        ),
        (
            "Is the bridge healthy?",
            "Sidecar liveness: WS connection (0/1) and total traces converted.",
            "graph",
            "",
            [
                metric_query("A", "homeapm.ws.connected", "", "ws connected",
                             time_aggregation="latest", space_aggregation="max"),
                metric_query("B", "homeapm.traces.converted.total", "",
                             "traces converted", time_aggregation="increase",
                             space_aggregation="sum"),
            ],
            None,
            None,
        ),
    ]

    for _title, _desc, _ptype, _unit, _qd, _thr, _cols in specs:
        # metric query B in the last panel needs expression "B"
        for i, q in enumerate(_qd):
            q["queryName"] = chr(ord("A") + i)
            q["expression"] = chr(ord("A") + i)
        widgets.append(
            widget(_title, _desc, _qd, panel_type=_ptype, unit=_unit,
                   thresholds=_thr, columns=_cols)
        )

    # 2-column grid layout, height 6 each.
    layout = []
    for i, w in enumerate(widgets):
        layout.append({
            "i": w["id"], "x": (i % 2) * 6, "y": (i // 2) * 6,
            "w": 6, "h": 6, "moved": False, "static": False,
        })
    return widgets, layout


def build_dashboard() -> dict:
    widgets, layout = build_widgets()
    return {
        "title": TITLE,
        "description": (
            "Home APM - Home Assistant automations as OTLP traces. Trace panels read "
            "root automation-run spans (serviceName = ha.automation); metric panels use "
            "the frozen Home APM metric contract. Use the $room selector to focus a room."
        ),
        "tags": ["home-apm", "home-assistant", "track3", "traces"],
        "layout": layout,
        "widgets": widgets,
        "variables": build_variables(),
        "version": "v4",
    }


# ---------------------------------------------------------------------------
# SigNoz REST
# ---------------------------------------------------------------------------


def login() -> str:
    r = httpx.post(
        f"{URL}/api/v2/sessions/email_password",
        json={"email": EMAIL, "password": PASSWORD, "orgID": ORG_ID},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]["accessToken"]


def find_existing(headers: dict, title: str) -> str | None:
    r = httpx.get(f"{URL}/api/v1/dashboards", headers=headers, timeout=30)
    r.raise_for_status()
    for d in r.json().get("data", []):
        data = d.get("data", d)
        if data.get("title") == title:
            return d.get("id") or d.get("uuid")
    return None


def _remap_onto_existing(body: dict, headers: dict, did: str) -> None:
    """Reassign new widgets onto the existing dashboard's panel IDs (by index).

    SigNoz's update endpoint forbids deleting more than one panel at once, so a
    rebuild that mints different widget IDs is rejected. Reusing the live panel IDs
    turns the update into a pure in-place edit no matter what IDs are on either side.
    """
    r = httpx.get(f"{URL}/api/v1/dashboards/{did}", headers=headers, timeout=30)
    r.raise_for_status()
    cur = r.json()["data"]
    cur = cur.get("data", cur)
    old_ids = [w["id"] for w in cur.get("widgets", [])]
    for i, w in enumerate(body["widgets"]):
        if i < len(old_ids):
            new_id = old_ids[i]
            body["layout"][i]["i"] = new_id
            w["id"] = new_id


def apply(body: dict) -> str:
    tok = login()
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    existing = find_existing(headers, body["title"])
    if existing:
        _remap_onto_existing(body, headers, existing)
        r = httpx.put(
            f"{URL}/api/v1/dashboards/{existing}", headers=headers, json=body, timeout=30
        )
        action = "updated"
    else:
        r = httpx.post(
            f"{URL}/api/v1/dashboards", headers=headers, json=body, timeout=30
        )
        action = "created"
    if r.status_code >= 300:
        print(f"ERROR {r.status_code}: {r.text[:800]}", file=sys.stderr)
        r.raise_for_status()
    data = r.json()["data"]
    did = data.get("id") or data.get("uuid")
    print(f"{action} dashboard '{body['title']}' -> {did}")
    return did


def verify(did: str) -> None:
    """Round-trip the dashboard and run each trace panel via query_range."""
    tok = login()
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    r = httpx.get(f"{URL}/api/v1/dashboards/{did}", headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    data = data.get("data", data)
    print(f"GET round-trip OK: {len(data['widgets'])} widgets, "
          f"{len(data.get('variables', {}))} variable(s)")

    now = int(time.time() * 1000)
    start = now - 7 * 86400 * 1000
    for w in data["widgets"]:
        q0 = w["query"]["builder"]["queryData"][0]
        if q0["dataSource"] != "traces":
            print(f"  [metric] {w['title']!r}: skipped (needs sidecar)")
            continue
        # substitute $room -> all-match for the standalone check
        expr = q0["filter"]["expression"].replace("automation.room IN [$room]",
                                                   "automation.room EXISTS")
        spec = {
            "name": "A", "signal": "traces", "disabled": False, "stepInterval": 60,
            "aggregations": q0["aggregations"],
            "filter": {"expression": expr},
        }
        if q0["groupBy"]:
            spec["groupBy"] = [
                {"name": g["key"], "fieldDataType": "string", "fieldContext": "span"}
                for g in q0["groupBy"]
            ]
        payload = {
            "schemaVersion": "v1", "start": start, "end": now, "requestType": "scalar",
            "compositeQuery": {"queries": [{"type": "builder_query", "spec": spec}]},
        }
        rr = httpx.post(f"{URL}/api/v5/query_range", headers=headers, json=payload,
                        timeout=30)
        try:
            rows = rr.json()["data"]["data"]["results"][0]["data"]
            print(f"  [trace ] {w['title']!r}: {len(rows)} row(s) -> {rows[:6]}")
        except Exception:
            print(f"  [trace ] {w['title']!r}: {rr.status_code} {rr.text[:160]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build + write json only")
    ap.add_argument("--verify", action="store_true", help="verify panels after apply")
    args = ap.parse_args()

    body = build_dashboard()
    DASHBOARD_JSON.write_text(json.dumps(body, indent=2), encoding="utf-8")
    print(f"wrote {DASHBOARD_JSON} ({len(body['widgets'])} panels)")

    if args.dry_run:
        return

    did = apply(body)
    if args.verify:
        verify(did)


if __name__ == "__main__":
    main()
