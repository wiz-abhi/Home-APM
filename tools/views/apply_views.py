#!/usr/bin/env python3
"""Build + apply Home APM SigNoz *saved views* (idempotent create-or-update).

Saved views are the demo-critical, pre-filtered Trace/Logs Explorer entry points:
each one drops a judge straight onto the runs behind a story beat (the 3am mystery,
the slow morning, the failing step) with zero typing.

Wire facts (verified against SigNoz v0.132.2 source + this live instance):

  * Endpoint       GET/POST  /api/v1/explorer/views          (list / create)
                   PUT/DEL   /api/v1/explorer/views/{viewId} (update / delete)
  * List is scoped by ``?sourcePage=traces|logs``.
  * The request body is a **v3 SavedView**: the ``compositeQuery`` is the *v3*
    query-builder shape (``builderQueries`` MAP keyed by "A", NOT the v5
    ``builder.queryData`` array the dashboard widgets use). Server-side
    ``SavedView.Validate() -> CompositeQuery.Validate()`` rejects anything else.
  * Filters use the classic v3 ``filters.items`` list of ``{key, op, value}``.
    Attribute-key shapes were read from this instance's autocomplete API so the
    Explorer UI re-hydrates the filter chips correctly when the view is opened:
      service.name  -> {"key":"service.name","dataType":"string","type":"resource","isColumn":true}
      automation.*  -> {"key":"automation.name","dataType":"string","type":"tag","isColumn":false}
      has_error     -> {"key":"has_error","dataType":"bool","type":"","isColumn":true}
      kind_string   -> {"key":"kind_string","dataType":"string","type":"","isColumn":true}
      duration_nano -> {"key":"duration_nano","dataType":"float64","type":"","isColumn":true}
  * Root automation-run spans are exactly ``kind_string = 'Server'`` (verified:
    151 Server roots, all with empty parent_span_id; the 616 Internal spans are
    the structural children parallel/repeat/wait/service_call).

Idempotency: views are matched by (sourcePage, name). Existing -> PUT, else POST.

Env (defaults target the seeded local demo stack):
  SIGNOZ_URL       http://localhost:8080
  SIGNOZ_EMAIL     user.abhishek2004@gmail.com
  SIGNOZ_PASSWORD  SigNoz@Warmup2026
  SIGNOZ_ORG_ID    019f5768-e00c-7dc4-9376-b2b4a44c5e55

Usage:
  python apply_views.py            # build -> write views.json -> create/update live
  python apply_views.py --dry-run  # build -> write views.json only (no network)
  python apply_views.py --verify   # after apply, GET each view back + count matching rows
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
VIEWS_JSON = HERE / "views.json"

URL = os.environ.get("SIGNOZ_URL", "http://localhost:8080").rstrip("/")
EMAIL = os.environ.get("SIGNOZ_EMAIL", "user.abhishek2004@gmail.com")
PASSWORD = os.environ.get("SIGNOZ_PASSWORD", "SigNoz@Warmup2026")
ORG_ID = os.environ.get("SIGNOZ_ORG_ID", "019f5768-e00c-7dc4-9376-b2b4a44c5e55")

AUTOMATION_SERVICE = "ha.automation"
SIDECAR_LOG_SERVICE = "ha.sidecar"  # confirmed live: the sidecar's OTLP-log service.name
SLOW_NS = 5_000_000_000  # 5s in nanoseconds
MYSTERY_AUTOMATION = "Hallway Lights 3AM"  # exact automation.name in live data

# --------------------------------------------------------------------------- #
# v3 query-builder fragments
# --------------------------------------------------------------------------- #


def attr(key: str, dtype: str = "string", atype: str = "tag", is_col: bool = False) -> dict:
    return {"key": key, "dataType": dtype, "type": atype, "isColumn": is_col, "isJSON": False}


SVC = attr("service.name", "string", "resource", True)
AUTONAME = attr("automation.name", "string", "tag", False)
HASERR = attr("has_error", "bool", "", True)
KIND = attr("kind_string", "string", "", True)
DUR = attr("duration_nano", "float64", "", True)

_EMPTY_ATTR = {"key": "", "dataType": "", "type": "", "isColumn": False, "isJSON": False}


def fitem(key: dict, op: str, value: object) -> dict:
    return {"key": key, "op": op, "value": value}


def trace_view_cq(items: list[dict], panel_type: str = "list") -> dict:
    """A v3 traces composite-query (noop list) with a filter set."""
    q = {
        "queryName": "A",
        "stepInterval": 60,
        "dataSource": "traces",
        "aggregateOperator": "noop",
        "aggregateAttribute": dict(_EMPTY_ATTR),
        "timeAggregation": "",
        "spaceAggregation": "",
        "filters": {"op": "AND", "items": items},
        "expression": "A",
        "disabled": False,
        "having": [],
        "limit": None,
        "offset": 0,
        "pageSize": 100,
        "orderBy": [{"columnName": "timestamp", "order": "desc"}],
        "reduceTo": "avg",
        "legend": "",
        "groupBy": [],
        "selectColumns": [],
        "functions": [],
    }
    return {
        "queryType": "builder",
        "panelType": panel_type,
        "unit": "",
        "builderQueries": {"A": q},
    }


def logs_view_cq(items: list[dict]) -> dict:
    q = {
        "queryName": "A",
        "stepInterval": 60,
        "dataSource": "logs",
        "aggregateOperator": "noop",
        "aggregateAttribute": dict(_EMPTY_ATTR),
        "timeAggregation": "",
        "spaceAggregation": "",
        "filters": {"op": "AND", "items": items},
        "expression": "A",
        "disabled": False,
        "having": [],
        "limit": None,
        "offset": 0,
        "pageSize": 100,
        "orderBy": [{"columnName": "timestamp", "order": "desc"}],
        "reduceTo": "avg",
        "legend": "",
        "groupBy": [],
        "selectColumns": [],
        "functions": [],
    }
    return {
        "queryType": "builder",
        "panelType": "list",
        "unit": "",
        "builderQueries": {"A": q},
    }


# --------------------------------------------------------------------------- #
# The view definitions
# --------------------------------------------------------------------------- #
LOG_SVC = attr("service.name", "string", "resource", False)


def build_views() -> list[dict]:
    return [
        {
            "name": "Automation runs",
            "sourcePage": "traces",
            "tags": ["home-apm"],
            "extraData": json.dumps({"color": "Blue"}),
            "_note": "All automation runs (root spans only: kind_string = Server).",
            "compositeQuery": trace_view_cq([
                fitem(SVC, "=", AUTOMATION_SERVICE),
                fitem(KIND, "=", "Server"),
            ]),
        },
        {
            "name": "Slow automations (>5s)",
            "sourcePage": "traces",
            "tags": ["home-apm"],
            "extraData": json.dumps({"color": "Orange"}),
            "_note": "Root automation runs whose end-to-end duration exceeds 5s.",
            "compositeQuery": trace_view_cq([
                fitem(SVC, "=", AUTOMATION_SERVICE),
                fitem(KIND, "=", "Server"),
                fitem(DUR, ">", SLOW_NS),
            ]),
        },
        {
            "name": "Failed steps",
            "sourcePage": "traces",
            "tags": ["home-apm"],
            "extraData": json.dumps({"color": "Red"}),
            "_note": "Every span with an error (service-call children carry the ERROR).",
            "compositeQuery": trace_view_cq([
                fitem(HASERR, "=", True),
            ]),
        },
        {
            "name": "3am mystery",
            "sourcePage": "traces",
            "tags": ["home-apm"],
            "extraData": json.dumps({"color": "Violet"}),
            "_note": f"Runs of '{MYSTERY_AUTOMATION}' - the silent choose branch. Root spans.",
            "compositeQuery": trace_view_cq([
                fitem(AUTONAME, "=", MYSTERY_AUTOMATION),
                fitem(KIND, "=", "Server"),
            ]),
        },
        {
            "name": "Home APM sidecar logs",
            "sourcePage": "logs",
            "tags": ["home-apm"],
            "extraData": json.dumps({"color": "Green"}),
            "_note": (
                "The sidecar's own OTLP logs (INFO 'converted run ... -> trace ...'), "
                f"service.name = '{SIDECAR_LOG_SERVICE}' - live."
            ),
            "compositeQuery": logs_view_cq([
                fitem(LOG_SVC, "=", SIDECAR_LOG_SERVICE),
            ]),
        },
    ]


# --------------------------------------------------------------------------- #
# SigNoz REST
# --------------------------------------------------------------------------- #


def login(client: httpx.Client) -> str:
    r = client.post(
        f"{URL}/api/v2/sessions/email_password",
        json={"email": EMAIL, "password": PASSWORD, "orgID": ORG_ID},
    )
    r.raise_for_status()
    return r.json()["data"]["accessToken"]


def list_views(client: httpx.Client, hdr: dict, source_page: str) -> list[dict]:
    r = client.get(f"{URL}/api/v1/explorer/views?sourcePage={source_page}", headers=hdr)
    r.raise_for_status()
    return r.json().get("data") or []


def body_for(view: dict) -> dict:
    return {
        "name": view["name"],
        "category": view.get("category", ""),
        "sourcePage": view["sourcePage"],
        "tags": view.get("tags", []),
        "extraData": view.get("extraData", ""),
        "compositeQuery": view["compositeQuery"],
    }


def apply_view(client: httpx.Client, hdr: dict, view: dict) -> tuple[str, str]:
    """Idempotent apply = delete-any-existing then create.

    NOTE: we deliberately do NOT use the PUT /views/{id} update endpoint. In
    SigNoz v0.132.2 ``UpdateView`` stores the composite query as a raw ``[]byte``
    (bun then hex/escape-encodes it) instead of ``string(data)`` like the create
    path, which double-encodes the stored JSON and makes every subsequent LIST
    500 with "invalid character '\\' looking for beginning of value". Delete +
    create keeps the stored ``data`` column clean and is fully idempotent.
    """
    existing = list_views(client, hdr, view["sourcePage"])
    matches = [v for v in existing if v.get("name") == view["name"]]
    verb = "created"
    for m in matches:
        mid = m.get("id") or m.get("uuid")
        client.delete(f"{URL}/api/v1/explorer/views/{mid}", headers=hdr)
        verb = "replaced"
    body = body_for(view)
    r = client.post(f"{URL}/api/v1/explorer/views", headers=hdr, json=body)
    if r.status_code >= 300:
        print(f"  ERROR {r.status_code}: {r.text[:400]}", file=sys.stderr)
        r.raise_for_status()
    data = r.json().get("data")
    vid = data if isinstance(data, str) else (data or {}).get("id", "?")
    return vid, verb


def verify(client: httpx.Client, hdr: dict, views: list[dict]) -> None:
    now = int(time.time() * 1000)
    start = now - 7 * 86400 * 1000
    for src in ("traces", "logs"):
        live = {v["name"]: v for v in list_views(client, hdr, src)}
        for view in [v for v in views if v["sourcePage"] == src]:
            got = live.get(view["name"])
            mark = "OK " if got else "MISSING"
            vid = (got or {}).get("id", "-") if got else "-"
            print(f"  [{src:6}] {mark} {view['name']!r}  id={vid}")
            # Count matching rows via v5 query_range so 'does it actually filter?' is proven.
            q = view["compositeQuery"]["builderQueries"]["A"]
            exprs = []
            for it in q["filters"]["items"]:
                k, op, val = it["key"]["key"], it["op"], it["value"]
                v = f"'{val}'" if isinstance(val, str) else (
                    "true" if val is True else "false" if val is False else val)
                exprs.append(f"{k} {op} {v}")
            spec = {
                "name": "A", "signal": q["dataSource"], "disabled": False,
                "stepInterval": 60, "aggregations": [{"expression": "count()"}],
                "filter": {"expression": " AND ".join(exprs)},
            }
            payload = {
                "schemaVersion": "v1", "start": start, "end": now,
                "requestType": "scalar",
                "compositeQuery": {"queries": [{"type": "builder_query", "spec": spec}]},
            }
            rr = client.post(f"{URL}/api/v5/query_range", headers=hdr, json=payload)
            try:
                rows = rr.json()["data"]["data"]["results"][0]["data"]
                n = rows[0][-1] if rows else 0
                print(f"           filter -> {n} matching {q['dataSource']} row-group(s)")
            except Exception:
                print(f"           filter -> {rr.status_code} {rr.text[:120]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build + write json only")
    ap.add_argument("--verify", action="store_true", help="GET views + count rows after apply")
    args = ap.parse_args()

    views = build_views()
    VIEWS_JSON.write_text(json.dumps(views, indent=2), encoding="utf-8")
    print(f"wrote {VIEWS_JSON} ({len(views)} views)")
    if args.dry_run:
        return 0

    with httpx.Client(timeout=30) as client:
        tok = login(client)
        hdr = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
        for view in views:
            vid, action = apply_view(client, hdr, view)
            print(f"view [{view['sourcePage']:6}] {view['name']!r} {action}  id={vid}")
        if args.verify:
            print("\n==== VERIFY ====")
            verify(client, hdr, views)
    return 0


if __name__ == "__main__":
    sys.exit(main())
