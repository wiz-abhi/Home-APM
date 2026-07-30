"""Focused unit tests for the tricky reconstruction paths.

These use tiny, hand-built payloads (clearly synthetic test inputs, not fixtures)
to pin the behaviors the golden-snapshot on the real ``spike`` fixture cannot
exercise on its own: service-call routing, choose/if nesting, template errors,
repeat iteration indexing, and — the interaction bomb — parallel branches that
must not truncate one another (spec §12b / §0B).
"""

from __future__ import annotations

from typing import Any

from homeapm.trace_reconstruct import Result, SpanKind, StepType, reconstruct


def _payload(actions: list[dict[str, Any]], trace: dict[str, Any], **cfg: Any) -> dict[str, Any]:
    """Wrap a config + trace dict in a minimal ``trace/get`` result object."""
    config = {
        "id": cfg.get("id", "unit"),
        "alias": cfg.get("alias", "Unit Automation"),
        "triggers": [],
        "conditions": [],
        "actions": actions,
        "mode": "single",
    }
    if "room" in cfg:
        config["variables"] = {"room": cfg["room"]}
    return {
        "run_id": cfg.get("run_id", "run0001"),
        "timestamp": {
            "start": "2026-07-22T15:00:00.000000+00:00",
            "finish": "2026-07-22T15:00:10.000000+00:00",
        },
        "domain": "automation",
        "item_id": cfg.get("id", "unit"),
        "trace": trace,
        "config": config,
        "context": {"id": "ctx-run-0001", "parent_id": None, "user_id": None},
    }


def _el(path: str, secs: float, **extra: Any) -> dict[str, Any]:
    micro = f"{secs:09.6f}".split(".")[1]
    whole = int(secs)
    return {"path": path, "timestamp": f"2026-07-22T15:00:{whole:02d}.{micro}+00:00", **extra}


def test_unwrap_accepts_both_envelope_and_result() -> None:
    """reconstruct() accepts the full WS envelope or the bare ``result`` object."""
    result = _payload([{"action": "light.turn_on"}], {"trigger": [_el("trigger", 0.1)]})
    from_result = reconstruct(result)
    envelope = {"id": 4, "type": "result", "success": True, "result": result}
    from_envelope = reconstruct(envelope)
    assert [s.node_path for s in from_result] == [s.node_path for s in from_envelope]


def test_root_is_single_server_automation_span() -> None:
    spans = reconstruct(_payload([{"action": "light.turn_on"}], {"trigger": [_el("trigger", 0.1)]}))
    roots = [s for s in spans if s.parent_span_id is None]
    assert len(roots) == 1
    root = roots[0]
    assert root.kind is SpanKind.SERVER
    assert root.service_name == "ha.automation"
    assert root.automation_name == "Unit Automation"


def test_room_is_read_from_config_variables() -> None:
    spans = reconstruct(
        _payload([{"action": "light.turn_on"}], {"trigger": [_el("trigger", 0.1)]}, room="kitchen")
    )
    assert all(s.automation_room == "kitchen" for s in spans)


def test_service_call_routes_to_domain_service_and_client_kind() -> None:
    """A service call becomes ha.<domain>, CLIENT, peer.service=<domain> (§0A)."""
    spans = reconstruct(
        _payload(
            [{"action": "climate.set_temperature"}],
            {"trigger": [_el("trigger", 0.1)], "action/0": [_el("action/0", 0.2)]},
        )
    )
    call = next(s for s in spans if s.node_path == "action/0")
    assert call.step_type is StepType.SERVICE_CALL
    assert call.service_name == "ha.climate"
    assert call.kind is SpanKind.CLIENT
    assert call.peer_service == "climate"
    assert call.name == "climate.set_temperature"


def test_choose_branch_condition_and_service_nest_correctly() -> None:
    actions = [
        {
            "choose": [
                {
                    "conditions": [{"condition": "template", "value_template": "{{ true }}"}],
                    "sequence": [{"action": "light.turn_on"}],
                }
            ]
        }
    ]
    trace = {
        "trigger": [_el("trigger", 0.1)],
        "action/0": [_el("action/0", 0.2, result={"choice": 0})],
        "action/0/choose/0": [_el("action/0/choose/0", 0.3, result={"result": True})],
        "action/0/choose/0/conditions/0": [_el("action/0/choose/0/conditions/0", 0.4)],
        "action/0/choose/0/sequence/0": [_el("action/0/choose/0/sequence/0", 0.5)],
    }
    spans = reconstruct(_payload(actions, trace))
    by_path = {s.node_path: s for s in spans}

    assert by_path["action/0"].step_type is StepType.CHOOSE
    assert by_path["action/0/choose/0/conditions/0"].step_type is StepType.CONDITION
    call = by_path["action/0/choose/0/sequence/0"]
    assert call.step_type is StepType.SERVICE_CALL and call.service_name == "ha.light"

    # nesting: condition + service hang off the branch; the branch hangs off choose
    branch = by_path["action/0/choose/0"]
    assert by_path["action/0/choose/0/conditions/0"].parent_span_id == branch.span_id
    assert call.parent_span_id == branch.span_id
    assert branch.parent_span_id == by_path["action/0"].span_id
    assert by_path["action/0"].parent_span_id == by_path["__root__"].span_id


def test_if_then_maps_to_choose_and_condition() -> None:
    actions = [{"if": [{"condition": "state"}], "then": [{"action": "cover.close_cover"}]}]
    trace = {
        "trigger": [_el("trigger", 0.1)],
        "action/0": [_el("action/0", 0.2)],
        "action/0/if/0": [_el("action/0/if/0", 0.3)],
        "action/0/then/0": [_el("action/0/then/0", 0.4)],
    }
    by_path = {s.node_path: s for s in reconstruct(_payload(actions, trace))}
    assert by_path["action/0"].step_type is StepType.CHOOSE
    assert by_path["action/0/if/0"].step_type is StepType.CONDITION
    assert by_path["action/0/then/0"].service_name == "ha.cover"


def test_template_error_sets_status_and_template_errors() -> None:
    actions = [{"action": "persistent_notification.create", "data": {"message": "{{ 1/0 }}"}}]
    err = "TemplateError: ZeroDivisionError: division by zero"
    trace = {
        "trigger": [_el("trigger", 0.1)],
        "action/0": [_el("action/0", 0.2, error=err)],
    }
    call = next(s for s in reconstruct(_payload(actions, trace)) if s.node_path == "action/0")
    assert call.status_error is True
    assert call.result is Result.ERROR
    assert call.template_errors == err
    assert call.status_message == err


def test_repeat_iterations_are_indexed() -> None:
    actions = [{"repeat": {"count": 3, "sequence": [{"action": "light.turn_off"}]}}]
    trace = {
        "trigger": [_el("trigger", 0.1)],
        "action/0": [_el("action/0", 0.2)],
        "action/0/repeat/sequence/0": [
            _el("action/0/repeat/sequence/0", 1.0),
            _el("action/0/repeat/sequence/0", 2.0),
            _el("action/0/repeat/sequence/0", 3.0),
        ],
    }
    spans = reconstruct(_payload(actions, trace))
    iters = sorted(
        s.extra_attributes["ha.iteration"]
        for s in spans
        if s.node_path == "action/0/repeat/sequence/0"
    )
    assert iters == ["0", "1", "2"]
    # every iteration parents onto the repeat container
    repeat = next(s for s in spans if s.node_path == "action/0")
    assert repeat.step_type is StepType.REPEAT
    assert all(
        s.parent_span_id == repeat.span_id
        for s in spans
        if s.node_path == "action/0/repeat/sequence/0"
    )


def test_parallel_branches_do_not_truncate_each_other() -> None:
    """The interaction bomb: a long-running branch step keeps its duration even
    when a *different* branch starts a step in between (spec §12b / §0B)."""
    actions: list[dict[str, Any]] = [
        {
            "parallel": [
                [{"delay": {"seconds": 5}}, {"action": "light.turn_off"}],
                [{"delay": {"seconds": 5}}],
            ]
        }
    ]
    trace = {
        "trigger": [_el("trigger", 0.1)],
        "action/0": [_el("action/0", 0.2)],
        # branch 0: delay starts at 1s, its next-in-branch step at 6s (~5s wide)
        "action/0/parallel/0/0": [_el("action/0/parallel/0/0", 1.0, result={"delay": 5.0})],
        "action/0/parallel/0/1": [_el("action/0/parallel/0/1", 6.0)],
        # branch 1: a lone delay starting at 1.5s — interleaves branch 0's window
        "action/0/parallel/1/0": [_el("action/0/parallel/1/0", 1.5, result={"delay": 5.0})],
    }
    spans = reconstruct(_payload(actions, trace))
    by_path = {s.node_path: s for s in spans}

    b0_delay = by_path["action/0/parallel/0/0"]
    b1_delay = by_path["action/0/parallel/1/0"]

    # branch 0's delay ends at its own next-in-branch step (6s), NOT at branch 1's
    # 1.5s start — proving branches are separate scopes.
    b0_ms = (b0_delay.end_unix_nano - b0_delay.start_unix_nano) / 1e6
    assert 4900 < b0_ms < 5100, f"branch-0 delay should be ~5s, got {b0_ms}ms"

    # branch discriminators are captured for both branches
    assert b0_delay.extra_attributes["ha.parallel_branch"] == "0"
    assert b1_delay.extra_attributes["ha.parallel_branch"] == "1"

    # the parallel container spans the whole block and both branches parent onto it
    parallel = by_path["action/0"]
    assert parallel.step_type is StepType.PARALLEL
    assert b0_delay.parent_span_id == parallel.span_id
    assert b1_delay.parent_span_id == parallel.span_id


def test_starts_are_monotonic_and_ends_bound_starts() -> None:
    actions: list[dict[str, Any]] = [{"action": "light.turn_on"}, {"delay": {"seconds": 2}}]
    trace = {
        "trigger": [_el("trigger", 0.1)],
        "action/0": [_el("action/0", 0.2)],
        "action/1": [_el("action/1", 0.3)],
    }
    spans = reconstruct(_payload(actions, trace))
    starts = [s.start_unix_nano for s in spans]
    assert starts == sorted(starts)
    assert all(isinstance(s.start_unix_nano, int) for s in spans)
    assert all(s.end_unix_nano >= s.start_unix_nano for s in spans)


def test_span_ids_are_deterministic() -> None:
    payload = _payload([{"action": "light.turn_on"}], {"trigger": [_el("trigger", 0.1)]})
    first = [s.span_id for s in reconstruct(payload)]
    second = [s.span_id for s in reconstruct(payload)]
    assert first == second
    assert all(len(sid) == 16 for sid in first)


def test_nested_parallel_reports_innermost_branch() -> None:
    """``ha.parallel_branch`` must key on the nearest enclosing ``parallel``.

    Returning the outermost index would file spans from two different inner
    lanes under one value, silently merging distinct concurrency lanes in any
    SigNoz group-by.
    """
    inner = {"parallel": [[{"action": "light.turn_on"}], [{"action": "light.turn_off"}]]}
    payload = _payload(
        [{"parallel": [[inner], [{"action": "cover.close_cover"}]]}],
        {
            "action/0/parallel/0/sequence/0/parallel/0/sequence/0": [
                {"timestamp": "2026-07-22T15:00:01.000000+00:00"}
            ],
            "action/0/parallel/0/sequence/0/parallel/1/sequence/0": [
                {"timestamp": "2026-07-22T15:00:02.000000+00:00"}
            ],
        },
    )
    branches = {
        s.node_path: s.extra_attributes.get("ha.parallel_branch")
        for s in reconstruct(payload)
        if "ha.parallel_branch" in s.extra_attributes
    }
    assert branches["action/0/parallel/0/sequence/0/parallel/0/sequence/0"] == "0"
    assert branches["action/0/parallel/0/sequence/0/parallel/1/sequence/0"] == "1"


def test_missing_timestamp_does_not_anchor_a_span_at_the_epoch() -> None:
    """An element with no ``timestamp`` must not drag the trace back to 1970."""
    payload = _payload(
        [{"action": "light.turn_on"}, {"action": "light.turn_off"}],
        {
            "action/0": [{"timestamp": "2026-07-22T15:00:01.000000+00:00"}],
            "action/1": [{}],  # no timestamp at all
        },
    )
    spans = reconstruct(payload)
    year_ns = 365 * 24 * 3600 * 1_000_000_000
    for s in spans:
        # anything anchored near the epoch lands decades before 2010
        assert s.start_unix_nano > 40 * year_ns, f"{s.node_path} anchored at the epoch"
        assert s.end_unix_nano - s.start_unix_nano < year_ns, f"{s.node_path} spans years"


def test_false_condition_and_untaken_branch_are_skipped_not_ok() -> None:
    """A condition that evaluated false is SKIPPED — otherwise the taken and
    untaken branches are indistinguishable, which is the question the tool answers."""
    payload = _payload(
        [{"choose": [{"conditions": [{"condition": "state"}], "sequence": []}]}],
        {
            "action/0": [
                {"timestamp": "2026-07-22T15:00:01.000000+00:00", "result": {"choice": "default"}}
            ],
            "action/0/choose/0/conditions/0": [
                {"timestamp": "2026-07-22T15:00:02.000000+00:00", "result": {"result": False}}
            ],
        },
    )
    by_path = {s.node_path: s for s in reconstruct(payload)}
    assert by_path["action/0/choose/0/conditions/0"].result is Result.SKIPPED
    assert by_path["action/0"].extra_attributes.get("ha.choice") == "default"


def test_delay_duration_comes_from_config_not_the_scope_boundary() -> None:
    """A delay must report its own length, not the boundary it happens to close.

    The trailing step of a scope inherits its parent's boundary, so without a
    real duration a 2-second delay ending a 10-second run reads 10 seconds wide.
    """
    payload = _payload(
        [{"delay": {"seconds": 2}}],
        {"action/0": [{"timestamp": "2026-07-22T15:00:01.000000+00:00"}]},
    )
    span = next(s for s in reconstruct(payload) if s.node_path == "action/0")
    assert (span.end_unix_nano - span.start_unix_nano) == 2_000_000_000
    assert span.extra_attributes["ha.end_inferred"] == "config_declared"


def test_every_span_declares_how_its_end_was_derived() -> None:
    """``ha.end_inferred`` marks measured vs inferred ends, so a bar is never
    presented as measured when it was guessed from a neighbour."""
    payload = _payload(
        [{"action": "light.turn_on"}],
        {"action/0": [{"timestamp": "2026-07-22T15:00:01.000000+00:00"}]},
    )
    allowed = {
        "recorded",
        "config_declared",
        "descendants",
        "next_sibling",
        "parent_boundary",
        "run_finish",
    }
    for s in reconstruct(payload):
        assert s.extra_attributes["ha.end_inferred"] in allowed
