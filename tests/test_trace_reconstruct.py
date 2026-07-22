"""Golden-fixture harness for :func:`homeapm.trace_reconstruct.reconstruct`.

Two layers:

1. **Invariant tests** — parametrized over every discovered
   ``fixtures/*/trace_get.json``. They run the reconstruction and assert the
   structural invariants that must hold for *any* valid run, regardless of the
   specific automation. These guard the tree builder against regressions on
   every change (spec #14 / risk #3).

2. **Golden-snapshot mechanism** — compares the reconstructed spans against a
   committed ``expected_spans.json`` next to each fixture. Snapshots are
   (re)generated with ``HOMEAPM_UPDATE_GOLDEN=1 pytest``. Cases without a
   snapshot are skipped, so this is inert until the fixtures agent lands data
   and the reconstruction algorithm is finalized.

Everything skips cleanly when no fixtures exist, so the scaffold CI is green
before any real payload is dumped.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest

from homeapm.trace_reconstruct import SpanSpec, reconstruct

from .conftest import discover_trace_fixtures, load_json

_FIXTURES = discover_trace_fixtures()
_UPDATE_GOLDEN = os.environ.get("HOMEAPM_UPDATE_GOLDEN") == "1"


def _case_id(path: Path) -> str:
    """Human-readable parametrization id: the case directory name."""
    return path.parent.name


def _spec_to_jsonable(spec: SpanSpec) -> dict[str, Any]:
    """Serialize a :class:`SpanSpec` to a stable, diffable dict (enums → values)."""
    out: dict[str, Any] = {}
    for f in dataclasses.fields(spec):
        value = getattr(spec, f.name)
        out[f.name] = value.value if hasattr(value, "value") else value
    return out


@pytest.mark.skipif(not _FIXTURES, reason="no trace_get.json fixtures present yet")
@pytest.mark.parametrize("fixture_path", _FIXTURES, ids=_case_id)
def test_reconstruct_invariants(fixture_path: Path) -> None:
    """Structural invariants that hold for every valid reconstructed run."""
    payload = load_json(fixture_path)
    try:
        spans = reconstruct(payload)
    except NotImplementedError:
        pytest.skip("reconstruct() not implemented yet (scaffold stub)")

    assert spans, "reconstruct() returned no spans"

    ids = [s.span_id for s in spans]
    assert len(ids) == len(set(ids)), "span_id values must be unique"

    # exactly one root
    roots = [s for s in spans if s.parent_span_id is None]
    assert len(roots) == 1, f"expected exactly one root, got {len(roots)}"

    # every non-root parent id resolves to a known span
    id_set = set(ids)
    for s in spans:
        if s.parent_span_id is not None:
            assert s.parent_span_id in id_set, f"dangling parent for span {s.span_id}"

    # monotonic starts in returned (pre-order) order; ends bound starts
    prev_start = None
    for s in spans:
        assert s.end_unix_nano >= s.start_unix_nano, f"end<start for span {s.span_id}"
        if prev_start is not None:
            assert s.start_unix_nano >= prev_start, "starts must be non-decreasing"
        prev_start = s.start_unix_nano

    # every span carries the required §0A identity attributes
    for s in spans:
        assert s.automation_name, f"missing automation.name on span {s.span_id}"
        assert s.node_path, f"missing ha.node_path on span {s.span_id}"


@pytest.mark.skipif(not _FIXTURES, reason="no trace_get.json fixtures present yet")
@pytest.mark.parametrize("fixture_path", _FIXTURES, ids=_case_id)
def test_reconstruct_golden_snapshot(fixture_path: Path) -> None:
    """Compare reconstruction against a committed per-case golden snapshot.

    Regenerate with ``HOMEAPM_UPDATE_GOLDEN=1 pytest``. Skips when no snapshot
    is committed yet (inert until the algorithm is finalized).
    """
    payload = load_json(fixture_path)
    golden_path = fixture_path.parent / "expected_spans.json"

    try:
        actual = [_spec_to_jsonable(s) for s in reconstruct(payload)]
    except NotImplementedError:
        pytest.skip("reconstruct() not implemented yet (scaffold stub)")

    if _UPDATE_GOLDEN:
        golden_path.write_text(json.dumps(actual, indent=2, sort_keys=True), encoding="utf-8")
        pytest.skip(f"golden snapshot written: {golden_path.name}")

    if not golden_path.exists():
        pytest.skip("no expected_spans.json snapshot committed yet")

    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    assert actual == expected, f"reconstruction drifted from golden snapshot {golden_path.name}"


def test_fixture_harness_imports() -> None:
    """Smoke test: the harness + public API import even with zero fixtures.

    Guarantees a non-empty, green test session for the scaffold before any
    payload exists (so the CI badge is meaningful from commit one).
    """
    assert callable(reconstruct)
    assert SpanSpec.__dataclass_fields__  # frozen §0A schema is present
