"""Shared pytest fixtures + fixture-directory discovery for golden tests.

Golden fixtures live in ``home-apm/fixtures/<case>/`` (owned by the fixtures
agent, not this scaffold). Each case directory is expected to contain a
``trace_get.json`` — a raw ``trace/get`` result dumped from a real run. Tests
skip cleanly when the directory is absent so the scaffold is green before any
fixture exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# repo root = tests/ -> home-apm/
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures"


def discover_trace_fixtures() -> list[Path]:
    """Return every ``fixtures/*/trace_get.json`` path (sorted; empty if none)."""
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(FIXTURES_DIR.glob("*/trace_get.json"))


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from ``path``."""
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """The golden-fixtures root directory."""
    return FIXTURES_DIR
