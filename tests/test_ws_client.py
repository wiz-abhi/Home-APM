"""Unit tests for the event->fetch correlation core (:mod:`homeapm.ws_client`).

These use a fully mocked ``command`` transport (no socket, no HA) to prove the
``automation_triggered`` -> ``trace/contexts`` -> ``trace/get`` chain resolves
the right run, polls a still-running run to completion, and handles the eviction
/ missing-context edge cases.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeapm.ws_client import (
    BackoffPolicy,
    FetchPolicy,
    fetch_trace_payload,
    item_id_from_states,
)

_CTX = "01KY549XBGBV1YYD192MH06HV6"
_RUN = "19037f226471a4a1aa303d884a098915"
# Zero-delay policy so polling loops run instantly under test.
_FAST = FetchPolicy(poll_seconds=0.0, max_get_attempts=5, max_contexts_attempts=3)


class FakeHA:
    """Scriptable ``command`` transport: canned responses per WS command type."""

    def __init__(self, contexts: dict[str, Any], get_sequence: list[dict[str, Any]]) -> None:
        self._contexts = contexts
        self._get_sequence = get_sequence
        self.calls: list[str] = []

    async def command(self, **payload: Any) -> dict[str, Any]:
        ctype = payload["type"]
        self.calls.append(ctype)
        if ctype == "trace/contexts":
            return {"success": True, "result": self._contexts}
        if ctype == "trace/get":
            idx = min(self.calls.count("trace/get") - 1, len(self._get_sequence) - 1)
            return {"success": True, "result": self._get_sequence[idx]}
        raise AssertionError(f"unexpected command {ctype}")


def _running(trace: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": _RUN, "state": "running", "script_execution": None, "trace": trace}


def _finished(trace: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": _RUN, "state": "stopped", "script_execution": "finished", "trace": trace}


def test_resolves_run_by_context_and_fetches_trace() -> None:
    ha = FakeHA(
        contexts={_CTX: {"run_id": _RUN, "domain": "automation", "item_id": "good_night"}},
        get_sequence=[_finished({"trigger/0": [{"timestamp": "2026-07-22T14:40:02+00:00"}]})],
    )
    payload = asyncio.run(fetch_trace_payload(ha.command, "good_night", _CTX, _FAST))
    assert payload is not None
    assert payload["run_id"] == _RUN
    assert "trace/contexts" in ha.calls
    assert ha.calls.count("trace/get") == 1


def test_polls_running_run_until_finished() -> None:
    trace = {"trigger/0": [{"timestamp": "2026-07-22T14:40:02+00:00"}]}
    ha = FakeHA(
        contexts={_CTX: {"run_id": _RUN, "item_id": "morning_routine"}},
        get_sequence=[_running(trace), _running(trace), _finished(trace)],
    )
    payload = asyncio.run(fetch_trace_payload(ha.command, "morning_routine", _CTX, _FAST))
    assert payload is not None
    assert payload["script_execution"] == "finished"
    # Stopped only on the third get; earlier running responses were re-polled.
    assert ha.calls.count("trace/get") == 3


def test_partial_trace_returned_when_cap_hit() -> None:
    trace = {"trigger/0": [{"timestamp": "2026-07-22T14:40:02+00:00"}]}
    ha = FakeHA(
        contexts={_CTX: {"run_id": _RUN, "item_id": "morning_routine"}},
        get_sequence=[_running(trace)],  # never finishes
    )
    payload = asyncio.run(fetch_trace_payload(ha.command, "morning_routine", _CTX, _FAST))
    assert payload is not None  # last partial payload is still returned
    assert payload["state"] == "running"
    assert ha.calls.count("trace/get") == _FAST.max_get_attempts


def test_unknown_context_yields_none_without_fetching() -> None:
    ha = FakeHA(contexts={}, get_sequence=[_finished({})])
    payload = asyncio.run(fetch_trace_payload(ha.command, "good_night", _CTX, _FAST))
    assert payload is None
    assert "trace/get" not in ha.calls
    # Retried trace/contexts up to the cap before giving up.
    assert ha.calls.count("trace/contexts") == _FAST.max_contexts_attempts


def test_context_appears_on_retry() -> None:
    """A context that lands on the 2nd poll (event beat the trace store) resolves."""

    class LateHA(FakeHA):
        async def command(self, **payload: Any) -> dict[str, Any]:
            if payload["type"] == "trace/contexts" and self.calls.count("trace/contexts") < 1:
                self.calls.append("trace/contexts")
                return {"success": True, "result": {}}  # not there yet
            return await super().command(**payload)

    ha = LateHA(
        contexts={_CTX: {"run_id": _RUN, "item_id": "good_night"}},
        get_sequence=[_finished({"trigger/0": [{}]})],
    )
    payload = asyncio.run(fetch_trace_payload(ha.command, "good_night", _CTX, _FAST))
    assert payload is not None
    assert ha.calls.count("trace/contexts") == 2


def test_item_id_from_states_reads_id_attribute() -> None:
    states = [
        {"entity_id": "light.hallway", "state": "on", "attributes": {}},
        {
            "entity_id": "automation.good_night",
            "state": "on",
            "attributes": {"id": "good_night", "friendly_name": "Good Night"},
        },
    ]
    assert item_id_from_states(states, "automation.good_night") == "good_night"
    assert item_id_from_states(states, "automation.missing") is None


def test_backoff_is_exponential_and_capped() -> None:
    policy = BackoffPolicy(initial_seconds=1.0, max_seconds=30.0, multiplier=2.0)
    assert policy.delay_for(0) == 1.0
    assert policy.delay_for(1) == 2.0
    assert policy.delay_for(2) == 4.0
    assert policy.delay_for(10) == 30.0  # capped


def test_command_router_dispatches_events_and_results() -> None:
    """End-to-end-ish: drive the client's consume loop with a fake socket.

    Proves a ``state_changed`` event reaches ``on_state`` and an
    ``automation_triggered`` event spawns a trace fetch that resolves + emits.
    """
    from homeapm.config import Config, Mode
    from homeapm.ws_client import HAWebSocketClient

    cfg = Config(
        ha_url="http://h:8123", ha_token="t", otlp_endpoint="http://o:4318", mode=Mode.SEEDED
    )

    states: list[dict[str, Any]] = []
    emitted: list[dict[str, Any]] = []

    async def on_trace(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    client = HAWebSocketClient(
        cfg,
        on_trace=on_trace,
        on_state=states.append,
        fetch_policy=_FAST,
    )
    client._item_ids["automation.good_night"] = "good_night"

    async def fake_command(**payload: Any) -> dict[str, Any]:
        ctype = payload["type"]
        if ctype == "trace/contexts":
            return {"success": True, "result": {_CTX: {"run_id": _RUN, "item_id": "good_night"}}}
        if ctype == "trace/get":
            return {"success": True, "result": _finished({"trigger/0": [{}]})}
        raise AssertionError(ctype)

    async def drive() -> None:
        client._command = fake_command  # type: ignore[method-assign]
        client._dispatch_event(
            {"event_type": "state_changed", "data": {"entity_id": "light.hallway"}}
        )
        client._dispatch_event(
            {
                "event_type": "automation_triggered",
                "data": {"entity_id": "automation.good_night", "name": "Good Night"},
                "context": {"id": _CTX},
            }
        )
        # let the spawned fetch task run to completion
        await asyncio.gather(*client._fetch_tasks)

    asyncio.run(drive())
    assert states == [{"entity_id": "light.hallway"}]
    assert len(emitted) == 1
    assert emitted[0]["run_id"] == _RUN
