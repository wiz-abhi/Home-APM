"""Offline replay: reconstruct + emit every dumped ``trace/get`` fixture.

Usage::

    python -m homeapm.replay [FIXTURES_DIR] [--otlp URL]

Every ``FIXTURES_DIR/<case>/trace_get.json`` (and a bare
``FIXTURES_DIR/trace_get.json``) is loaded, run through
:func:`homeapm.trace_reconstruct.reconstruct`, and exported through
:class:`homeapm.otlp_emit.OTLPEmitter` to SigNoz. This is the no-house
replicability path (spec #14): clone, point at an OTLP endpoint, watch the flame
graphs appear — no running Home Assistant required.

The OTLP endpoint defaults to ``http://localhost:4318`` (override with
``--otlp`` or ``OTLP_ENDPOINT``). Each run's minted ``trace_id`` is printed so a
trace can be opened directly in the SigNoz UI.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from homeapm.config import Config, Mode
from homeapm.otlp_emit import OTLPEmitter
from homeapm.trace_reconstruct import reconstruct

_DEFAULT_OTLP = "http://localhost:4318"


def _discover(root: Path) -> list[Path]:
    """Return every ``trace_get.json`` under ``root`` (case dirs and root itself)."""
    found: list[Path] = []
    if (root / "trace_get.json").is_file():
        found.append(root / "trace_get.json")
    found.extend(sorted(root.glob("*/trace_get.json")))
    return found


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _parse_args(argv: list[str]) -> tuple[Path, str]:
    """Parse ``[FIXTURES_DIR] [--otlp URL]`` → (fixtures_dir, otlp_endpoint)."""
    otlp = os.environ.get("OTLP_ENDPOINT", _DEFAULT_OTLP).strip() or _DEFAULT_OTLP
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--otlp":
            i += 1
            if i >= len(argv):
                raise SystemExit("homeapm.replay: --otlp requires a URL argument")
            otlp = argv[i]
        else:
            positional.append(arg)
        i += 1
    fixtures = Path(positional[0]) if positional else Path("fixtures")
    return fixtures, otlp


def main(argv: list[str] | None = None) -> int:
    """Replay every discovered fixture through reconstruct + emit.

    Returns:
        ``0`` if at least one fixture was emitted, ``1`` if none were found.
    """
    fixtures_dir, otlp = _parse_args(sys.argv[1:] if argv is None else argv)

    if not fixtures_dir.is_dir():
        print(f"homeapm.replay: no such directory: {fixtures_dir}", file=sys.stderr)
        return 1

    paths = _discover(fixtures_dir)
    if not paths:
        print(f"homeapm.replay: no trace_get.json under {fixtures_dir}", file=sys.stderr)
        return 1

    config = Config(
        ha_url="http://localhost:8123",
        ha_token="",
        otlp_endpoint=otlp,
        mode=Mode.SEEDED,
    )
    emitter = OTLPEmitter(config)

    print(f"replaying {len(paths)} fixture(s) -> {config.otlp_traces_url}")
    total_spans = 0
    try:
        for path in paths:
            case = path.parent.name
            spans = reconstruct(_load(path))
            result = emitter.emit(spans)
            total_spans += result.span_count
            services = ", ".join(result.services)
            print(
                f"  {case:<20} run={result.run_id}  spans={result.span_count:<3} "
                f"trace_id={result.trace_id_hex}  services=[{services}]"
            )
    finally:
        emitter.shutdown()

    print(f"done: {len(paths)} run(s), {total_spans} span(s) exported to {otlp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
