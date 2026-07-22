"""Self-observability signals for the sidecar (spec #10, trimmed).

Deliberately just two signals - the full Bridge-Health dashboard row was dropped
as diminishing returns (spec section 1 DROPPED). Names follow the FROZEN METRIC
CONTRACT verbatim:

- ``homeapm.ws.connected``          gauge 0/1 - 1 while the WS client is
  authenticated + subscribed, else 0.
- ``homeapm.traces.converted.total``  counter - runs successfully emitted.

The gauge is observable (re-exported every interval so the panel is a live line);
the counter is a normal cumulative counter incremented after each emit.
"""

from __future__ import annotations

from collections.abc import Iterable

from opentelemetry.metrics import CallbackOptions, Counter, Meter, Observation

from homeapm.config import Config

_WS_CONNECTED_METRIC = "homeapm.ws.connected"
_TRACES_CONVERTED_METRIC = "homeapm.traces.converted.total"


class SelfObservability:
    """Owns the sidecar's own health signals.

    Args:
        config: Resolved sidecar configuration (reserved for future knobs).
        meter: The shared OTel meter (from
            :func:`homeapm.otlp_metrics.build_meter_provider`).
    """

    def __init__(self, config: Config, meter: Meter) -> None:
        self._config = config
        self._traces_converted = 0
        self._ws_connected = 0.0
        meter.create_observable_gauge(
            _WS_CONNECTED_METRIC,
            callbacks=[self._observe_connected],
            description="1 while the sidecar's HA WebSocket is authenticated + subscribed.",
        )
        self._counter: Counter = meter.create_counter(
            _TRACES_CONVERTED_METRIC,
            description="Automation runs reconstructed and exported as OTLP traces.",
        )

    def set_ws_connected(self, connected: bool) -> None:
        """Update the ``homeapm.ws.connected`` gauge (called by the WS client)."""
        self._ws_connected = 1.0 if connected else 0.0

    def incr_traces_converted(self, by: int = 1) -> None:
        """Increment and publish ``homeapm.traces.converted.total`` (after emit)."""
        if by <= 0:
            return
        self._traces_converted += by
        self._counter.add(by)

    def _observe_connected(self, _options: CallbackOptions) -> Iterable[Observation]:
        """Yield the current WS-connected gauge value (periodic callback)."""
        return [Observation(self._ws_connected)]

    @property
    def ws_connected(self) -> bool:
        """Whether the WS-connected gauge currently reads 1 (for tests/logging)."""
        return self._ws_connected >= 1.0

    @property
    def traces_converted_total(self) -> int:
        """Current converted-run count (for tests / logging)."""
        return self._traces_converted
