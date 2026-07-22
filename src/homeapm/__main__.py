"""Asyncio entrypoint: wire config -> WS client -> reconstruct -> OTLP emit.

Run with ``python -m homeapm`` or the ``homeapm`` console script. This module
only orchestrates; every unit of behavior lives in a testable sibling module.

Signals wired:
    - traces:  ``automation_triggered`` -> ``trace/get`` ->
      :func:`~homeapm.trace_reconstruct.reconstruct` -> :class:`OTLPEmitter`;
    - metrics: ``state_changed`` -> :class:`MetricsBridge` gauges;
    - selfobs: ``homeapm.ws.connected`` + ``homeapm.traces.converted.total``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from homeapm.config import Config, ConfigError, load_config
from homeapm.metrics import MetricsBridge
from homeapm.otlp_emit import OTLPEmitter
from homeapm.otlp_metrics import build_meter_provider
from homeapm.selfobs import SelfObservability
from homeapm.trace_reconstruct import reconstruct
from homeapm.ws_client import HAWebSocketClient, WSAuthError

log = logging.getLogger("homeapm")


def _configure_logging() -> None:
    """Line-buffered stdout logging (redirected to sidecar.log in the demo)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def _run(config: Config) -> None:
    """Build and run the full pipeline until cancelled."""
    meter_provider = build_meter_provider(config)
    meter = meter_provider.get_meter("homeapm")

    selfobs = SelfObservability(config, meter)
    emitter = OTLPEmitter(config)
    metrics = MetricsBridge(config, meter)

    async def on_trace(payload: dict[str, Any]) -> None:
        spans = reconstruct(payload)
        result = emitter.emit(spans)
        selfobs.incr_traces_converted()
        log.info(
            "converted run %s -> trace %s (%d spans)",
            result.run_id,
            result.trace_id_hex,
            result.span_count,
        )

    def on_state(data: dict[str, Any]) -> None:
        metrics.handle_event(data)

    client = HAWebSocketClient(
        config,
        on_trace=on_trace,
        on_state=on_state,
        on_connection_change=selfobs.set_ws_connected,
    )
    log.info("Home APM sidecar starting: HA=%s OTLP=%s", config.ha_url, config.otlp_endpoint)
    try:
        await client.run_forever()
    finally:
        await client.close()
        emitter.shutdown()
        meter_provider.shutdown()


def main(argv: list[str] | None = None) -> int:
    """Console-script entrypoint. Returns a process exit code.

    Returns:
        ``0`` on clean shutdown, ``2`` on configuration error, ``3`` on a fatal
        authentication failure (bad HA token).
    """
    _ = argv
    _configure_logging()
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"homeapm: configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 0
    except WSAuthError as exc:
        print(f"homeapm: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
