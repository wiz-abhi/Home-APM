#!/usr/bin/env python3
"""Generate the click-by-click demo deep-links -> writes DEMO-LINKS.md.

Every URL is reproducible: run this right before recording to refresh the
trace-detail links (SigNoz retains a rolling window, so trace IDs age out).

Two kinds of link:
  * **Pre-filtered Trace Explorer** deep-links. These carry the query as a
    ``compositeQuery`` URL param in the *frontend* query shape (``builder.queryData``
    array with BOTH ``filter.expression`` and ``filters.items``). Verified live:
    the Explorer keeps ``dataSource=traces`` and applies the filter (the naive
    v3 ``builderQueries``-map shape gets reset to a default metrics query, so we
    do NOT use that shape for URLs). ``filter.expression`` is what actually drives
    QB v5, ``filters.items`` keeps the filter chips populated for the reader.
  * **Static routes** (dashboard / services / service-map / a specific trace).

Trace IDs are pulled fresh from ClickHouse (docker exec, read-only SELECT).

Usage:
  python make_demo_links.py            # write DEMO-LINKS.md with fresh trace ids
  python make_demo_links.py --print    # also echo every URL to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "DEMO-LINKS.md"

BASE = os.environ.get("SIGNOZ_URL", "http://localhost:8080").rstrip("/")
DASHBOARD_ID = os.environ.get("HOMEAPM_DASHBOARD_ID", "019f8a8f-d7f4-77dd-a5b8-b69d2a7fad3b")
CH_CONTAINER = "signoz-telemetrystore-clickhouse-0-0"


def attr(key: str, dtype: str, atype: str, is_col: bool) -> dict:
    return {"key": key, "dataType": dtype, "type": atype, "isColumn": is_col, "isJSON": False}


SVC = attr("service.name", "string", "resource", True)
AUTONAME = attr("automation.name", "string", "tag", False)
HASERR = attr("has_error", "bool", "", True)
KIND = attr("kind_string", "string", "", True)
DUR = attr("duration_nano", "float64", "", True)


def fitem(key: dict, op: str, value: object) -> dict:
    return {"key": key, "op": op, "value": value}


def explorer_url(expr: str, items: list[dict], panel: str = "list") -> str:
    """Frontend-shape compositeQuery deep-link (verified to pre-filter live)."""
    q = {
        "dataSource": "traces", "queryName": "A", "aggregateOperator": "noop",
        "aggregateAttribute": {
            "key": "", "dataType": "", "type": "", "isColumn": False, "isJSON": False,
        },
        "timeAggregation": "rate", "spaceAggregation": "sum",
        "filter": {"expression": expr},
        "filters": {"op": "AND", "items": items},
        "aggregations": [{"metricName": "", "temporality": "", "timeAggregation": "avg",
                          "spaceAggregation": "sum", "reduceTo": "avg"}],
        "expression": "A", "disabled": False, "stepInterval": None, "having": [], "limit": None,
        "orderBy": [{"columnName": "timestamp", "order": "desc"}], "groupBy": [],
        "legend": "", "reduceTo": "avg", "functions": [],
    }
    cq = {
        "queryType": "builder",
        "builder": {"queryData": [q], "queryFormulas": [], "queryTraceOperator": []},
        "promql": [{"name": "A", "query": "", "legend": "", "disabled": False}],
        "clickhouse_sql": [{"name": "A", "legend": "", "disabled": False, "query": ""}],
        "id": "home-apm-demo",
    }
    enc = urllib.parse.quote(json.dumps(cq))
    return f"{BASE}/traces-explorer?panelType={panel}&compositeQuery={enc}"


def logs_explorer_url() -> str:
    """Logs Explorer deep-link filtered to the sidecar's own OTLP logs."""
    q = {
        "dataSource": "logs", "queryName": "A", "aggregateOperator": "noop",
        "aggregateAttribute": {
            "key": "", "dataType": "", "type": "", "isColumn": False, "isJSON": False,
        },
        "timeAggregation": "rate", "spaceAggregation": "sum",
        "filter": {"expression": "service.name = 'ha.sidecar'"},
        "filters": {"op": "AND", "items": [fitem(
            attr("service.name", "string", "resource", False), "=", "ha.sidecar")]},
        "aggregations": [{"metricName": "", "temporality": "", "timeAggregation": "avg",
                          "spaceAggregation": "sum", "reduceTo": "avg"}],
        "expression": "A", "disabled": False, "stepInterval": None, "having": [], "limit": None,
        "orderBy": [{"columnName": "timestamp", "order": "desc"}], "groupBy": [],
        "legend": "", "reduceTo": "avg", "functions": [],
    }
    cq = {
        "queryType": "builder",
        "builder": {"queryData": [q], "queryFormulas": [], "queryTraceOperator": []},
        "promql": [{"name": "A", "query": "", "legend": "", "disabled": False}],
        "clickhouse_sql": [{"name": "A", "legend": "", "disabled": False, "query": ""}],
        "id": "home-apm-logs",
    }
    enc = urllib.parse.quote(json.dumps(cq))
    return f"{BASE}/logs/logs-explorer?compositeQuery={enc}"


def latest_trace(where: str, order: str = "max(timestamp)") -> str | None:
    q = (
        "SELECT traceID FROM signoz_traces.distributed_signoz_index_v3 "
        f"WHERE {where} AND timestamp > now() - INTERVAL 3 HOUR "
        f"GROUP BY traceID ORDER BY {order} DESC LIMIT 1 FORMAT TabSeparated"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", CH_CONTAINER, "clickhouse-client", "-q", q],
            capture_output=True, text=True, timeout=30,
        )
        tid = out.stdout.strip().splitlines()
        return tid[0] if tid else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
VIEWS = [
    ("Automation runs", "service.name = 'ha.automation' AND kind_string = 'Server'",
     [fitem(SVC, "=", "ha.automation"), fitem(KIND, "=", "Server")]),
    ("Slow automations (>5s)",
     "service.name = 'ha.automation' AND kind_string = 'Server' AND duration_nano > 5000000000",
     [fitem(SVC, "=", "ha.automation"), fitem(KIND, "=", "Server"),
      fitem(DUR, ">", 5_000_000_000)]),
    ("Failed steps", "has_error = true", [fitem(HASERR, "=", True)]),
    ("3am mystery", "automation.name = 'Hallway Lights 3AM' AND kind_string = 'Server'",
     [fitem(AUTONAME, "=", "Hallway Lights 3AM"), fitem(KIND, "=", "Server")]),
]


def build() -> str:
    gn = latest_trace("attributes_string['automation.name']='Good Night' AND has_error=1") \
        or latest_trace(
            "attributes_string['automation.name']='Good Night' AND kind_string='Server'")
    mr = latest_trace(
        "attributes_string['automation.name']='Morning Routine' AND kind_string='Server'",
        order="max(durationNano)")
    h3 = latest_trace(
        "attributes_string['automation.name']='Hallway Lights 3AM' AND kind_string='Server'")

    lines = [
        "# Home APM - Demo deep-links (click-by-click)",
        "",
        "Regenerate right before recording: `python tools/views/make_demo_links.py`",
        "(trace IDs age out of SigNoz's retention window; the Explorer/dashboard/map",
        "links are stable). Log in once, then every link below lands pre-filtered.",
        "",
        "> Set the time-range picker to **Last 3 hours** for the Explorer/Services/",
        "> Service-map beats so the seeded runs are all in frame.",
        "",
        "## Beat 0 - Saved views hub",
        f"- Saved views listing: {BASE}/traces/saved-views",
        "  (click any of the 4 named views - they hydrate the query builder)",
        "",
        "## Beats 1-4 - Pre-filtered Trace Explorer (one URL each)",
    ]
    for name, expr, items in VIEWS:
        lines.append(f"### {name}")
        lines.append(f"`{expr}`")
        lines.append(f"{explorer_url(expr, items)}")
        lines.append("")

    lines += [
        "## Beat 1 - The 3am mystery (specific run)",
        f"- Newest 'Hallway Lights 3AM' run: {BASE}/trace/{h3 or 'RUN_make_demo_links_to_fill'}",
        "  (open it to show the silently-passing `choose` branch)",
        "",
        "## Beat 2 - The slow morning (47s red span)",
        f"- Slowest 'Morning Routine' run: {BASE}/trace/{mr or 'RUN_make_demo_links_to_fill'}",
        "  (the `wait_for_trigger` span is ~47s wide and red)",
        "",
        "## Beat 2.5 - Parallel + template error (good_night)",
        f"- 'Good Night' run WITH the error span: "
        f"{BASE}/trace/{gn or 'RUN_make_demo_links_to_fill'}",
        "  (parallel block = overlapping bars; `repeat` = stacked iterations;",
        "   the persistent_notification template action = ERROR span)",
        "",
        "## Beat 1.5 - Logs <-> traces (sidecar's own OTLP logs)",
        f"- Saved logs view 'Home APM sidecar logs': {BASE}/logs/saved-views",
        f"- Logs Explorer filtered to the sidecar: {logs_explorer_url()}",
        "  (INFO `converted run <run_id> -> trace <trace_id>` lines - the bridge narrating itself)",
        "",
        "## Beat 4 - The board + the house service map",
        f"- Home APM dashboard: {BASE}/dashboard/{DASHBOARD_ID}",
        f"- Services (RED metrics for all 7 ha.* services): {BASE}/services",
        f"- Service map (automation -> light/cover/climate/...): {BASE}/service-map",
        "  (set range to Last 3h; the failing `ha.automation -> ha.persistent_notification`",
        "   edge shows ~70% errors)",
        "",
        "## Beat 5 - The alert",
        f"- Triggered alerts: {BASE}/alerts?tab=Triggered%20Alerts",
        "",
        "---",
        "_All Explorer links use the frontend `compositeQuery` shape with a populated",
        "`filter.expression` - verified live to keep `dataSource=traces` and apply the",
        "filter on load._",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="echo URLs to stdout too")
    args = ap.parse_args()
    md = build()
    OUT.write_text(md, encoding="utf-8")
    print(f"wrote {OUT}")
    if args.print:
        print("\n" + md)


if __name__ == "__main__":
    main()
