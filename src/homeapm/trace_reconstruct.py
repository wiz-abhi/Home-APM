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

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
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


_ROOT_PATH = "__root__"

# A path's first segment ("action"/"trigger"/"condition") names the config
# collection it indexes into. Real HA `trace/get` config uses the singular key
# ("action": [...]); some dumps use the plural — accept either, in order.
_ROOT_KEY_CANDIDATES: dict[str, tuple[str, ...]] = {
    "trigger": ("trigger", "triggers"),
    "action": ("action", "actions"),
    "condition": ("condition", "conditions"),
}


def reconstruct(payload: dict[str, Any]) -> list[SpanSpec]:
    """Reconstruct a run's span tree from a Home Assistant ``trace/get`` payload.

    The returned list satisfies these invariants (enforced by the golden tests):

    - exactly one root span (``parent_span_id is None``);
    - every non-root ``parent_span_id`` refers to a ``span_id`` in the list;
    - starts are monotonic in traversal order and every ``end >= start``;
    - every span carries a non-empty ``automation_name`` and ``node_path``.

    Args:
        payload: A ``trace/get`` WebSocket response. Either the full envelope
            ``{"id", "type", "success", "result": {...}}`` or the already-
            unwrapped ``result`` object is accepted; both carry ``trace``,
            ``context``, ``config`` and ``run_id``.

    Returns:
        Spans in deterministic start order (root first). ``trace_id`` is not set
        here — :mod:`homeapm.otlp_emit` mints it per ``run_id``.

    Raises:
        ValueError: If the payload carries no recognizable ``trace`` object.
    """
    data = _unwrap(payload)

    trace = data.get("trace")
    if not isinstance(trace, dict):
        raise ValueError("payload has no 'trace' object to reconstruct")

    config_raw = data.get("config")
    config: dict[str, Any] = config_raw if isinstance(config_raw, dict) else {}
    run_id = str(data.get("run_id") or "unknown_run")
    run_context_id = _dig(data, "context", "id") or run_id
    automation_id = str(config.get("id") or data.get("item_id") or "unknown")
    automation_name = str(config.get("alias") or automation_id)
    room = _room_of(config)

    ts_raw = data.get("timestamp")
    ts: dict[str, Any] = ts_raw if isinstance(ts_raw, dict) else {}
    start_raw, finish_raw = ts.get("start"), ts.get("finish")
    run_start = _iso_to_nanos(start_raw) if isinstance(start_raw, str) else None
    run_finish = _iso_to_nanos(finish_raw) if isinstance(finish_raw, str) else None

    events = _flatten_events(trace)
    known = [e.start for e in events if e.has_start]
    if run_start is None:
        run_start = min(known, default=0)
    if run_finish is None:
        run_finish = max(known, default=run_start)
    _fill_missing_starts(events, run_start)
    _apply_declared_delays(config, events)

    # Synthetic run root — HA has no single root element; it owns the whole run.
    root = _Event(
        path=_ROOT_PATH,
        segs=(_ROOT_PATH,),
        seq=0,
        start=min(run_start, *(e.start for e in events)) if events else run_start,
        result=None,
        changed_variables=None,
        error=None,
        is_root=True,
    )
    all_events = [root, *events]
    all_events.sort(key=lambda e: (e.start, len(e.segs), e.path, e.seq))

    _assign_parents(all_events, root)
    ends, end_origin = _compute_ends(all_events, root, run_finish)

    common: dict[str, str] = {
        "automation_name": automation_name,
        "automation_id": automation_id,
        "automation_room": room,
        "context_id": str(run_context_id),
        "run_id": run_id,
    }

    spans: list[SpanSpec] = []
    for e in all_events:
        span_id = _span_id(run_id, e.path, e.seq)
        parent_span_id = None if e.is_root else _span_id(run_id, e.parent.path, e.parent.seq)
        info = _classify(config, e)
        result, status_error, status_message, template_errors = _outcome(e, info.step_type)

        extra: dict[str, str] = {}
        if e.multiplicity > 1:
            extra["ha.iteration"] = str(e.seq)
        branch = _parallel_branch(e.segs)
        if branch is not None:
            extra["ha.parallel_branch"] = str(branch)
        # Duration provenance: HA records no per-step end, so most ends are
        # inferred. Say which, per span, rather than presenting every bar as
        # measured (see README "Honest limits").
        extra["ha.end_inferred"] = end_origin.get(id(e), "run_finish")
        if isinstance(e.result, dict) and e.result.get("choice") is not None:
            extra["ha.choice"] = str(e.result["choice"])

        spans.append(
            SpanSpec(
                span_id=span_id,
                parent_span_id=parent_span_id,
                name=info.name,
                kind=info.kind,
                service_name=info.service_name,
                start_unix_nano=e.start,
                end_unix_nano=max(ends[id(e)], e.start),
                node_path=e.path,
                step_type=info.step_type,
                result=result,
                changed_variables=_json_or_empty(e.changed_variables),
                template_errors=template_errors,
                peer_service=info.peer_service,
                status_error=status_error,
                status_message=status_message,
                extra_attributes=extra,
                **common,
            )
        )
    return spans


# ---------------------------------------------------------------------------
# internal model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Event:
    """One executed ``TraceElement`` instance (mutable during tree building)."""

    path: str
    segs: tuple[str, ...]
    seq: int  # index within the path's element list (repeat iteration index)
    start: int  # epoch nanoseconds
    result: Any
    changed_variables: Any
    error: str | None
    is_root: bool = False
    multiplicity: int = 1  # how many instances share this path
    has_start: bool = True  # False when the element carried no parsable timestamp
    recorded_end: int | None = None  # a real end HA recorded (e.g. delay result)
    end_source: str = "recorded"  # provenance label when recorded_end is set
    parent: _Event = field(init=False, repr=False, default=None)  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class _ClassifyResult:
    step_type: StepType
    name: str
    service_name: str
    kind: SpanKind
    peer_service: str | None


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept either the full WS envelope or the bare ``result`` object."""
    if "trace" in payload:
        return payload
    inner = payload.get("result")
    if isinstance(inner, dict):
        return inner
    return payload


def _flatten_events(trace: dict[str, Any]) -> list[_Event]:
    """Explode the path-keyed ``trace`` dict into one :class:`_Event` per instance."""
    events: list[_Event] = []
    for path, elements in trace.items():
        if not isinstance(elements, list):
            continue
        multiplicity = len(elements)
        segs = tuple(path.split("/"))
        for seq, el in enumerate(elements):
            if not isinstance(el, dict):
                continue
            ts = el.get("timestamp")
            parsed = _iso_to_nanos(ts) if isinstance(ts, str) else None
            events.append(
                _Event(
                    path=path,
                    segs=segs,
                    seq=seq,
                    start=parsed if parsed is not None else 0,
                    result=el.get("result"),
                    changed_variables=el.get("changed_variables"),
                    error=_error_text(el),
                    multiplicity=multiplicity,
                    has_start=parsed is not None,
                    recorded_end=_recorded_end(el, parsed, segs),
                )
            )
    return events


def _recorded_end(el: dict[str, Any], start: int | None, segs: tuple[str, ...]) -> int | None:
    """A *real* end derived from the element's own ``result``, where HA records one.

    A ``delay`` step reports ``result: {"delay": <seconds>, "done": true}`` — the
    step's own duration. Using it beats the scope-boundary fallback, under which
    the trailing step of any scope inherits its parent's boundary and reads far
    wider than it ran (a 1-second delay closing a ``repeat`` iteration would
    otherwise stretch across the whole loop).

    **Not trusted inside a ``parallel`` block.** Concurrent branches race when
    Home Assistant attaches results to trace elements, and the values land on the
    wrong elements: in the committed ``good_night`` fixture the two branch delays
    report each other's durations (3.0 s and 5.0 s swapped), while the measured
    inter-step elapsed agrees with the *config*, not with ``result.delay``. Inside
    ``parallel`` we therefore keep the boundary inference, which is imprecise but
    never attributes one branch's duration to another.
    """
    if start is None or "parallel" in segs or not isinstance(el.get("result"), dict):
        return None
    seconds = el["result"].get("delay")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
        return start + int(seconds * 1_000_000_000)
    return None


def _apply_declared_delays(config: dict[str, Any], events: list[_Event]) -> None:
    """Bound ``delay`` steps by their *configured* duration where none was recorded.

    This is what rescues delays inside a ``parallel`` block, where
    :func:`_recorded_end` deliberately refuses ``result.delay`` because HA races
    the value onto the wrong branch. The config is static, so it cannot race —
    a ``delay: {seconds: 3}`` step ran ~3 s no matter which branch it sat in.
    Marked separately (``config_declared``) so it is never mistaken for a
    measurement.
    """
    for e in events:
        if e.recorded_end is not None:
            continue
        node = _walk_config(config, e.segs)
        if not isinstance(node, dict) or "delay" not in node:
            continue
        nanos = _delay_nanos(node["delay"])
        if nanos is not None:
            e.recorded_end = e.start + nanos
            e.end_source = "config_declared"


def _delay_nanos(delay: Any) -> int | None:
    """Total nanoseconds for a HA ``delay`` value (mapping form or bare seconds)."""
    if isinstance(delay, (int, float)) and not isinstance(delay, bool):
        return int(delay * 1_000_000_000) if delay >= 0 else None
    if not isinstance(delay, dict):
        return None
    weights = {
        "milliseconds": 1_000_000,
        "seconds": 1_000_000_000,
        "minutes": 60_000_000_000,
        "hours": 3_600_000_000_000,
        "days": 86_400_000_000_000,
    }
    total = 0
    seen = False
    for unit, weight in weights.items():
        value = delay.get(unit)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value * weight)
            seen = True
    return total if seen and total >= 0 else None


def _fill_missing_starts(events: list[_Event], fallback: int) -> None:
    """Anchor any element that carried no parsable ``timestamp``.

    Home Assistant stamps every ``TraceElement`` (spec §0B), so this is purely
    defensive — but without it a single missing timestamp anchors that span at
    the Unix epoch, which drags the run root to 1970 and renders the entire
    flame graph as one ~56-year bar.
    """
    last = fallback
    for e in sorted(events, key=lambda ev: (ev.segs, ev.seq)):
        if e.has_start:
            last = e.start
        else:
            e.start = last


def _assign_parents(all_events: list[_Event], root: _Event) -> None:
    """Attach each non-root event to the deepest, most-recent enclosing event."""
    for e in all_events:
        if e.is_root:
            continue
        best: _Event | None = None
        for f in all_events:
            if f is e or f.is_root:
                continue
            if (
                f.start <= e.start
                and _is_strict_prefix(f.segs, e.segs)
                and (
                    best is None
                    or len(f.segs) > len(best.segs)
                    or (len(f.segs) == len(best.segs) and f.start > best.start)
                )
            ):
                best = f
        e.parent = best or root


def _compute_ends(
    all_events: list[_Event], root: _Event, run_finish: int
) -> tuple[dict[int, int], dict[int, str]]:
    """Bound every span's end (§0B) via two passes over the reconstructed tree.

    A step has no recorded end in Home Assistant, so its end is inferred as the
    start of the next step *in the same scope*; a scope's last step bounds to its
    parent's boundary (ultimately the run finish). "Scope" is the set of siblings
    under one parent — but ``parallel`` branches are split into separate scopes by
    branch index, so concurrent branches never truncate each other. A container
    span ends when its last descendant ends.
    """
    children: dict[int, list[_Event]] = {}
    for e in all_events:
        if not e.is_root:
            children.setdefault(id(e.parent), []).append(e)

    # Pass 1 (top-down): the outer boundary each node must finish by.
    scope_end: dict[int, int] = {id(root): run_finish}
    scope_kind: dict[int, str] = {}
    for parent in sorted(all_events, key=lambda e: (len(e.segs), e.start)):
        kids = children.get(id(parent))
        if not kids:
            continue
        parent_scope = scope_end.get(id(parent), run_finish)
        groups: dict[str | None, list[_Event]] = {}
        for kid in kids:
            groups.setdefault(_branch_key(parent, kid), []).append(kid)
        for members in groups.values():
            members.sort(key=lambda e: (e.start, e.path, e.seq))
            for i, member in enumerate(members):
                last = i + 1 >= len(members)
                boundary = parent_scope if last else members[i + 1].start
                scope_end[id(member)] = max(boundary, member.start)
                scope_kind[id(member)] = "parent_boundary" if last else "next_sibling"

    # Pass 2 (bottom-up): containers to last child, leaves to their scope boundary.
    # ``how`` records the provenance of each end so a span can advertise whether
    # its duration was measured or inferred (``ha.end_inferred``).
    ends: dict[int, int] = {}
    how: dict[int, str] = {}

    def resolve(e: _Event) -> int:
        cached = ends.get(id(e))
        if cached is not None:
            return cached
        kids = children.get(id(e), [])
        if e.recorded_end is not None:
            end = e.recorded_end
            origin = e.end_source
            if kids:
                end = max(end, *(resolve(k) for k in kids))
        elif kids:
            end = max(resolve(k) for k in kids)
            origin = "descendants"
        elif e.is_root:
            end = run_finish
            origin = "run_finish"
        else:
            end = scope_end.get(id(e), run_finish)
            origin = scope_kind.get(id(e), "run_finish")
        end = max(end, e.start)
        ends[id(e)] = end
        how[id(e)] = origin
        return end

    for e in all_events:
        resolve(e)
    return ends, how


def _branch_key(parent: _Event, child: _Event) -> str | None:
    """Scope discriminator: distinct per ``parallel`` branch, ``None`` otherwise."""
    extra = child.segs[len(parent.segs) :]
    if len(extra) >= 2 and extra[0] == "parallel":
        return f"parallel:{extra[1]}"
    return None


def _classify(config: dict[str, Any], e: _Event) -> _ClassifyResult:
    """Determine step type, name and service routing for one event."""
    if e.is_root:
        alias = str(config.get("alias") or config.get("id") or "automation")
        return _ClassifyResult(StepType.SEQUENCE, alias, "ha.automation", SpanKind.SERVER, None)

    if e.segs and e.segs[0] == "trigger":
        return _ClassifyResult(
            StepType.TRIGGER, "trigger", "ha.automation", SpanKind.INTERNAL, None
        )

    node = _walk_config(config, e.segs)
    if isinstance(node, dict):
        concrete = _classify_config_node(node)
        if concrete is not None:
            return concrete

    # Authoritative fallback: a real service-call element carries its own
    # domain/service in ``result.params`` even when the config walk misses.
    from_result = _service_from_result(e.result)
    if from_result is not None:
        return from_result

    return _classify_by_position(e)


def _service_from_result(result: Any) -> _ClassifyResult | None:
    """Recognize a service call from a trace element's ``result.params``."""
    if not isinstance(result, dict):
        return None
    params = result.get("params")
    if not isinstance(params, dict):
        return None
    domain, service = params.get("domain"), params.get("service")
    if isinstance(domain, str) and domain and isinstance(service, str) and service:
        return _ClassifyResult(
            StepType.SERVICE_CALL, f"{domain}.{service}", f"ha.{domain}", SpanKind.CLIENT, domain
        )
    return None


def _classify_config_node(node: dict[str, Any]) -> _ClassifyResult | None:
    """Classify a concrete config action dict (service call, choose, repeat, …)."""
    service = node.get("action") or node.get("service")
    if isinstance(service, str) and "." in service and "choose" not in node:
        domain = service.split(".", 1)[0]
        return _ClassifyResult(
            StepType.SERVICE_CALL, service, f"ha.{domain}", SpanKind.CLIENT, domain
        )
    if "choose" in node:
        return _ClassifyResult(StepType.CHOOSE, "choose", "ha.automation", SpanKind.INTERNAL, None)
    if "if" in node:
        return _ClassifyResult(StepType.CHOOSE, "if", "ha.automation", SpanKind.INTERNAL, None)
    if "repeat" in node:
        return _ClassifyResult(StepType.REPEAT, "repeat", "ha.automation", SpanKind.INTERNAL, None)
    if "parallel" in node:
        return _ClassifyResult(
            StepType.PARALLEL, "parallel", "ha.automation", SpanKind.INTERNAL, None
        )
    if "wait_for_trigger" in node:
        return _ClassifyResult(
            StepType.WAIT, "wait_for_trigger", "ha.automation", SpanKind.INTERNAL, None
        )
    if "wait_template" in node:
        return _ClassifyResult(
            StepType.WAIT, "wait_template", "ha.automation", SpanKind.INTERNAL, None
        )
    if "delay" in node:
        return _ClassifyResult(
            StepType.WAIT, _delay_name(node["delay"]), "ha.automation", SpanKind.INTERNAL, None
        )
    if "condition" in node:
        cond = node.get("condition")
        name = f"condition: {cond}" if isinstance(cond, str) else "condition"
        return _ClassifyResult(StepType.CONDITION, name, "ha.automation", SpanKind.INTERNAL, None)
    if "variables" in node:
        return _ClassifyResult(
            StepType.SEQUENCE, "variables", "ha.automation", SpanKind.INTERNAL, None
        )
    if "stop" in node:
        return _ClassifyResult(StepType.SEQUENCE, "stop", "ha.automation", SpanKind.INTERNAL, None)
    return None


def _classify_by_position(e: _Event) -> _ClassifyResult:
    """Fallback classification for structural nodes HA emits with no config match."""
    segs = e.segs
    last = segs[-1]
    prev = segs[-2] if len(segs) >= 2 else last
    internal = ("ha.automation", SpanKind.INTERNAL, None)

    # Structural keyword as the final segment (`.../if`, `.../parallel`, ...).
    keyword = {
        "if": (StepType.CONDITION, "if"),
        "choose": (StepType.CHOOSE, "choose"),
        "parallel": (StepType.PARALLEL, "parallel"),
        "repeat": (StepType.REPEAT, "repeat"),
    }.get(last)
    if keyword is not None:
        return _ClassifyResult(keyword[0], keyword[1], *internal)

    # Indexed container keyed by the second-to-last segment (`.../choose/0`).
    if prev == "choose":
        return _ClassifyResult(StepType.CHOOSE, f"choose branch {last}", *internal)
    if prev in ("default", "then", "else"):
        return _ClassifyResult(StepType.CHOOSE, prev, *internal)
    if prev in ("conditions", "condition"):
        return _ClassifyResult(StepType.CONDITION, "condition", *internal)
    if prev == "parallel":
        return _ClassifyResult(StepType.PARALLEL, f"parallel branch {last}", *internal)

    # A service-execution / state-change leaf under a call: label it by entity.
    entity = _entity_of(e.changed_variables)
    if entity is not None:
        return _ClassifyResult(StepType.SEQUENCE, entity, *internal)

    if prev in ("sequence", "action"):
        return _ClassifyResult(StepType.SEQUENCE, f"{prev}[{last}]", *internal)
    return _ClassifyResult(StepType.SEQUENCE, f"{prev}[{last}]", *internal)


def _entity_of(changed_variables: Any) -> str | None:
    """The affected ``entity_id`` from a state-change element, if present."""
    if isinstance(changed_variables, dict):
        this = changed_variables.get("this")
        if isinstance(this, dict):
            entity = this.get("entity_id")
            if isinstance(entity, str) and entity:
                return entity
    return None


def _walk_config(config: dict[str, Any], segs: tuple[str, ...]) -> Any:
    """Navigate ``config`` along a node path; return the target node or ``None``."""
    if not segs:
        return None
    node: Any = None
    for key in _ROOT_KEY_CANDIDATES.get(segs[0], (segs[0],)):
        if key in config:
            node = config[key]
            break
    for seg in segs[1:]:
        if node is None:
            return None
        if isinstance(node, list):
            if seg.isdigit() and int(seg) < len(node):
                node = node[int(seg)]
            elif seg == "sequence":
                # HA inserts an implicit ``sequence`` path segment over a branch
                # body that is a bare list in config (e.g. parallel branches);
                # skip it so the following index lands on the list.
                continue
            else:
                return None
        elif isinstance(node, dict):
            node = node.get(seg)
        else:
            return None
    return node


def _outcome(e: _Event, step_type: StepType) -> tuple[Result, bool, str | None, str | None]:
    """Map an event to (ha.result, status_error, status_message, template_errors)."""
    if e.error:
        is_template = "template" in e.error.lower()
        template_errors = e.error if is_template else None
        return Result.ERROR, True, e.error, template_errors
    if (
        step_type is StepType.WAIT
        and isinstance(e.result, dict)
        and e.result.get("timeout") is True
    ):
        return Result.TIMEOUT, False, None, None
    # A condition that evaluated false, or a choose branch that was not taken,
    # is SKIPPED — not OK. Without this the taken and untaken branches are
    # indistinguishable, which is the exact question the tool exists to answer.
    if (
        step_type in (StepType.CONDITION, StepType.CHOOSE)
        and isinstance(e.result, dict)
        and e.result.get("result") is False
    ):
        return Result.SKIPPED, False, None, None
    return Result.OK, False, None, None


def _parallel_branch(segs: tuple[str, ...]) -> int | None:
    """The *nearest-enclosing* ``parallel`` branch index for this path, if any.

    Nested ``parallel`` blocks must key on the innermost branch: returning the
    outermost index would file two spans sitting in different inner lanes under
    the same ``ha.parallel_branch`` value, silently merging distinct concurrency
    lanes in any SigNoz group-by. This mirrors :func:`_branch_key`.
    """
    branch: int | None = None
    for i, seg in enumerate(segs):
        if seg == "parallel" and i + 1 < len(segs) and segs[i + 1].isdigit():
            branch = int(segs[i + 1])
    return branch


def _is_strict_prefix(prefix: tuple[str, ...], segs: tuple[str, ...]) -> bool:
    """Segment-wise strict prefix test (``action/0`` prefixes ``action/0/choose/0``)."""
    return len(prefix) < len(segs) and segs[: len(prefix)] == prefix


def _error_text(el: dict[str, Any]) -> str | None:
    """Extract an error string from a trace element, if any."""
    err = el.get("error")
    if isinstance(err, str) and err:
        return err
    if isinstance(el.get("result"), dict):
        rerr = el["result"].get("error")
        if isinstance(rerr, str) and rerr:
            return rerr
    return None


def _room_of(config: dict[str, Any]) -> str:
    """Read ``automation.room`` from the automation-level ``variables`` block."""
    variables = config.get("variables")
    if isinstance(variables, dict):
        room = variables.get("room")
        if isinstance(room, str):
            return room
    return ""


def _delay_name(delay: Any) -> str:
    """Human-readable name for a delay step (``delay 2s`` where derivable)."""
    if isinstance(delay, dict):
        secs = delay.get("seconds")
        if secs is not None:
            return f"delay {secs}s"
    return "delay"


def _dig(obj: Any, *keys: str) -> Any:
    """Nested ``dict.get`` chain; ``None`` if any hop is missing."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _json_or_empty(value: Any) -> str:
    """Serialize ``changed_variables`` to a stable JSON string (``"{}"`` if empty)."""
    if not value:
        return "{}"
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return "{}"


def _span_id(run_id: str, node_path: str, seq: int) -> str:
    """Deterministic 64-bit span id (16 hex) from run + path + instance index."""
    digest = hashlib.sha1(f"{run_id}|{node_path}|{seq}".encode()).digest()
    return digest[:8].hex()


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _iso_to_nanos(ts: str) -> int:
    """Parse an ISO-8601 timestamp to integer epoch nanoseconds (exact µs)."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = dt.astimezone(UTC) - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
