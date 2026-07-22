"""Turn reconstructed :class:`SpanSpec` lists into OTel spans and export via OTLP.

This module owns the ``run_id → trace_id`` map (spec §0A): every span from one
run shares a single sidecar-minted trace id, so a run renders as one flame
graph and #13 can later link a log's ``context.id`` to that ``trace_id``.

It maps each :class:`~homeapm.trace_reconstruct.SpanSpec` onto an OTel
:class:`~opentelemetry.sdk.trace.ReadableSpan` carrying the frozen §0A
attributes and the deliberate CLIENT/SERVER ``span.kind`` pairing that draws the
house service map (#15).

The ``service.name``-per-HA-domain requirement is met without one tracer
provider per domain: a distinct :class:`~opentelemetry.sdk.resources.Resource`
(``service.name`` per domain) is attached to each :class:`ReadableSpan`, and the
whole run is handed to a single :class:`OTLPSpanExporter.export` call. The OTLP
encoder groups spans by resource into separate ``ResourceSpans`` in one request,
while SigNoz stitches them back into a single trace by shared ``trace_id`` — the
foundation of the house service map.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, TraceFlags
from opentelemetry.trace import SpanKind as OTelSpanKind
from opentelemetry.trace.status import Status, StatusCode
from opentelemetry.util.types import AttributeValue

from homeapm.config import Config
from homeapm.trace_reconstruct import SpanKind, SpanSpec

_TRACE_ID_BYTES = 16
_SPAN_ID_BYTES = 8

_SCOPE = InstrumentationScope("homeapm.trace_reconstruct", "0.1.0")

_KIND_MAP: dict[SpanKind, OTelSpanKind] = {
    SpanKind.SERVER: OTelSpanKind.SERVER,
    SpanKind.INTERNAL: OTelSpanKind.INTERNAL,
    SpanKind.CLIENT: OTelSpanKind.CLIENT,
}


class EmitError(RuntimeError):
    """Raised when the OTLP exporter reports a failed export."""


class TraceIdRegistry:
    """Owns the per-run ``run_id → trace_id`` mapping (spec §0A).

    A 128-bit trace id is minted once per ``run_id`` and reused for every span
    in that run and for later log correlation (#13).
    """

    def __init__(self) -> None:
        self._by_run: dict[str, int] = {}

    def trace_id_for(self, run_id: str) -> int:
        """Return (minting once) the 128-bit trace id for ``run_id``."""
        tid = self._by_run.get(run_id)
        if tid is None:
            tid = int.from_bytes(secrets.token_bytes(_TRACE_ID_BYTES), "big")
            self._by_run[run_id] = tid
        return tid

    def known(self, run_id: str) -> bool:
        """Whether a trace id has already been minted for ``run_id``."""
        return run_id in self._by_run


@dataclass(slots=True)
class EmitResult:
    """Outcome of emitting one run's spans."""

    run_id: str
    trace_id_hex: str
    span_count: int
    services: tuple[str, ...] = ()


class OTLPEmitter:
    """Convert :class:`SpanSpec` lists to OTel spans and export over OTLP/HTTP.

    Args:
        config: Resolved sidecar configuration (provides the OTLP endpoint and
            resource namespace).
        registry: Shared trace-id registry (injectable for tests).
    """

    def __init__(self, config: Config, registry: TraceIdRegistry | None = None) -> None:
        self._config = config
        self._registry = registry or TraceIdRegistry()
        self._exporter: SpanExporter = OTLPSpanExporter(endpoint=config.otlp_traces_url)
        self._resources: dict[str, Resource] = {}

    @property
    def registry(self) -> TraceIdRegistry:
        """The owned ``run_id → trace_id`` registry (consumed by #13)."""
        return self._registry

    def emit(self, spans: list[SpanSpec]) -> EmitResult:
        """Export one run's reconstructed spans as a single OTLP trace.

        All spans must share one ``run_id``; the minted trace id is looked up
        (or created) via :attr:`registry`. Parent/child ids from the specs are
        preserved so the tree renders as one flame graph, and each span's
        ``service.name`` resource is attached so the service map draws.

        Args:
            spans: A non-empty list from
                :func:`homeapm.trace_reconstruct.reconstruct`.

        Returns:
            An :class:`EmitResult` describing the exported trace.

        Raises:
            ValueError: If ``spans`` is empty.
            EmitError: If the OTLP exporter reports a non-success result.
        """
        if not spans:
            raise ValueError("emit() requires at least one span")

        run_id = spans[0].run_id
        trace_id = self._registry.trace_id_for(run_id)

        readable = [self._to_readable(s, trace_id) for s in spans]
        result = self._exporter.export(readable)
        if result is not SpanExportResult.SUCCESS:
            raise EmitError(f"OTLP export failed for run {run_id}: {result!r}")

        services = tuple(sorted({s.service_name for s in spans}))
        return EmitResult(
            run_id=run_id,
            trace_id_hex=format(trace_id, "032x"),
            span_count=len(spans),
            services=services,
        )

    def _to_readable(self, spec: SpanSpec, trace_id: int) -> ReadableSpan:
        """Build one :class:`ReadableSpan` from a :class:`SpanSpec`."""
        context = SpanContext(
            trace_id=trace_id,
            span_id=int(spec.span_id, 16),
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        parent = None
        if spec.parent_span_id is not None:
            parent = SpanContext(
                trace_id=trace_id,
                span_id=int(spec.parent_span_id, 16),
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )

        if spec.status_error:
            status = Status(StatusCode.ERROR, spec.status_message)
        else:
            status = Status(StatusCode.UNSET)

        return ReadableSpan(
            name=spec.name,
            context=context,
            parent=parent,
            resource=self._resource_for(spec.service_name),
            attributes=_attributes_for(spec),
            kind=_KIND_MAP[spec.kind],
            status=status,
            start_time=spec.start_unix_nano,
            end_time=spec.end_unix_nano,
            instrumentation_scope=_SCOPE,
        )

    def _resource_for(self, service_name: str) -> Resource:
        """Return (caching) the OTel ``Resource`` bound to ``service_name``.

        A distinct ``service.name`` per HA domain is what lets the SigNoz
        service map draw automation → light/climate/cover (#15).
        """
        resource = self._resources.get(service_name)
        if resource is None:
            resource = Resource.create(
                {
                    "service.name": service_name,
                    "service.namespace": self._config.service_namespace,
                }
            )
            self._resources[service_name] = resource
        return resource

    def shutdown(self) -> None:
        """Flush and shut down the OTLP exporter."""
        self._exporter.shutdown()


def _attributes_for(spec: SpanSpec) -> dict[str, AttributeValue]:
    """Assemble the frozen §0A attribute set for one span."""
    attrs: dict[str, AttributeValue] = {
        "automation.name": spec.automation_name,
        "automation.id": spec.automation_id,
        "automation.room": spec.automation_room,
        "ha.node_path": spec.node_path,
        "ha.step_type": spec.step_type.value,
        "ha.context_id": spec.context_id,
        "ha.run_id": spec.run_id,
        "ha.result": spec.result.value,
        "ha.changed_variables": spec.changed_variables,
    }
    if spec.template_errors:
        attrs["ha.template_errors"] = spec.template_errors
    if spec.peer_service:
        attrs["peer.service"] = spec.peer_service
    for key, value in spec.extra_attributes.items():
        attrs[key] = value
    return attrs


def span_id_hex() -> str:
    """Mint a random 64-bit span id as lowercase hex (helper for emission)."""
    return secrets.token_bytes(_SPAN_ID_BYTES).hex()
