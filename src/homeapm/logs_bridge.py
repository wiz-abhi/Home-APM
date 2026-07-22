"""Logs -> trace correlation (spec #13, trimmed scope).

This module turns Home Assistant *logbook* entries into OTLP **log records** that
carry the ``trace_id`` (and root ``span_id``) of the automation run that produced
them, so a log line in SigNoz is one click away from its flame graph (demo Beat
1.5). It also exports the sidecar's own operational logs over OTLP.

Scope (deliberately trimmed per spec #13): **own-run** ``context.id`` linkage
only. We do not walk ``context.parent_id`` up the chain (the null-originating-
context rabbit hole, HA #68047). Home Assistant's logbook stream already resolves
the owning automation for a caused state change and hands it to us in two shapes:

- an **automation-trigger** entry carries ``context_id`` == the run's context id
  (``ha.context_id`` per §0A) and ``domain: automation``;
- a **caused state-change** entry carries ``context_id: null`` but names its
  automation via ``context_name`` (the automation alias) + ``context_entity_id``.

So correlation is: match on ``context_id`` when present, else match the entry's
``context_name`` to a registered run whose time window contains the entry. No
parent-id graph walking is involved.

Timing note: an automation's trace is only emitted (and thus registered here)
*after* the run finishes, while its logbook entries stream in real time — i.e.
most entries arrive **before** their trace exists. The correlator therefore
buffers unresolved entries and flushes them when the matching run registers, so
no correlation is lost to that race and no entry is mislinked to a *previous* run
of the same automation.

I/O boundary: the correlation + record-shaping core (:class:`LogCorrelator`,
:func:`parse_logbook_entry`, :func:`build_correlated_log`) is pure and offline-
testable. Only :class:`LogsBridge` and :func:`install_sidecar_log_export` touch
the OTel SDK / network.
"""

from __future__ import annotations

import logging
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

from homeapm.config import Config

log = logging.getLogger("homeapm.logs")

# Logbook logs are stamped with the same service as the automation traces so the
# "ha.automation" service in SigNoz shows traces AND logs side by side.
_LOGBOOK_SERVICE = "ha.automation"
# The sidecar's own operational logs get a distinct, self-describing service.
_SIDECAR_SERVICE = "ha.sidecar"

_MAX_RUNS = 500
_MAX_PENDING = 2000
# A caused state change can land just after the run's inferred finish; allow slack.
_MATCH_SLACK_NANOS = 5 * 1_000_000_000

_UNAVAILABLE_STATES = frozenset({"unavailable", "unknown"})
_ERROR_TOKENS = ("error", "failed", "failure", "exception")


@dataclass(frozen=True, slots=True)
class LinkTarget:
    """A registered automation run a log can be correlated to.

    Attributes:
        context_id: The run's ``ha.context_id`` (§0A).
        automation_name: The automation alias (matches a child entry's
            ``context_name``).
        trace_id: 128-bit trace id minted for the run (int).
        span_id: 64-bit root span id of the run (int); logs anchor to the root.
        room: ``automation.room`` copied onto correlated logs.
        start_nanos: Run start (epoch ns) — the low edge of the match window.
        finish_nanos: Inferred run finish (epoch ns) — the high edge.
    """

    context_id: str
    automation_name: str
    trace_id: int
    span_id: int
    room: str
    start_nanos: int
    finish_nanos: int


@dataclass(frozen=True, slots=True)
class ParsedEntry:
    """A normalized, correlation-ready view of one logbook entry (pure)."""

    when_nanos: int
    context_id: str | None
    context_name: str | None
    context_entity_id: str | None
    entity_id: str | None
    domain: str | None
    state: str | None
    message: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class CorrelatedLog:
    """A shaped, ready-to-emit OTLP log record (pure output of the core)."""

    timestamp_nanos: int
    trace_id: int
    span_id: int
    severity_number: SeverityNumber
    severity_text: str
    body: str
    attributes: dict[str, str] = field(default_factory=dict)


def parse_logbook_entry(entry: dict[str, Any]) -> ParsedEntry | None:
    """Normalize one ``logbook/event_stream`` entry, or ``None`` if unusable.

    ``when`` (unix epoch seconds, float) becomes integer epoch nanoseconds — the
    log's real timestamp. Entries without a ``when`` cannot be time-correlated
    and are dropped.
    """
    when = entry.get("when")
    if not isinstance(when, int | float):
        return None
    return ParsedEntry(
        when_nanos=int(float(when) * 1_000_000_000),
        context_id=_str_or_none(entry.get("context_id")),
        context_name=_str_or_none(entry.get("context_name")),
        context_entity_id=_str_or_none(entry.get("context_entity_id")),
        entity_id=_str_or_none(entry.get("entity_id")),
        domain=_str_or_none(entry.get("domain")),
        state=_str_or_none(entry.get("state")),
        message=_str_or_none(entry.get("message")),
        name=_str_or_none(entry.get("name")),
    )


def build_correlated_log(parsed: ParsedEntry, target: LinkTarget) -> CorrelatedLog:
    """Shape a matched (entry, run) pair into an emittable log record (pure)."""
    severity_number, severity_text = _severity(parsed)
    attrs: dict[str, str] = {"log.source": "ha.logbook"}
    if parsed.entity_id:
        attrs["entity_id"] = parsed.entity_id
    domain = parsed.domain or _domain_of(parsed.entity_id)
    if domain:
        attrs["domain"] = domain
    attrs["automation.name"] = parsed.name or parsed.context_name or target.automation_name
    if target.room:
        attrs["automation.room"] = target.room
    attrs["ha.context_id"] = parsed.context_id or target.context_id
    attrs["ha.correlation"] = "context_id" if parsed.context_id else "automation_name"
    return CorrelatedLog(
        timestamp_nanos=parsed.when_nanos,
        trace_id=target.trace_id,
        span_id=target.span_id,
        severity_number=severity_number,
        severity_text=severity_text,
        body=_body(parsed),
        attributes=attrs,
    )


def _body(parsed: ParsedEntry) -> str:
    """Human-readable log body, e.g. 'light.hallway -> on by automation ...'."""
    if parsed.domain == "automation" and parsed.message:
        subject = parsed.name or parsed.entity_id or "automation"
        return f"{subject}: {parsed.message}"
    if parsed.state is not None:
        who = f" by automation {parsed.context_name}" if parsed.context_name else ""
        return f"{parsed.entity_id} → {parsed.state}{who}"
    if parsed.message:
        return parsed.message
    return parsed.entity_id or "logbook event"


def _severity(parsed: ParsedEntry) -> tuple[SeverityNumber, str]:
    """Map an entry to an OTel severity.

    Note: HA 2026.7.3 does not expose ``system_log/subscribe`` (verified —
    ``unknown_command``), so the ERROR path keys off error tokens in the entry
    text rather than a dedicated error stream; ``unavailable``/``unknown`` states
    surface as WARN.
    """
    text = (parsed.message or "").lower()
    if any(tok in text for tok in _ERROR_TOKENS):
        return SeverityNumber.ERROR, "ERROR"
    if parsed.state is not None and parsed.state.lower() in _UNAVAILABLE_STATES:
        return SeverityNumber.WARN, "WARN"
    return SeverityNumber.INFO, "INFO"


class LogCorrelator:
    """Bounded run registry + pending-entry buffer that links logs to traces.

    Two indices, both bounded (LRU / ``maxlen``) to keep memory flat under a long-
    running sidecar:

    - ``_by_ctx``: ``context_id -> LinkTarget`` for direct trigger-entry matches;
    - ``_runs``: a recency deque scanned for ``context_name`` + time-window
      matches (caused state changes whose ``context_id`` is null).

    Unresolved entries are buffered and re-matched when a run registers.
    """

    def __init__(self, max_runs: int = _MAX_RUNS, max_pending: int = _MAX_PENDING) -> None:
        self._max_runs = max_runs
        self._by_ctx: OrderedDict[str, LinkTarget] = OrderedDict()
        self._runs: deque[LinkTarget] = deque(maxlen=max_runs)
        self._pending: deque[ParsedEntry] = deque(maxlen=max_pending)

    def register(self, target: LinkTarget) -> list[CorrelatedLog]:
        """Register a finished run and flush any buffered entries it now matches."""
        self._by_ctx[target.context_id] = target
        self._by_ctx.move_to_end(target.context_id)
        while len(self._by_ctx) > self._max_runs:
            self._by_ctx.popitem(last=False)
        self._runs.append(target)

        emitted: list[CorrelatedLog] = []
        retained: deque[ParsedEntry] = deque(maxlen=self._pending.maxlen)
        for parsed in self._pending:
            match = self._match(parsed)
            if match is not None:
                emitted.append(build_correlated_log(parsed, match))
            else:
                retained.append(parsed)
        self._pending = retained
        return emitted

    def ingest(self, parsed: ParsedEntry) -> list[CorrelatedLog]:
        """Correlate one entry now, or buffer it for a later matching run."""
        match = self._match(parsed)
        if match is not None:
            return [build_correlated_log(parsed, match)]
        self._pending.append(parsed)
        return []

    def _match(self, parsed: ParsedEntry) -> LinkTarget | None:
        """Resolve an entry to a run: exact context id, else name + time window."""
        if parsed.context_id is not None:
            target = self._by_ctx.get(parsed.context_id)
            if target is not None:
                return target
        name = parsed.context_name
        if name is not None:
            for target in reversed(self._runs):
                if (
                    target.automation_name == name
                    and target.start_nanos - _MATCH_SLACK_NANOS <= parsed.when_nanos
                    and parsed.when_nanos <= target.finish_nanos + _MATCH_SLACK_NANOS
                ):
                    return target
        return None

    @property
    def pending_count(self) -> int:
        """Number of buffered, not-yet-correlated entries (for tests/metrics)."""
        return len(self._pending)


class LogsBridge:
    """Owns the OTLP log pipeline and drives correlation from logbook batches.

    Args:
        config: Resolved sidecar configuration (OTLP endpoint, namespace, env).
        correlator: Injectable correlator (defaults to a fresh bounded one).
    """

    def __init__(self, config: Config, correlator: LogCorrelator | None = None) -> None:
        self._config = config
        self._correlator = correlator or LogCorrelator()
        resource = Resource.create(
            {
                "service.name": _LOGBOOK_SERVICE,
                "service.namespace": config.service_namespace,
                "deployment.environment": config.environment,
            }
        )
        self._provider = LoggerProvider(resource=resource)
        self._provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=config.otlp_logs_url))
        )
        self._logger = self._provider.get_logger("homeapm.logs_bridge")
        self._emitted_total = 0

    @property
    def emitted_total(self) -> int:
        """Count of correlated log records emitted (for logging/tests)."""
        return self._emitted_total

    def register_run(
        self,
        *,
        context_id: str,
        automation_name: str,
        trace_id: int,
        span_id: int,
        room: str,
        start_nanos: int,
        finish_nanos: int,
    ) -> None:
        """Register a converted run; flush + emit any buffered logs it matches."""
        target = LinkTarget(
            context_id=context_id,
            automation_name=automation_name,
            trace_id=trace_id,
            span_id=span_id,
            room=room,
            start_nanos=start_nanos,
            finish_nanos=finish_nanos,
        )
        for correlated in self._correlator.register(target):
            self._emit(correlated)

    def handle_logbook_batch(self, events: list[dict[str, Any]]) -> None:
        """Ingest one ``logbook/event_stream`` batch, emitting correlated logs."""
        for entry in events:
            if not isinstance(entry, dict):
                continue
            parsed = parse_logbook_entry(entry)
            if parsed is None:
                continue
            for correlated in self._correlator.ingest(parsed):
                self._emit(correlated)

    def _emit(self, correlated: CorrelatedLog) -> None:
        """Emit one correlated OTLP log, stamping the run's trace + span id.

        The trace context is carried via a non-recording span placed in a
        :class:`~opentelemetry.context.Context`; the logs SDK copies its
        ``trace_id``/``span_id`` onto the exported record (public API — no
        private ``LogRecord`` construction).
        """
        span_context = SpanContext(
            trace_id=correlated.trace_id,
            span_id=correlated.span_id,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        context = set_span_in_context(NonRecordingSpan(span_context))
        self._logger.emit(
            timestamp=correlated.timestamp_nanos,
            observed_timestamp=correlated.timestamp_nanos,
            context=context,
            severity_number=correlated.severity_number,
            severity_text=correlated.severity_text,
            body=correlated.body,
            attributes=dict(correlated.attributes),
        )
        self._emitted_total += 1

    def shutdown(self) -> None:
        """Flush and shut down the OTLP log pipeline."""
        self._provider.shutdown()


def install_sidecar_log_export(config: Config) -> LoggerProvider:
    """Export the sidecar's own Python logs over OTLP (spec #13, item 2).

    Attaches an OTel :class:`LoggingHandler` to the root logger under a distinct
    ``ha.sidecar`` service. When a log is emitted inside an active span the
    handler stamps trace context automatically; the sidecar's operational logs
    run outside spans, so they land as ordinary (uncorrelated) logs — still a
    real OTLP logs surface for the "Best Use of SigNoz" story.

    Returns:
        The created :class:`LoggerProvider` so the caller can shut it down.
    """
    resource = Resource.create(
        {
            "service.name": _SIDECAR_SERVICE,
            "service.namespace": config.service_namespace,
            "deployment.environment": config.environment,
        }
    )
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=config.otlp_logs_url))
    )
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    logging.getLogger().addHandler(handler)
    return provider


def _str_or_none(value: Any) -> str | None:
    """Coerce a JSON value to a non-empty string, or ``None``."""
    if isinstance(value, str) and value:
        return value
    return None


def _domain_of(entity_id: str | None) -> str | None:
    """The domain prefix of an ``entity_id`` (``light.hallway`` -> ``light``)."""
    if entity_id and "." in entity_id:
        return entity_id.split(".", 1)[0]
    return None
