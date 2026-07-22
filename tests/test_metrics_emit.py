"""Offline emit test: the meter actually produces the FROZEN-contract metrics.

Uses an :class:`InMemoryMetricReader` (no OTLP, no network) to collect what the
:class:`MetricsBridge` and :class:`SelfObservability` instruments emit, then
asserts the metric names, values, and attribute sets match the frozen contract.
"""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from homeapm.config import Config, Mode
from homeapm.metrics import MetricsBridge
from homeapm.selfobs import SelfObservability

_CFG = Config(
    ha_url="http://h:8123", ha_token="t", otlp_endpoint="http://o:4318", mode=Mode.SEEDED
)


def _collect(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    """Return ``{metric_name: [data_points]}`` from one collection cycle."""
    data = reader.get_metrics_data()
    points: dict[str, list[Any]] = {}
    if data is None:
        return points
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                points.setdefault(metric.name, []).extend(metric.data.data_points)
    return points


def _state_event(entity_id: str, state: str, **attrs: object) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "new_state": {"entity_id": entity_id, "state": state, "attributes": dict(attrs)},
    }


def test_bridge_and_selfobs_emit_frozen_metrics() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("homeapm.test")

    bridge = MetricsBridge(_CFG, meter)
    selfobs = SelfObservability(_CFG, meter)

    bridge.handle_event(
        _state_event(
            "sensor.living_room_temperature",
            "25.0",
            device_class="temperature",
            unit_of_measurement="°C",
        )
    )
    bridge.handle_event(_state_event("light.hallway", "on"))
    selfobs.set_ws_connected(True)
    selfobs.incr_traces_converted()
    selfobs.incr_traces_converted(2)

    points = _collect(reader)

    assert "ha.sensor.value" in points
    sensor = points["ha.sensor.value"][0]
    assert sensor.value == 25.0
    assert sensor.attributes["device_class"] == "temperature"
    assert sensor.attributes["room"] == "living_room"

    assert "ha.entity.state" in points
    assert points["ha.entity.state"][0].value == 1.0

    assert "homeapm.ws.connected" in points
    assert points["homeapm.ws.connected"][0].value == 1.0

    assert "homeapm.traces.converted.total" in points
    assert points["homeapm.traces.converted.total"][0].value == 3

    provider.shutdown()
