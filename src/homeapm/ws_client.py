"""Async Home Assistant WebSocket client: connect -> auth -> subscribe -> fetch.

Responsibilities (spec moat + #10 resilience):

- open the WS connection to ``{HA_URL}/api/websocket`` and complete the ``auth``
  handshake with the long-lived token, with a boot-time validation that raises a
  **human** error sentence on a bad token (#7);
- ``subscribe_events`` for ``automation_triggered`` AND ``state_changed``;
- on each ``automation_triggered``, resolve the fired run's ``run_id`` race-safely
  via ``trace/contexts`` (correlating ``event.context.id``), fetch ``trace/get``
  and poll until the run finishes, then hand the raw payload to a callback;
- feed ``state_changed`` payloads (and an initial ``get_states`` snapshot) to a
  metrics callback;
- reconnect with exponential backoff, re-authenticating and re-subscribing so the
  bridge survives HA restarts (#10).

All I/O lives here; span reconstruction stays a pure offline function in
:mod:`homeapm.trace_reconstruct`. The request/response router lets many trace
fetches run concurrently over the single socket while events keep flowing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from websockets.asyncio.client import connect as ws_connect

from homeapm.config import Config

log = logging.getLogger("homeapm.ws")

# Called with the raw ``trace/get`` result dict for one automation run.
TracePayloadHandler = Callable[[dict[str, Any]], Awaitable[None]]
# Called with a ``state_changed`` event ``data`` dict (and initial snapshot rows).
StateHandler = Callable[[dict[str, Any]], None]
# Called with one ``logbook/event_stream`` batch (its ``events`` list) for #13.
LogbookHandler = Callable[[list[dict[str, Any]]], None]
# Called with the WS connected/disconnected transition (drives the self-gauge).
ConnectionHandler = Callable[[bool], None]
# An async request/response transport: send a WS command, await its ``result`` msg.
CommandFn = Callable[..., Awaitable[dict[str, Any]]]

_HUMAN_AUTH_ERROR = (
    "Home Assistant rejected the access token (auth_invalid). Create a new "
    "long-lived access token in Home Assistant (Profile -> Security) and set "
    "HA_TOKEN, or write it to .ha-runtime/token.txt."
)


@dataclass(slots=True)
class BackoffPolicy:
    """Exponential-backoff parameters for the reconnect loop (#10)."""

    initial_seconds: float = 1.0
    max_seconds: float = 30.0
    multiplier: float = 2.0

    def delay_for(self, attempt: int) -> float:
        """Return the capped backoff delay for a zero-based retry ``attempt``."""
        return min(self.max_seconds, self.initial_seconds * (self.multiplier**attempt))


@dataclass(slots=True)
class FetchPolicy:
    """How aggressively to resolve + poll a run's ``trace/get`` after a trigger.

    A run marked with a long ``wait`` (e.g. ``morning_routine``) is still running
    when ``automation_triggered`` fires, so ``trace/get`` is polled until the run
    reports finished (or the attempt cap is hit, when the partial trace is used).
    """

    poll_seconds: float = 3.0
    max_get_attempts: int = 25
    max_contexts_attempts: int = 5


class WSAuthError(RuntimeError):
    """Raised when the Home Assistant auth handshake is rejected (bad token)."""


def _is_run_complete(result: dict[str, Any]) -> bool:
    """Whether a ``trace/get`` result represents a finished run (not still running)."""
    if result.get("script_execution"):
        return True
    return result.get("state") not in (None, "running")


async def fetch_trace_payload(
    command: CommandFn,
    item_id: str,
    context_id: str,
    policy: FetchPolicy,
) -> dict[str, Any] | None:
    """Resolve a run from its context id and fetch its (completed) ``trace/get``.

    This is the correlation core, isolated from the socket so it is unit-testable
    with a mocked ``command`` transport:

    1. poll ``trace/contexts`` (race-safe: HA keeps the context as long as the
       trace) until ``context_id`` maps to a ``run_id``;
    2. poll ``trace/get`` for that run until it reports finished, returning the
       last good payload (a still-running run yields its partial trace at the cap).

    Args:
        command: Async transport issuing one WS command and awaiting its result.
        item_id: The automation's config id (``trace/get`` ``item_id``).
        context_id: ``event.context.id`` from the ``automation_triggered`` event.
        policy: Retry/poll cadence.

    Returns:
        The raw ``trace/get`` result dict, or ``None`` if the run never resolved.
    """
    run_id = await _resolve_run_id(command, item_id, context_id, policy)
    if run_id is None:
        log.warning("no run_id for context %s (item %s); dropping trigger", context_id, item_id)
        return None

    payload: dict[str, Any] | None = None
    for attempt in range(policy.max_get_attempts):
        resp = await command(type="trace/get", domain="automation", item_id=item_id, run_id=run_id)
        result = resp.get("result")
        if isinstance(result, dict) and isinstance(result.get("trace"), dict):
            payload = result
            if _is_run_complete(result):
                return payload
        if attempt + 1 < policy.max_get_attempts:
            await asyncio.sleep(policy.poll_seconds)
    return payload


async def _resolve_run_id(
    command: CommandFn,
    item_id: str,
    context_id: str,
    policy: FetchPolicy,
) -> str | None:
    """Map ``context_id`` -> ``run_id`` via ``trace/contexts`` (with brief retry)."""
    for attempt in range(policy.max_contexts_attempts):
        resp = await command(type="trace/contexts", domain="automation", item_id=item_id)
        contexts = resp.get("result")
        if isinstance(contexts, dict):
            entry = contexts.get(context_id)
            if isinstance(entry, dict) and entry.get("run_id"):
                return str(entry["run_id"])
        if attempt + 1 < policy.max_contexts_attempts:
            await asyncio.sleep(policy.poll_seconds)
    return None


def item_id_from_states(states: list[Any], entity_id: str) -> str | None:
    """Return the automation config ``id`` for ``entity_id`` from a get_states list."""
    for state in states:
        if not isinstance(state, dict) or state.get("entity_id") != entity_id:
            continue
        attrs = state.get("attributes")
        if isinstance(attrs, dict) and attrs.get("id"):
            return str(attrs["id"])
    return None


class HAWebSocketClient:
    """Long-lived, self-reconnecting HA WebSocket consumer.

    Args:
        config: Resolved sidecar configuration.
        on_trace: Coroutine invoked with each raw ``trace/get`` result.
        on_state: Optional callback for each ``state_changed`` data dict and for
            the initial ``get_states`` snapshot rows.
        on_connection_change: Optional callback fired on connect/disconnect.
        backoff: Reconnect backoff policy.
        fetch_policy: Trace resolve/poll cadence.
        command_timeout: Per-command response timeout (seconds).
    """

    def __init__(
        self,
        config: Config,
        on_trace: TracePayloadHandler,
        on_state: StateHandler | None = None,
        on_connection_change: ConnectionHandler | None = None,
        on_logbook: LogbookHandler | None = None,
        backoff: BackoffPolicy | None = None,
        fetch_policy: FetchPolicy | None = None,
        command_timeout: float = 30.0,
    ) -> None:
        self._config = config
        self._on_trace = on_trace
        self._on_state = on_state
        self._on_connection_change = on_connection_change
        self._on_logbook = on_logbook
        self._backoff = backoff or BackoffPolicy()
        self._fetch_policy = fetch_policy or FetchPolicy()
        self._command_timeout = command_timeout

        self._msg_id = 0
        self._connected = False
        self._closing = False
        self._attempt = 0
        self._ha_version: str | None = None
        self._ws: Any = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._fetch_tasks: set[asyncio.Task[None]] = set()
        self._item_ids: dict[str, str] = {}
        self._logbook_sub_id: int | None = None

    @property
    def connected(self) -> bool:
        """Whether the socket is currently authenticated and subscribed."""
        return self._connected

    def _next_id(self) -> int:
        """Return the next monotonic WebSocket message id."""
        self._msg_id += 1
        return self._msg_id

    # -- lifecycle ------------------------------------------------------------

    async def run_forever(self) -> None:
        """Validate the token, then connect/consume, reconnecting with backoff."""
        await self._boot_validate()
        while not self._closing:
            try:
                await self._connect_once()
                reason = "connection closed"
            except asyncio.CancelledError:
                raise
            except WSAuthError:
                raise
            except Exception as exc:
                reason = repr(exc)
            self._set_connected(False)
            if self._closing:
                break
            delay = self._backoff.delay_for(self._attempt)
            self._attempt += 1
            log.warning("HA WS session ended (%s); reconnecting in %.1fs", reason, delay)
            await asyncio.sleep(delay)

    async def _boot_validate(self) -> None:
        """One-shot auth check (#7). Bad token is fatal; unreachable HA is not."""
        try:
            async with ws_connect(self._config.ws_url, max_size=None, open_timeout=10) as ws:
                await self._authenticate(ws)
        except WSAuthError:
            raise
        except Exception as exc:
            log.warning(
                "Boot check: Home Assistant not reachable at %s yet (%s); will keep retrying.",
                self._config.ws_url,
                exc,
            )
            return
        log.info("Boot check OK: authenticated to Home Assistant %s.", self._ha_version)

    async def _connect_once(self) -> None:
        """Run one authenticated session: connect, auth, subscribe, consume."""
        async with ws_connect(
            self._config.ws_url, max_size=None, ping_interval=20, ping_timeout=20
        ) as ws:
            await self._authenticate(ws)
            self._ws = ws
            self._pending = {}
            self._logbook_sub_id = None
            consumer = asyncio.create_task(self._consume(ws))
            try:
                await self._subscribe(ws, "automation_triggered")
                await self._subscribe(ws, "state_changed")
                await self._subscribe_logbook(ws)
                self._set_connected(True)
                log.info("connected: subscribed to automation_triggered + state_changed")
                await self._prime_states()
                await consumer
            finally:
                consumer.cancel()
                with suppress(asyncio.CancelledError):
                    await consumer
                await self._drain_fetch_tasks()
                self._ws = None
                self._fail_pending()

    async def _authenticate(self, ws: Any) -> None:
        """Complete the ``auth_required`` -> ``auth`` -> ``auth_ok`` handshake."""
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise WSAuthError(f"unexpected greeting from HA: {hello.get('type')!r}")
        await ws.send(json.dumps({"type": "auth", "access_token": self._config.ha_token}))
        resp = json.loads(await ws.recv())
        if resp.get("type") == "auth_invalid":
            detail = resp.get("message")
            raise WSAuthError(f"{_HUMAN_AUTH_ERROR} (HA said: {detail})")
        if resp.get("type") != "auth_ok":
            raise WSAuthError(f"unexpected auth response from HA: {resp.get('type')!r}")
        self._ha_version = resp.get("ha_version")

    async def _subscribe(self, ws: Any, event_type: str) -> None:
        """Send ``subscribe_events`` for ``event_type`` and confirm success."""
        resp = await self._command(type="subscribe_events", event_type=event_type)
        if not resp.get("success"):
            raise RuntimeError(f"subscribe_events({event_type}) failed: {resp}")

    async def _subscribe_logbook(self, ws: Any) -> None:
        """Subscribe to ``logbook/event_stream`` (#13), routing batches by sub id.

        Unlike ``subscribe_events``, this streams under a single id: HA returns
        one ``result`` (success) then pushes ``event`` frames carrying an
        ``events`` list. We capture the id so :meth:`_consume` can route those
        frames to :attr:`_on_logbook`. A failed subscribe is non-fatal — the
        trace/metric pipeline keeps running without correlated logs.
        """
        if self._on_logbook is None:
            return
        mid = self._next_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[mid] = fut
        start_time = datetime.now(UTC).isoformat()
        try:
            await ws.send(
                json.dumps({"id": mid, "type": "logbook/event_stream", "start_time": start_time})
            )
            resp = await asyncio.wait_for(fut, timeout=self._command_timeout)
        except (TimeoutError, ConnectionError) as exc:
            log.warning("logbook/event_stream subscribe failed (%s); logs correlation off", exc)
            return
        finally:
            self._pending.pop(mid, None)
        if resp.get("success"):
            self._logbook_sub_id = mid
            log.info("subscribed to logbook/event_stream (#13 logs correlation)")
        else:
            log.warning("logbook/event_stream not available (%s); logs correlation off", resp)

    async def _prime_states(self) -> None:
        """Snapshot all states once: build entity->item_id map + seed metrics."""
        try:
            resp = await self._command(type="get_states")
        except (TimeoutError, ConnectionError) as exc:
            log.warning("get_states failed (%s); item-id map / metric seed skipped", exc)
            return
        states = resp.get("result")
        if not isinstance(states, list):
            return
        for state in states:
            if not isinstance(state, dict):
                continue
            entity_id = state.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            attrs = state.get("attributes")
            if entity_id.startswith("automation.") and isinstance(attrs, dict) and attrs.get("id"):
                self._item_ids[entity_id] = str(attrs["id"])
            if self._on_state is not None:
                self._on_state({"entity_id": entity_id, "new_state": state})
        log.info("primed %d entities (%d automations mapped)", len(states), len(self._item_ids))

    # -- request/response router ---------------------------------------------

    async def _command(self, **payload: Any) -> dict[str, Any]:
        """Send one WS command and await its matching ``result`` message."""
        ws = self._ws
        if ws is None:
            raise ConnectionError("WS command issued with no active connection")
        mid = self._next_id()
        payload["id"] = mid
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[mid] = fut
        try:
            await ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout=self._command_timeout)
        finally:
            self._pending.pop(mid, None)

    async def _consume(self, ws: Any) -> None:
        """Read frames, route ``result`` to futures and dispatch events."""
        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "result":
                fut = self._pending.get(msg.get("id"))
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            elif mtype == "event":
                if self._logbook_sub_id is not None and msg.get("id") == self._logbook_sub_id:
                    self._dispatch_logbook(msg.get("event"))
                else:
                    self._dispatch_event(msg.get("event"))

    def _dispatch_event(self, event: Any) -> None:
        """Route one HA event to the trace-fetch path or the metrics callback."""
        if not isinstance(event, dict):
            return
        event_type = event.get("event_type")
        if event_type == "automation_triggered":
            self._spawn_trace_fetch(event)
        elif event_type == "state_changed" and self._on_state is not None:
            data = event.get("data")
            if isinstance(data, dict):
                self._on_state(data)

    def _dispatch_logbook(self, event: Any) -> None:
        """Hand one ``logbook/event_stream`` batch's ``events`` list to #13."""
        if self._on_logbook is None or not isinstance(event, dict):
            return
        events = event.get("events")
        if isinstance(events, list):
            self._on_logbook(events)

    def _spawn_trace_fetch(self, event: dict[str, Any]) -> None:
        """Launch a background task that fetches + emits one run's trace."""
        task = asyncio.create_task(self._run_trace_fetch(event))
        self._fetch_tasks.add(task)
        task.add_done_callback(self._fetch_tasks.discard)

    async def _run_trace_fetch(self, event: dict[str, Any]) -> None:
        """Correlate + fetch one run's trace, then hand it to ``on_trace``."""
        data = event.get("data")
        context = event.get("context")
        if not isinstance(data, dict) or not isinstance(context, dict):
            return
        entity_id = data.get("entity_id")
        context_id = context.get("id")
        if not isinstance(entity_id, str) or not isinstance(context_id, str):
            return
        item_id = self._item_ids.get(entity_id) or entity_id.split(".", 1)[-1]
        name = data.get("name") or entity_id
        try:
            payload = await fetch_trace_payload(
                self._command, item_id, context_id, self._fetch_policy
            )
        except (TimeoutError, ConnectionError) as exc:
            log.warning("trace fetch for %s aborted (%s)", name, exc)
            return
        except asyncio.CancelledError:
            raise
        if payload is None:
            return
        try:
            await self._on_trace(payload)
            log.info("emitted trace for %s (run %s)", name, payload.get("run_id"))
        except Exception:
            log.exception("failed to reconstruct/emit trace for %s", name)

    # -- helpers --------------------------------------------------------------

    def _set_connected(self, connected: bool) -> None:
        """Update connected state, reset backoff on success, notify listeners."""
        if connected:
            self._attempt = 0
        if connected == self._connected:
            return
        self._connected = connected
        if self._on_connection_change is not None:
            self._on_connection_change(connected)

    def _fail_pending(self) -> None:
        """Cancel any in-flight command futures on session teardown."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("WS session ended"))
        self._pending = {}

    async def _drain_fetch_tasks(self) -> None:
        """Cancel and await outstanding trace-fetch tasks."""
        tasks = list(self._fetch_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._fetch_tasks.clear()

    async def close(self) -> None:
        """Best-effort graceful shutdown of the active connection."""
        self._closing = True
        self._set_connected(False)
        ws = self._ws
        if ws is not None:
            with suppress(Exception):
                await ws.close()
        await asyncio.sleep(0)
