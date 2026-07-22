"""THE pure function: a Home Assistant ``trace/get`` payload → a list of spans.

This module is deliberately I/O-free and side-effect-free so it can be golden-
tested offline against dumped fixtures (no running house required). The parser
agent fills in :func:`reconstruct` **without touching any other module** — the
contract is the payload dict in, the ``list[SpanSpec]`` out, and the frozen
:class:`SpanSpec` schema below.

Design notes (spec §0A / §0B):
- Home Assistant stores automation traces as a path-keyed dict of
  ``TraceElement`` lists (``trace["trace"]`` maps ``node_path`` →
  ``list[dict]``). Each element carries a real ``timestamp`` (verify on the
  night-one spike, §0B); if present, real per-element **start** times replace
  interpolated starts, which makes parallel & repeat correct.
- Home Assistant stores **no per-step end**. A step's duration is inferred as
  ``(next in-scope event start minus this start)``; a terminal/leaf span's end
  bounds to its parent/trace finish. This is correctly-scoped inference, not
  zero inference — do not claim the heuristic is gone entirely.
- ``span_id`` / ``parent_span_id`` are ordinary tree ids local to one run;
  ``trace_id`` is *not* set here — it is minted per run by :mod:`homeapm.otlp_emit`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StepType(StrEnum):
    """The ``ha.step_type`` enumeration (frozen, spec §0A)."""

    TRIGGER = "trigger"
    CONDITION = "condition"
    CHOOSE = "choose"
    SEQUENCE = "sequence"
    WAIT = "wait"
    REPEAT = "repeat"
    PARALLEL = "parallel"
    SERVICE_CALL = "service_call"


class SpanKind(StrEnum):
    """OTel span kind. Deliberate CLIENT/SERVER pairing draws the service map."""

    SERVER = "server"
    INTERNAL = "internal"
    CLIENT = "client"


class Result(StrEnum):
    """Normalized ``ha.result`` outcome for a span."""

    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class SpanSpec:
    """One reconstructed span carrying the FULL frozen §0A schema.

    This dataclass is the hand-off contract between :func:`reconstruct` and
    :mod:`homeapm.otlp_emit`. It is additive-only: never rename a field (four
    downstream consumers filter/correlate on these names — §0A, risk #4).

    Timestamps are integer nanoseconds since the Unix epoch (OTel convention).

    Structural / tree fields:
        span_id: Run-local unique id for this span.
        parent_span_id: Parent's ``span_id``; ``None`` only for the run root.
        name: Human-readable span name (e.g. ``"choose: night branch"``).
        kind: OTel span kind (drives the service map, §0A).
        service_name: OTel ``service.name`` — ``ha.automation`` for the root and
            structural steps; ``ha.<target domain>`` for service-call children.
        start_unix_nano: Real per-element start (§0B) in ns.
        end_unix_nano: Inferred end in ns; ``end >= start`` always holds.

    Frozen §0A span attributes:
        automation_name: ``automation.name``.
        automation_id: ``automation.id``.
        automation_room: ``automation.room``.
        node_path: ``ha.node_path`` (e.g. ``conditions/0/conditions/1``).
        step_type: ``ha.step_type``.
        context_id: ``ha.context_id`` (feeds logs↔trace correlation, #13).
        run_id: ``ha.run_id``; the sidecar owns run_id → trace_id mapping.
        result: ``ha.result``.
        changed_variables: ``ha.changed_variables`` as a JSON string.
        template_errors: ``ha.template_errors`` (``None`` if none).
        peer_service: ``peer.service`` = target domain for CLIENT spans; else ``None``.

    OTel status:
        status_error: True → span status ERROR.
        status_message: Optional status description.
    """

    # --- tree / timing ---
    span_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    service_name: str
    start_unix_nano: int
    end_unix_nano: int

    # --- frozen §0A attributes ---
    automation_name: str
    automation_id: str
    automation_room: str
    node_path: str
    step_type: StepType
    context_id: str
    run_id: str
    result: Result
    changed_variables: str = "{}"
    template_errors: str | None = None
    peer_service: str | None = None

    # --- otel status ---
    status_error: bool = False
    status_message: str | None = None

    # extension hook: additive-only extra attributes, never used to rename above
    extra_attributes: dict[str, str] = field(default_factory=dict)


def reconstruct(payload: dict[str, Any]) -> list[SpanSpec]:
    """Reconstruct a run's span tree from a Home Assistant ``trace/get`` payload.

    The returned list satisfies these invariants (enforced by the golden tests):

    - exactly one root span (``parent_span_id is None``);
    - every non-root ``parent_span_id`` refers to a ``span_id`` in the list;
    - starts are monotonic in traversal order and every ``end >= start``;
    - every span carries a non-empty ``automation_name`` and ``node_path``.

    Args:
        payload: The ``result`` object of a ``trace/get`` WebSocket response
            (or an equivalent dumped fixture), containing at least ``trace``,
            ``context``, ``config`` and ``run_id`` keys.

    Returns:
        Spans in deterministic pre-order (root first). ``trace_id`` is not set
        here — :mod:`homeapm.otlp_emit` mints it per ``run_id``.

    Raises:
        NotImplementedError: Until the night-one spike lands the algorithm.
    """
    raise NotImplementedError(
        "reconstruct() is implemented by the parser agent after the §0B "
        "timestamp spike; see module docstring for the frozen contract."
    )
