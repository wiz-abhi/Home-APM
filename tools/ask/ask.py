"""Ask your house — a plain-English front door to your Home Assistant traces.

Type ``why did my hallway lights turn on at 3am?`` and get ONE English sentence
naming the exact cause, grounded in live SigNoz data via the MCP server (spec
item #8). Single-turn orchestration:

  1. Parse the question with ``gemini-3.1-flash-lite`` into a structured intent.
  2. Deterministic MCP tool chain (raw JSON-RPC): find the relevant automation
     run, pull its span tree with the frozen §0A ``ha.*`` attributes.
  3. One final Gemini call narrates the causal chain in a single sentence plus a
     supporting detail; the trace id and a SigNoz deep link are printed too.

The MCP path is primary (a judging point). Gemini is used only to translate
natural language at the two ends; every causal fact comes from real trace data,
so a heuristic narration fallback keeps the tool answering even if the LLM is
unreachable.

Usage:
    python tools/ask/ask.py "why did my hallway lights turn on at 3am?"
    python tools/ask/ask.py "why is my morning routine slow?"
    python tools/ask/ask.py "did anything fail tonight?"
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from mcp_client import McpClient, McpError

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
SIGNOZ_TRACE_LINK = "http://localhost:8080/trace/{trace_id}"
AUTOMATION_SERVICE = "ha.automation"
DEFAULT_TIME_RANGE = "24h"

QuestionType = Literal["why", "slow", "failed", "status"]


# --------------------------------------------------------------------------- #
# Gemini (OpenAI-compatible) — called directly over httpx, no extra deps
# --------------------------------------------------------------------------- #


def _load_gemini_key() -> str | None:
    """Read ``GEMINI_API_KEY`` from the environment, or the Windows user store."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    if sys.platform == "win32":  # the key lives in the User env scope on this box
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _ = winreg.QueryValueEx(handle, "GEMINI_API_KEY")
                return str(value) if value else None
        except OSError:
            return None
    return None


def gemini_chat(prompt: str, *, system: str, json_mode: bool = False) -> str | None:
    """One Gemini chat completion. Returns ``None`` on any failure (caller falls back)."""
    key = _load_gemini_key()
    if not key:
        return None
    payload: dict[str, Any] = {
        "model": GEMINI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = httpx.post(
            GEMINI_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=20.0,
        )
        if resp.status_code != 200:
            return None
        content: str = resp.json()["choices"][0]["message"]["content"]
        return content.strip()
    except (httpx.HTTPError, KeyError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# intent parsing
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Intent:
    """Structured reading of the user's question."""

    question_type: QuestionType
    automation_hint: str | None
    time_range: str = DEFAULT_TIME_RANGE


_INTENT_SYSTEM = (
    "You classify a smart-home debugging question about Home Assistant automations. "
    "Return ONLY a JSON object with keys: "
    "question_type (one of: why, slow, failed, status), "
    "automation_hint (a short lowercase phrase naming the automation or device the "
    "user means, e.g. 'hallway lights', 'morning routine'; null if none is named), "
    "time_range (a SigNoz relative range like '24h','12h','48h'; default '24h'). "
    "Use question_type 'slow' for latency/slowness, 'failed' for errors/failures, "
    "'why' for causal 'why did X happen', 'status' otherwise."
)


def _heuristic_intent(question: str) -> Intent:
    """Deterministic fallback intent parser (no LLM)."""
    q = question.lower()
    if any(w in q for w in ("slow", "slug", "latency", "taking long", "delay")):
        qtype: QuestionType = "slow"
    elif any(w in q for w in ("fail", "error", "broke", "crash", "wrong")):
        qtype = "failed"
    elif q.strip().startswith("why") or "why" in q:
        qtype = "why"
    else:
        qtype = "status"
    hint: str | None = None
    for phrase in ("hallway", "morning routine", "morning", "good night", "garage"):
        if phrase in q:
            hint = phrase
            break
    return Intent(question_type=qtype, automation_hint=hint)


def parse_intent(question: str) -> Intent:
    """Parse the question with Gemini, falling back to a heuristic parser."""
    raw = gemini_chat(question, system=_INTENT_SYSTEM, json_mode=True)
    if raw is None:
        return _heuristic_intent(question)
    try:
        obj = json.loads(raw)
        qtype = obj.get("question_type", "why")
        if qtype not in ("why", "slow", "failed", "status"):
            qtype = "why"
        hint = obj.get("automation_hint")
        return Intent(
            question_type=qtype,
            automation_hint=str(hint).lower().strip() if hint else None,
            time_range=str(obj.get("time_range") or DEFAULT_TIME_RANGE),
        )
    except (ValueError, AttributeError):
        return _heuristic_intent(question)


# --------------------------------------------------------------------------- #
# SigNoz Query Builder helpers
# --------------------------------------------------------------------------- #


def _rows_from_builder(text: str) -> list[dict[str, Any]]:
    """Extract the flat ``[{col: val, ...}]`` rows from a builder-query response."""
    if not text.startswith("{"):
        raise McpError(f"unexpected builder response: {text[:200]}")
    payload = json.loads(text)
    if payload.get("status") != "success":
        raise McpError(f"builder query not successful: {text[:200]}")
    results = payload["data"]["data"]["results"]
    if not results:
        return []
    rows = results[0].get("rows") or []
    return [row["data"] for row in rows]


def _builder_query(
    filter_expr: str, select: list[str], *, time_range: str, limit: int
) -> dict[str, Any]:
    """Assemble a v5 raw Query-Builder request for the traces signal."""
    return {
        "schemaVersion": "v1",
        "requestType": "raw",
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "traces",
                        "filter": {"expression": filter_expr},
                        "selectFields": [{"name": name} for name in select],
                        "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
                        "limit": limit,
                    },
                }
            ]
        },
        "variables": {},
        "formatOptions": {"formatTableResultForUI": False, "fillGaps": False},
        "start": _relative_start_ms(time_range),
        "end": _now_ms(),
    }


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _relative_start_ms(time_range: str) -> int:
    """Convert a SigNoz relative range ('24h','30m','3d') to a start epoch-ms."""
    unit = time_range[-1]
    try:
        amount = int(time_range[:-1])
    except ValueError:
        amount = 24
        unit = "h"
    factor = {"m": 60, "h": 3600, "d": 86400}.get(unit, 3600)
    return _now_ms() - amount * factor * 1000


# --------------------------------------------------------------------------- #
# MCP chain: resolve automation → find trace → fetch spans
# --------------------------------------------------------------------------- #


def resolve_automation(client: McpClient, hint: str | None) -> str | None:
    """Fuzzy-match the user's hint against live ``automation.name`` values."""
    if not hint:
        return None
    text = client.call_tool(
        "signoz_get_field_values",
        {"signal": "traces", "name": "automation.name", "fieldContext": "attribute"},
    )
    try:
        values = json.loads(text)["data"]["values"]["stringValues"]
    except (ValueError, KeyError, TypeError):
        return None
    names = [v for v in values if isinstance(v, str) and not v.startswith("[sim]")]
    hint_words = set(hint.split())
    best: tuple[int, str] | None = None
    for name in names:
        lname = name.lower()
        if hint in lname or lname in hint:
            return name
        overlap = len(hint_words & set(lname.split()))
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, name)
    return best[1] if best else None


_TRACE_SELECT = ["trace_id", "automation.name", "name", "ha.step_type", "durationNano", "has_error"]
_SPAN_SELECT = [
    "span_id",
    "parent_span_id",
    "name",
    "service.name",
    "ha.step_type",
    "ha.node_path",
    "ha.result",
    "ha.template_errors",
    "automation.name",
    "automation.room",
    "durationNano",
    "has_error",
    "status_message",
]


def find_trace(client: McpClient, intent: Intent, automation_name: str | None) -> str | None:
    """Pick the single most relevant trace id for the question.

    Scoping differs by question type: 'failed' matches error spans (which live on
    the ``ha.<domain>`` child that raised, not on ``ha.automation``), so it filters
    on ``has_error`` and keeps only rows carrying an ``automation.name``; 'slow'
    searches the widest span set so the true villain surfaces; 'why'/'status'
    scope to the ``ha.automation`` root of the most recent matching run.
    """
    clauses: list[str] = []
    if automation_name:
        clauses.append(f"automation.name = '{_escape(automation_name)}'")

    if intent.question_type == "failed":
        clauses.append("has_error = true")
    elif intent.question_type == "slow":
        if not automation_name:
            clauses.append(f"service.name = '{AUTOMATION_SERVICE}'")
    else:  # why / status
        clauses.append(f"service.name = '{AUTOMATION_SERVICE}'")

    query = _builder_query(
        " AND ".join(clauses), _TRACE_SELECT, time_range=intent.time_range, limit=200
    )
    rows = _rows_from_builder(client.call_tool("signoz_execute_builder_query", {"query": query}))
    if not rows:
        return None

    # Keep only Home Assistant spans (they all carry an automation.name); this
    # scopes an unqualified `has_error = true` to our traces, not the whole stack.
    ha_rows = [r for r in rows if r.get("automation.name")]
    rows = ha_rows or rows

    if intent.question_type == "slow":
        slowest = max(rows, key=lambda r: _as_int(r.get("durationNano")))
        return str(slowest.get("trace_id"))
    # 'why' / 'failed' / 'status': rows are newest-first, so the first row's
    # trace is the most recent matching run.
    return str(rows[0].get("trace_id"))


def fetch_spans(client: McpClient, trace_id: str, time_range: str) -> list[dict[str, Any]]:
    """Pull every span of one trace, enriched with the frozen §0A attributes."""
    query = _builder_query(
        f"trace_id = '{_escape(trace_id)}'", _SPAN_SELECT, time_range=time_range, limit=300
    )
    rows = _rows_from_builder(client.call_tool("signoz_execute_builder_query", {"query": query}))
    rows.sort(key=lambda r: _as_int(r.get("durationNano")), reverse=False)
    return rows


# --------------------------------------------------------------------------- #
# fact extraction
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Facts:
    """The causal facts distilled from one trace's span tree."""

    automation: str
    room: str
    trace_id: str
    trigger: str | None
    branches: list[str]
    conditions: list[str]
    slowest_name: str | None
    slowest_seconds: float
    errors: list[str]
    service_calls: list[str]
    total_seconds: float
    total_human: str = "under a second"
    span_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def extract_facts(spans: list[dict[str, Any]], trace_id: str) -> Facts:
    """Distil a span list into the handful of facts the narration needs."""
    automation = _first(spans, "automation.name") or "an automation"
    room = _first(spans, "automation.room") or ""
    trigger: str | None = None
    branches: list[str] = []
    conditions: list[str] = []
    errors: list[str] = []
    service_calls: list[str] = []
    slowest_name: str | None = None
    slowest_ns = 0
    total_ns = 0

    for span in spans:
        name = str(span.get("name") or "")
        step = str(span.get("ha.step_type") or "")
        dur = _as_int(span.get("durationNano"))
        node_path = str(span.get("ha.node_path") or "")
        if node_path == "__root__":
            total_ns = max(total_ns, dur)
        if step == "trigger":
            trigger = name
        elif step == "choose" and "branch" in name:
            branches.append(name)
        elif step == "condition":
            conditions.append(name)
        elif step == "service_call":
            svc = str(span.get("service.name") or "")
            domain = svc.replace("ha.", "", 1) if svc.startswith("ha.") else svc
            service_calls.append(f"{name} (domain {domain})")
        if _truthy(span.get("has_error")):
            detail = str(span.get("ha.template_errors") or span.get("status_message") or "error")
            errors.append(f"{name}: {detail}")
        # the slowest non-root span is usually the villain
        if node_path != "__root__" and dur > slowest_ns:
            slowest_ns = dur
            slowest_name = name

    return Facts(
        automation=automation,
        room=room,
        trace_id=trace_id,
        trigger=trigger,
        branches=branches,
        conditions=_dedupe(conditions),
        slowest_name=slowest_name,
        slowest_seconds=round(slowest_ns / 1e9, 1),
        errors=_dedupe(errors),
        service_calls=_dedupe(service_calls),
        total_seconds=round(total_ns / 1e9, 1),
        total_human=_human_duration(total_ns),
        span_count=len(spans),
    )


def _human_duration(nanos: int) -> str:
    """Render a duration for prose: milliseconds under 1s, else seconds."""
    if nanos <= 0:
        return "under a millisecond"
    if nanos < 1_000_000_000:
        return f"{round(nanos / 1e6)}ms"
    return f"{round(nanos / 1e9, 1)}s"


# --------------------------------------------------------------------------- #
# narration
# --------------------------------------------------------------------------- #


_NARRATE_SYSTEM = (
    "You explain a Home Assistant automation run to its owner in plain English. "
    "Given structured trace facts, answer the user's question in EXACTLY ONE "
    "sentence that names the concrete cause, then add ONE short supporting detail "
    "sentence with a number (a duration, a branch, or an error). Be specific and "
    "confident; use only the facts provided; do not invent entities or times. No "
    "preamble, no bullet points."
)


def _facts_prompt(question: str, facts: Facts, intent: Intent) -> str:
    lines = [
        f"User question: {question}",
        f"Question type: {intent.question_type}",
        f"Automation: {facts.automation}" + (f" (room: {facts.room})" if facts.room else ""),
        f"Trigger step: {facts.trigger or 'unknown'}",
        f"Choose branches taken: {', '.join(facts.branches) or 'none'}",
        f"Conditions evaluated (these passed silently to reach the branch): "
        f"{', '.join(facts.conditions) or 'none'}",
        f"Service calls (the actual effects): {', '.join(facts.service_calls) or 'none'}",
        f"Slowest step: {facts.slowest_name or 'n/a'} at {facts.slowest_seconds}s",
        f"Total run duration: {facts.total_human}",
        f"Errors: {'; '.join(facts.errors) or 'none'}",
    ]
    return "\n".join(lines)


def _heuristic_narration(facts: Facts, intent: Intent) -> str:
    """Deterministic one-sentence answer used when Gemini is unavailable."""
    if intent.question_type == "failed" or facts.errors:
        if facts.errors:
            return (
                f"Yes — {facts.automation} failed: {facts.errors[0]}. "
                f"The error surfaced during a service call in this run."
            )
        return f"No failures found for {facts.automation} in the last {intent.time_range}."
    if intent.question_type == "slow":
        return (
            f"{facts.automation} was slow because its '{facts.slowest_name}' step took "
            f"{facts.slowest_seconds}s. That single step dominated the {facts.total_seconds}s run."
        )
    branch = facts.branches[0] if facts.branches else "its action"
    effect = facts.service_calls[0] if facts.service_calls else "a service call"
    cond = f" after {facts.conditions[0]} silently passed" if facts.conditions else ""
    return (
        f"{facts.automation} ran because its '{facts.trigger or 'trigger'}' fired and "
        f"{branch}{cond}, which invoked {effect}. "
        f"The whole run took {facts.total_human} across {facts.span_count} steps."
    )


def narrate(question: str, facts: Facts, intent: Intent) -> str:
    """Narrate the causal chain in one sentence + one detail (Gemini, then heuristic)."""
    answer = gemini_chat(_facts_prompt(question, facts, intent), system=_NARRATE_SYSTEM)
    if answer:
        return answer
    return _heuristic_narration(facts, intent)


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #


def _escape(value: str) -> str:
    return value.replace("'", "\\'")


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _truthy(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def _first(spans: list[dict[str, Any]], key: str) -> str | None:
    for span in spans:
        val = span.get(key)
        if val:
            return str(val)
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def answer_question(question: str) -> int:
    """Run the full single-turn pipeline for one question. Returns an exit code."""
    intent = parse_intent(question)
    try:
        with McpClient() as client:
            automation = resolve_automation(client, intent.automation_hint)
            trace_id = find_trace(client, intent, automation)
            if trace_id is None:
                target = automation or intent.automation_hint or "your automations"
                print(
                    f"I couldn't find a matching run for {target} in the last "
                    f"{intent.time_range}. Try widening the window or triggering a run."
                )
                return 1
            spans = fetch_spans(client, trace_id, intent.time_range)
    except McpError as err:
        print(f"MCP error: {err}", file=sys.stderr)
        return 2

    facts = extract_facts(spans, trace_id)
    print(narrate(question, facts, intent))
    print()
    print(f"  trace_id:    {trace_id}")
    print(f"  flame graph: {SIGNOZ_TRACE_LINK.format(trace_id=trace_id)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    return answer_question(" ".join(args))


if __name__ == "__main__":
    raise SystemExit(main())
