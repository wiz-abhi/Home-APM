"""Unit tests for the logs->trace correlation core (:mod:`homeapm.logs_bridge`).

These are pure and offline: they drive the correlator + record-shaping functions
with mocked ``logbook/event_stream`` payloads (the two real shapes observed live
against HA 2026.7.3) and assert the trace/span linkage, the buffer+flush race
handling, and the body/severity mapping. No OTel SDK, no socket, no HA.
"""

from __future__ import annotations

from typing import Any

from opentelemetry._logs import SeverityNumber

from homeapm.logs_bridge import (
    LinkTarget,
    LogCorrelator,
    build_correlated_log,
    parse_logbook_entry,
)

_SEC = 1_000_000_000
# A run window: starts at t=1000s, finishes at t=1050s (a 50s automation).
_START = 1000 * _SEC
_FINISH = 1050 * _SEC
_TRACE_ID = 0x0123456789ABCDEF0123456789ABCDEF
_SPAN_ID = 0x1122334455667788
_CTX = "01KY5B28C8BT0REXVWA7WN1Z8E"


def _target(
    *,
    context_id: str = _CTX,
    name: str = "Hallway Lights 3AM",
    start: int = _START,
    finish: int = _FINISH,
    trace_id: int = _TRACE_ID,
    span_id: int = _SPAN_ID,
    room: str = "hallway",
) -> LinkTarget:
    return LinkTarget(
        context_id=context_id,
        automation_name=name,
        trace_id=trace_id,
        span_id=span_id,
        room=room,
        start_nanos=start,
        finish_nanos=finish,
    )


def _trigger_entry(when_s: float = 1000.0) -> dict[str, Any]:
    """An automation-trigger logbook entry: carries the run's ``context_id``."""
    return {
        "entity_id": "automation.hallway_lights_3am",
        "domain": "automation",
        "message": "triggered by time pattern",
        "name": "Hallway Lights 3AM",
        "context_id": _CTX,
        "when": when_s,
    }


def _state_entry(when_s: float = 1002.0, state: str = "on") -> dict[str, Any]:
    """A caused state-change entry: null ``context_id``, names its automation."""
    return {
        "entity_id": "light.hallway",
        "state": state,
        "context_id": None,
        "context_entity_id": "automation.hallway_lights_3am",
        "context_event_type": "automation_triggered",
        "context_name": "Hallway Lights 3AM",
        "when": when_s,
    }


# -- parsing -----------------------------------------------------------------


def test_parse_converts_when_to_nanos_and_extracts_fields() -> None:
    parsed = parse_logbook_entry(_state_entry(when_s=1002.5))
    assert parsed is not None
    assert parsed.when_nanos == 1002_500_000_000
    assert parsed.entity_id == "light.hallway"
    assert parsed.context_name == "Hallway Lights 3AM"
    assert parsed.context_id is None
    assert parsed.state == "on"


def test_parse_rejects_entry_without_when() -> None:
    assert parse_logbook_entry({"entity_id": "light.hallway"}) is None


# -- correlation: direct context_id (trigger entry) --------------------------


def test_trigger_entry_links_by_context_id() -> None:
    corr = LogCorrelator()
    corr.register(_target())
    logs = corr.ingest(parse_logbook_entry(_trigger_entry()))  # type: ignore[arg-type]
    assert len(logs) == 1
    assert logs[0].trace_id == _TRACE_ID
    assert logs[0].span_id == _SPAN_ID
    assert logs[0].attributes["ha.correlation"] == "context_id"


# -- correlation: name + time window (caused state change) -------------------


def test_state_entry_links_by_name_within_window() -> None:
    corr = LogCorrelator()
    corr.register(_target())
    logs = corr.ingest(parse_logbook_entry(_state_entry(when_s=1002.0)))  # type: ignore[arg-type]
    assert len(logs) == 1
    assert logs[0].trace_id == _TRACE_ID
    assert logs[0].attributes["ha.correlation"] == "automation_name"
    assert logs[0].attributes["entity_id"] == "light.hallway"
    assert logs[0].attributes["domain"] == "light"
    assert logs[0].attributes["automation.room"] == "hallway"


def test_state_entry_outside_window_is_not_linked() -> None:
    corr = LogCorrelator()
    corr.register(_target())
    # 100s after finish -> well outside window + slack -> stays pending.
    logs = corr.ingest(parse_logbook_entry(_state_entry(when_s=1150.0)))  # type: ignore[arg-type]
    assert logs == []
    assert corr.pending_count == 1


# -- the race: entries arrive BEFORE their trace registers -------------------


def test_pending_entries_flush_on_register() -> None:
    """Logbook entries stream before the trace exists; register flushes them."""
    corr = LogCorrelator()
    # Both entries arrive first -> buffered, nothing emitted yet.
    assert corr.ingest(parse_logbook_entry(_trigger_entry())) == []  # type: ignore[arg-type]
    assert corr.ingest(parse_logbook_entry(_state_entry())) == []  # type: ignore[arg-type]
    assert corr.pending_count == 2
    # Trace finishes and registers -> both correlate now.
    flushed = corr.register(_target())
    assert len(flushed) == 2
    assert {log.trace_id for log in flushed} == {_TRACE_ID}
    assert corr.pending_count == 0


def test_state_entry_not_mislinked_to_previous_run() -> None:
    """A newer run's entry must not bind to the older run still in the window."""
    corr = LogCorrelator()
    old = _target(
        context_id="OLDCTX", start=1000 * _SEC, finish=1050 * _SEC, trace_id=0xAAAA, span_id=0xA1
    )
    new = _target(
        context_id="NEWCTX", start=2000 * _SEC, finish=2050 * _SEC, trace_id=0xBBBB, span_id=0xB1
    )
    corr.register(old)
    # A state change from the NEW run arrives before the new trace registers.
    assert corr.ingest(parse_logbook_entry(_state_entry(when_s=2002.0))) == []  # type: ignore[arg-type]
    flushed = corr.register(new)
    assert len(flushed) == 1
    assert flushed[0].trace_id == 0xBBBB  # bound to the correct (new) run


# -- body + severity shaping -------------------------------------------------


def test_body_for_state_change_reads_like_a_sentence() -> None:
    parsed = parse_logbook_entry(_state_entry(state="on"))
    assert parsed is not None
    log = build_correlated_log(parsed, _target())
    assert log.body == "light.hallway → on by automation Hallway Lights 3AM"


def test_body_for_trigger_names_the_automation() -> None:
    parsed = parse_logbook_entry(_trigger_entry())
    assert parsed is not None
    log = build_correlated_log(parsed, _target())
    assert log.body == "Hallway Lights 3AM: triggered by time pattern"


def test_unavailable_state_is_warn() -> None:
    parsed = parse_logbook_entry(_state_entry(state="unavailable"))
    assert parsed is not None
    log = build_correlated_log(parsed, _target())
    assert log.severity_number is SeverityNumber.WARN
    assert log.severity_text == "WARN"


def test_error_message_is_error_severity() -> None:
    entry = _trigger_entry()
    entry["message"] = "failed to render template"
    parsed = parse_logbook_entry(entry)
    assert parsed is not None
    log = build_correlated_log(parsed, _target())
    assert log.severity_number is SeverityNumber.ERROR


def test_default_state_change_is_info() -> None:
    parsed = parse_logbook_entry(_state_entry(state="on"))
    assert parsed is not None
    log = build_correlated_log(parsed, _target())
    assert log.severity_number is SeverityNumber.INFO


# -- log timestamp is the entry's real HA time -------------------------------


def test_log_timestamp_is_entry_when() -> None:
    parsed = parse_logbook_entry(_state_entry(when_s=1002.25))
    assert parsed is not None
    log = build_correlated_log(parsed, _target())
    assert log.timestamp_nanos == 1002_250_000_000


# -- bound memory ------------------------------------------------------------


def test_run_registry_is_bounded() -> None:
    corr = LogCorrelator(max_runs=3)
    for i in range(10):
        corr.register(_target(context_id=f"ctx-{i}", name=f"auto-{i}"))
    # Only the last 3 runs are retained for name-window matching.
    parsed = parse_logbook_entry(
        {
            "entity_id": "light.x",
            "state": "on",
            "context_name": "auto-1",  # evicted
            "when": 1002.0,
        }
    )
    assert parsed is not None
    assert corr.ingest(parsed) == []
