"""Two self-observability gauges for the sidecar (spec #10, trimmed).

Deliberately just two gauges — the full Bridge-Health dashboard row was dropped
as diminishing returns (spec §1 DROPPED):

- ``ha_ws_connected``       1 while the WS client is authenticated, else 0.
- ``traces_converted_total``  monotonically-increasing count of runs emitted.
"""

from __future__ import annotations

from homeapm.config import Config


class SelfObservability:
    """Owns the sidecar's own health gauges.

    Args:
        config: Resolved sidecar configuration (OTLP metrics endpoint).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._traces_converted = 0
        self._ws_connected = False

    def set_ws_connected(self, connected: bool) -> None:
        """Update the ``ha_ws_connected`` gauge (called by the WS client)."""
        self._ws_connected = connected
        raise NotImplementedError("set_ws_connected(): push gauge via OTLP.")

    def incr_traces_converted(self, by: int = 1) -> None:
        """Increment and publish ``traces_converted_total`` (called after emit)."""
        self._traces_converted += by
        raise NotImplementedError("incr_traces_converted(): publish counter via OTLP.")

    @property
    def traces_converted_total(self) -> int:
        """Current converted-run count (for tests / logging)."""
        return self._traces_converted
