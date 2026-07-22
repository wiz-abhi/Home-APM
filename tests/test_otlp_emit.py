"""Offline contract tests for :class:`homeapm.otlp_emit.OTLPEmitter`.

The emitter creates its own OTLP/HTTP exporter in ``__init__``; these tests swap
it for an :class:`InMemorySpanExporter` and assert the mapping the flame graph
and the house service map depend on: one trace id per run, preserved span/parent
ids, a distinct ``service.name`` resource per span, CLIENT/SERVER/INTERNAL kinds,
ERROR status, and the frozen §0A attribute set — all without a live SigNoz.
"""

from __future__ import annotations

from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind as OTelSpanKind
from opentelemetry.trace.status import StatusCode

from homeapm.config import Config, Mode
from homeapm.otlp_emit import OTLPEmitter, TraceIdRegistry
from homeapm.trace_reconstruct import (
    Result,
    SpanKind,
    SpanSpec,
    StepType,
    reconstruct,
)

_ROOT_ID = "a" * 16
_C1 = "b" * 16
_C2 = "c" * 16


def _config() -> Config:
    return Config(
        ha_url="http://localhost:8123",
        ha_token="",
        otlp_endpoint="http://localhost:4318",
        mode=Mode.SEEDED,
    )


def _emitter_with_memory() -> tuple[OTLPEmitter, InMemorySpanExporter]:
    emitter = OTLPEmitter(_config())
    memory = InMemorySpanExporter()
    emitter._exporter = memory
    return emitter, memory


def _spec(
    span_id: str,
    parent: str | None,
    name: str,
    kind: SpanKind,
    service: str,
    *,
    run_id: str = "run0001",
    peer: str | None = None,
    error: bool = False,
) -> SpanSpec:
    return SpanSpec(
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        kind=kind,
        service_name=service,
        start_unix_nano=1_000_000_000,
        end_unix_nano=1_000_500_000,
        automation_name="Unit Automation",
        automation_id="unit",
        automation_room="kitchen",
        node_path=name,
        step_type=StepType.SERVICE_CALL if peer else StepType.SEQUENCE,
        context_id="ctx-1",
        run_id=run_id,
        result=Result.ERROR if error else Result.OK,
        peer_service=peer,
        status_error=error,
        status_message="boom" if error else None,
    )


def test_emit_shares_one_trace_id_and_preserves_tree() -> None:
    emitter, memory = _emitter_with_memory()
    spans = [
        _spec(_ROOT_ID, None, "root", SpanKind.SERVER, "ha.automation"),
        _spec(_C1, _ROOT_ID, "light.turn_on", SpanKind.CLIENT, "ha.light", peer="light"),
    ]
    result = emitter.emit(spans)

    finished = memory.get_finished_spans()
    assert len(finished) == 2
    trace_ids = {s.context.trace_id for s in finished}
    assert len(trace_ids) == 1  # one trace id for the whole run
    assert result.trace_id_hex == format(next(iter(trace_ids)), "032x")

    by_name = {s.name: s for s in finished}
    assert by_name["root"].parent is None
    child = by_name["light.turn_on"]
    assert child.parent is not None
    assert child.parent.span_id == by_name["root"].context.span_id
    # ids come straight from the SpanSpec (deterministic, matches golden)
    assert format(child.context.span_id, "016x") == _C1


def test_emit_attaches_service_name_resource_per_span() -> None:
    emitter, memory = _emitter_with_memory()
    spans = [
        _spec(_ROOT_ID, None, "root", SpanKind.SERVER, "ha.automation"),
        _spec(_C1, _ROOT_ID, "light", SpanKind.CLIENT, "ha.light", peer="light"),
        _spec(_C2, _ROOT_ID, "cover", SpanKind.CLIENT, "ha.cover", peer="cover"),
    ]
    emitter.emit(spans)
    finished = {s.name: s for s in memory.get_finished_spans()}
    assert finished["root"].resource.attributes["service.name"] == "ha.automation"
    assert finished["light"].resource.attributes["service.name"] == "ha.light"
    assert finished["cover"].resource.attributes["service.name"] == "ha.cover"


def test_emit_maps_span_kinds() -> None:
    emitter, memory = _emitter_with_memory()
    spans = [
        _spec(_ROOT_ID, None, "root", SpanKind.SERVER, "ha.automation"),
        _spec(_C1, _ROOT_ID, "structural", SpanKind.INTERNAL, "ha.automation"),
        _spec(_C2, _ROOT_ID, "call", SpanKind.CLIENT, "ha.light", peer="light"),
    ]
    emitter.emit(spans)
    kinds = {s.name: s.kind for s in memory.get_finished_spans()}
    assert kinds["root"] is OTelSpanKind.SERVER
    assert kinds["structural"] is OTelSpanKind.INTERNAL
    assert kinds["call"] is OTelSpanKind.CLIENT


def test_emit_sets_error_status_and_frozen_attributes() -> None:
    emitter, memory = _emitter_with_memory()
    spans = [
        _spec(_ROOT_ID, None, "root", SpanKind.SERVER, "ha.automation"),
        _spec(
            _C1,
            _ROOT_ID,
            "boom",
            SpanKind.CLIENT,
            "ha.persistent_notification",
            peer="persistent_notification",
            error=True,
        ),
    ]
    emitter.emit(spans)
    by_name = {s.name: s for s in memory.get_finished_spans()}

    err = by_name["boom"]
    assert err.status.status_code is StatusCode.ERROR
    assert err.attributes is not None
    assert err.attributes["peer.service"] == "persistent_notification"
    for key in (
        "automation.name",
        "automation.id",
        "automation.room",
        "ha.node_path",
        "ha.step_type",
        "ha.context_id",
        "ha.run_id",
        "ha.result",
        "ha.changed_variables",
    ):
        assert key in err.attributes, f"missing frozen §0A attribute {key}"

    assert by_name["root"].status.status_code is not StatusCode.ERROR


def test_registry_mints_one_trace_id_per_run() -> None:
    registry = TraceIdRegistry()
    assert not registry.known("run-A")
    tid = registry.trace_id_for("run-A")
    assert registry.known("run-A")
    assert registry.trace_id_for("run-A") == tid
    assert registry.trace_id_for("run-B") != tid


def test_emit_rejects_empty_span_list() -> None:
    emitter, _ = _emitter_with_memory()
    try:
        emitter.emit([])
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("emit([]) should raise ValueError")


def test_emit_end_to_end_from_reconstruct() -> None:
    """reconstruct → emit round-trip: the real spike-shaped choose payload."""
    payload = {
        "run_id": "runXYZ",
        "timestamp": {
            "start": "2026-07-22T15:00:00.000000+00:00",
            "finish": "2026-07-22T15:00:02.000000+00:00",
        },
        "trace": {
            "trigger": [{"path": "trigger", "timestamp": "2026-07-22T15:00:00.100000+00:00"}],
            "action/0": [{"path": "action/0", "timestamp": "2026-07-22T15:00:00.200000+00:00"}],
        },
        "config": {
            "id": "x",
            "alias": "X",
            "actions": [{"action": "light.turn_on"}],
        },
        "context": {"id": "ctx"},
    }
    emitter, memory = _emitter_with_memory()
    result = emitter.emit(reconstruct(payload))
    finished = memory.get_finished_spans()
    assert result.span_count == len(finished) == 3  # root + trigger + service call
    assert {s.context.trace_id for s in finished} == {int(result.trace_id_hex, 16)}
    assert "ha.light" in result.services


def test_export_failure_raises() -> None:
    from homeapm.otlp_emit import EmitError

    class _FailExporter:
        def export(self, spans: object) -> SpanExportResult:
            return SpanExportResult.FAILURE

        def shutdown(self) -> None:
            pass

    emitter = OTLPEmitter(_config())
    emitter._exporter = _FailExporter()  # type: ignore[assignment]
    spans = [_spec(_ROOT_ID, None, "root", SpanKind.SERVER, "ha.automation")]
    try:
        emitter.emit(spans)
    except EmitError:
        pass
    else:  # pragma: no cover
        raise AssertionError("failed export should raise EmitError")
