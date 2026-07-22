"""Async Home Assistant WebSocket client: connect → auth → subscribe → fetch.

Responsibilities (spec moat + #10 resilience):
- open the WS connection to ``{HA_URL}/api/websocket`` and complete the
  ``auth`` handshake with the long-lived token;
- ``subscribe_events`` for ``automation_triggered`` (and, for #13, observe
  ``state_changed`` if metrics share this socket);
- on each trigger, issue ``trace/get`` for the fired automation's latest run
  and hand the raw payload to a callback;
- reconnect with exponential backoff and re-subscribe on HA restart.

This module performs all the I/O; it never reconstructs spans itself — it
passes raw payload dicts to the pipeline so :mod:`homeapm.trace_reconstruct`
stays pure and offline-testable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeapm.config import Config

# Called with the raw ``trace/get`` result dict for one automation run.
TracePayloadHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class BackoffPolicy:
    """Exponential-backoff parameters for the reconnect loop (#10)."""

    initial_seconds: float = 1.0
    max_seconds: float = 30.0
    multiplier: float = 2.0

    def delay_for(self, attempt: int) -> float:
        """Return the capped backoff delay for a zero-based retry ``attempt``."""
        return min(self.max_seconds, self.initial_seconds * (self.multiplier**attempt))


class WSAuthError(RuntimeError):
    """Raised when the Home Assistant auth handshake is rejected."""


class HAWebSocketClient:
    """Long-lived, self-reconnecting HA WebSocket consumer.

    Args:
        config: Resolved sidecar configuration.
        on_trace: Coroutine invoked with each raw ``trace/get`` result.
        backoff: Reconnect backoff policy.
    """

    def __init__(
        self,
        config: Config,
        on_trace: TracePayloadHandler,
        backoff: BackoffPolicy | None = None,
    ) -> None:
        self._config = config
        self._on_trace = on_trace
        self._backoff = backoff or BackoffPolicy()
        self._msg_id = 0
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the socket is currently authenticated and subscribed.

        Mirrored to the ``ha_ws_connected`` self-gauge (:mod:`homeapm.selfobs`).
        """
        return self._connected

    def _next_id(self) -> int:
        """Return the next monotonic WebSocket message id."""
        self._msg_id += 1
        return self._msg_id

    async def run_forever(self) -> None:
        """Connect and consume until cancelled, reconnecting with backoff.

        Each iteration: :meth:`_connect_once` runs a full authenticated session;
        on disconnect it sleeps :meth:`BackoffPolicy.delay_for` and retries,
        re-subscribing on the fresh socket.

        Raises:
            NotImplementedError: Until the WS session loop is implemented.
        """
        raise NotImplementedError("run_forever(): implement connect/backoff loop.")

    async def _connect_once(self) -> None:
        """Run one authenticated session: connect, auth, subscribe, consume.

        Raises:
            WSAuthError: If authentication fails.
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("_connect_once(): implement one WS session.")

    async def _authenticate(self, ws: Any) -> None:
        """Complete the ``auth_required`` → ``auth`` → ``auth_ok`` handshake.

        Raises:
            WSAuthError: On ``auth_invalid``.
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("_authenticate(): implement HA auth handshake.")

    async def _subscribe_triggers(self, ws: Any) -> None:
        """Send ``subscribe_events`` for ``automation_triggered``.

        Raises:
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("_subscribe_triggers(): implement subscribe_events.")

    async def _fetch_trace(self, ws: Any, automation_id: str, run_id: str) -> dict[str, Any]:
        """Issue ``trace/get`` for one run and return its raw ``result`` dict.

        Args:
            ws: The live WebSocket connection.
            automation_id: The automation's entity/config id.
            run_id: The specific run to fetch.

        Returns:
            The raw ``trace/get`` result payload.

        Raises:
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("_fetch_trace(): implement trace/get request.")

    async def _consume(self, ws: Any) -> None:
        """Read messages, dispatch trigger events to ``trace/get`` + callback.

        Raises:
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("_consume(): implement message pump.")

    async def close(self) -> None:
        """Best-effort graceful shutdown of the active connection."""
        self._connected = False
        await asyncio.sleep(0)
