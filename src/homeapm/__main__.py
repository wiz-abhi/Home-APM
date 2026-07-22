"""Asyncio entrypoint: wire config → WS client → reconstruct → OTLP emit.

Run with ``python -m homeapm`` or the ``homeapm`` console script. This module
only orchestrates; every unit of behavior lives in a testable sibling module.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from homeapm.config import Config, ConfigError, load_config
from homeapm.metrics import MetricsBridge
from homeapm.otlp_emit import OTLPEmitter
from homeapm.selfobs import SelfObservability
from homeapm.trace_reconstruct import reconstruct
from homeapm.ws_client import HAWebSocketClient


async def _run(config: Config) -> None:
    """Build and run the full pipeline until cancelled.

    Wiring:
        - :class:`SelfObservability` and :class:`OTLPEmitter` (shares the
          run_id→trace_id registry);
        - :class:`MetricsBridge` for ``state_changed`` gauges;
        - :class:`HAWebSocketClient` whose ``on_trace`` callback runs
          :func:`reconstruct` then :meth:`OTLPEmitter.emit`.

    Raises:
        NotImplementedError: Until the modules it wires are implemented.
    """
    selfobs = SelfObservability(config)
    emitter = OTLPEmitter(config)
    _metrics = MetricsBridge(config, entity_rooms={})

    async def on_trace(payload: dict[str, Any]) -> None:
        spans = reconstruct(payload)
        result = emitter.emit(spans)
        selfobs.incr_traces_converted()
        _ = result

    client = HAWebSocketClient(config, on_trace=on_trace)
    try:
        await client.run_forever()
    finally:
        await client.close()
        emitter.shutdown()


def main(argv: list[str] | None = None) -> int:
    """Console-script entrypoint. Returns a process exit code.

    Args:
        argv: Unused for now (reserved for future flags); config is env-driven.

    Returns:
        ``0`` on clean shutdown, ``2`` on configuration error.
    """
    _ = argv
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"homeapm: configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
