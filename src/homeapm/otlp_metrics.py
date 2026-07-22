"""OTLP/HTTP metrics plumbing shared by :mod:`homeapm.metrics` and :mod:`selfobs`.

A single :class:`~opentelemetry.sdk.metrics.MeterProvider` backs every gauge and
counter the sidecar emits. Its resource carries ``deployment.environment`` (spec
item 4 — the SigNoz env filter hides signals without it) so metrics and traces
line up under one environment. A :class:`PeriodicExportingMetricReader` re-exports
the latest observable-gauge values on a fixed interval, so a panel like
``homeapm.ws.connected`` draws a continuous line even when nothing changes.
"""

from __future__ import annotations

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from homeapm.config import Config

# The sidecar's own resource identity for every metric it emits.
_METRICS_SERVICE_NAME = "homeapm.sidecar"

# How often observable gauges are re-exported (ms). Short enough that a freshly
# opened dashboard fills in within one scrape, long enough to stay cheap.
_EXPORT_INTERVAL_MS = 10_000


def build_meter_provider(config: Config) -> MeterProvider:
    """Construct the shared :class:`MeterProvider` exporting to ``{OTLP}/v1/metrics``.

    Args:
        config: Resolved sidecar configuration (OTLP endpoint, namespace, env).

    Returns:
        A configured :class:`MeterProvider`; call :meth:`MeterProvider.shutdown`
        on exit to flush the final export.
    """
    resource = Resource.create(
        {
            "service.name": _METRICS_SERVICE_NAME,
            "service.namespace": config.service_namespace,
            "deployment.environment": config.environment,
        }
    )
    exporter = OTLPMetricExporter(endpoint=config.otlp_metrics_url)
    reader = PeriodicExportingMetricReader(
        exporter, export_interval_millis=_EXPORT_INTERVAL_MS
    )
    return MeterProvider(resource=resource, metric_readers=[reader])
