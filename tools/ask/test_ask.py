"""Offline unit tests for the ask-your-house pipeline (no network, no house).

Run: ``python -m pytest tools/ask/test_ask.py``

These cover the pure, deterministic parts: heuristic intent parsing, builder-row
extraction, fact distillation, and the fallback narration — everything except
the two Gemini calls and the live MCP round-trips.
"""

from __future__ import annotations

import json

from ask import (
    Intent,
    _heuristic_intent,
    _heuristic_narration,
    _human_duration,
    _rows_from_builder,
    extract_facts,
)


def test_heuristic_intent_classifies_the_three_demo_questions() -> None:
    assert _heuristic_intent("why did my hallway lights turn on at 3am?").question_type == "why"
    assert _heuristic_intent("why is my morning routine slow?").question_type == "slow"
    assert _heuristic_intent("did anything fail tonight?").question_type == "failed"
    assert _heuristic_intent("why did my hallway lights turn on?").automation_hint == "hallway"


def test_human_duration_renders_ms_and_seconds() -> None:
    assert _human_duration(0) == "under a millisecond"
    assert _human_duration(1_000_000) == "1ms"
    assert _human_duration(53_000_000_000) == "53.0s"


def test_rows_from_builder_unwraps_nested_result() -> None:
    text = json.dumps(
        {
            "status": "success",
            "data": {"data": {"results": [{"rows": [{"data": {"name": "trigger"}}]}]}},
        }
    )
    assert _rows_from_builder(text) == [{"name": "trigger"}]


def _hallway_spans() -> list[dict[str, object]]:
    """A minimal stand-in for the hallway 3am trace (silent choose branch)."""
    return [
        {
            "ha.node_path": "__root__",
            "ha.step_type": "sequence",
            "name": "Hallway Lights 3AM",
            "automation.name": "Hallway Lights 3AM",
            "automation.room": "hallway",
            "durationNano": 1_000_000,
        },
        {"ha.node_path": "trigger/0", "ha.step_type": "trigger", "name": "trigger",
         "automation.name": "Hallway Lights 3AM", "durationNano": 200_000},
        {"ha.node_path": "action/0", "ha.step_type": "choose", "name": "choose",
         "automation.name": "Hallway Lights 3AM", "durationNano": 500_000},
        {"ha.node_path": "action/0/choose/0", "ha.step_type": "choose", "name": "choose branch 0",
         "automation.name": "Hallway Lights 3AM", "durationNano": 400_000},
        {"ha.node_path": "action/0/choose/0/conditions/0", "ha.step_type": "condition",
         "name": "condition: template", "automation.name": "Hallway Lights 3AM",
         "durationNano": 100_000},
        {"ha.node_path": "action/0/choose/0/sequence/0", "ha.step_type": "service_call",
         "name": "light.turn_on", "service.name": "ha.light",
         "automation.name": "Hallway Lights 3AM", "durationNano": 300_000},
    ]


def test_extract_facts_captures_the_silent_choose_branch() -> None:
    facts = extract_facts(_hallway_spans(), "abc123")
    assert facts.automation == "Hallway Lights 3AM"
    assert facts.trigger == "trigger"
    assert "choose branch 0" in facts.branches
    assert "condition: template" in facts.conditions
    assert any("light.turn_on" in c for c in facts.service_calls)
    assert facts.total_human == "1ms"


def test_extract_facts_flags_the_slow_villain() -> None:
    spans = [
        {"ha.node_path": "__root__", "ha.step_type": "sequence", "name": "Morning Routine",
         "automation.name": "Morning Routine", "durationNano": 53_000_000_000},
        {"ha.node_path": "action/1", "ha.step_type": "wait", "name": "wait_for_trigger",
         "automation.name": "Morning Routine", "durationNano": 52_900_000_000},
        {"ha.node_path": "action/2", "ha.step_type": "service_call", "name": "light.turn_on",
         "service.name": "ha.light", "automation.name": "Morning Routine", "durationNano": 900_000},
    ]
    facts = extract_facts(spans, "def456")
    assert facts.slowest_name == "wait_for_trigger"
    assert facts.slowest_seconds == 52.9
    answer = _heuristic_narration(facts, Intent("slow", "morning routine"))
    assert "wait_for_trigger" in answer and "52.9s" in answer


def test_extract_facts_surfaces_template_errors() -> None:
    spans = [
        {"ha.node_path": "__root__", "ha.step_type": "sequence", "name": "Good Night",
         "automation.name": "Good Night", "durationNano": 8_000_000_000},
        {"ha.node_path": "action/2", "ha.step_type": "service_call",
         "name": "persistent_notification.create", "service.name": "ha.persistent_notification",
         "automation.name": "Good Night", "durationNano": 500_000, "has_error": True,
         "ha.template_errors":
             "Error rendering data template: ZeroDivisionError: division by zero"},
    ]
    facts = extract_facts(spans, "ghi789")
    assert facts.errors and "division by zero" in facts.errors[0]
    answer = _heuristic_narration(facts, Intent("failed", None))
    assert "Good Night" in answer and "division by zero" in answer
