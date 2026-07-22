"""Turn reconstructed :class:`SpanSpec` lists into OTel spans and export via OTLP.

This module owns the ``run_id → trace_id`` map (spec §0A): every span from one
run shares a single sidecar-minted trace id, so a run renders as one flame
graph and #13 can later link a log's ``context.id`` to that ``trace_id``.

It maps each :class:`~homeapm.trace_reconstruct.SpanSpec` onto an OTel SDK span
with the frozen §0A attributes, the deliberate CLIENT/SERVER ``span.kind``
pairing that draws the house service map (#15), and ``service.name`` per HA
domain via per-domain tracer providers/resources.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from homeapm.config import Config
from homeapm.trace_reconstruct import SpanSpec

_TRACE_ID_BYTES = 16
_SPAN_ID_BYTES = 8


class TraceIdRegistry:
    """Owns the per-run ``run_id → trace_id`` mapping (spec §0A).

    A 128-bit trace id is minted once per ``run_id`` and reused for every span
    in that run and for later log correlation (#13).
    """

    def __init__(self) -> None:
        self._by_run: dict[str, int] = {}

    def trace_id_for(self, run_id: str) -> int:
        """Return (minting once) the 128-bit trace id for ``run_id``."""
        tid = self._by_run.get(run_id)
        if tid is None:
            tid = int.from_bytes(secrets.token_bytes(_TRACE_ID_BYTES), "big")
            self._by_run[run_id] = tid
        return tid

    def known(self, run_id: str) -> bool:
        """Whether a trace id has already been minted for ``run_id``."""
        return run_id in self._by_run


@dataclass(slots=True)
class EmitResult:
    """Outcome of emitting one run's spans."""

    run_id: str
    trace_id_hex: str
    span_count: int


class OTLPEmitter:
    """Convert :class:`SpanSpec` lists to OTel spans and export over OTLP/HTTP.

    Args:
        config: Resolved sidecar configuration (provides the OTLP endpoint and
            resource namespace).
        registry: Shared trace-id registry (injectable for tests).
    """

    def __init__(self, config: Config, registry: TraceIdRegistry | None = None) -> None:
        self._config = config
        self._registry = registry or TraceIdRegistry()

    @property
    def registry(self) -> TraceIdRegistry:
        """The owned ``run_id → trace_id`` registry (consumed by #13)."""
        return self._registry

    def emit(self, spans: list[SpanSpec]) -> EmitResult:
        """Export one run's reconstructed spans as a single OTLP trace.

        All spans must share one ``run_id``; the minted trace id is looked up
        (or created) via :attr:`registry`. Parent/child ids from the specs are
        preserved so the tree renders as one flame graph.

        Args:
            spans: A non-empty list from
                :func:`homeapm.trace_reconstruct.reconstruct`.

        Returns:
            An :class:`EmitResult` describing the exported trace.

        Raises:
            NotImplementedError: Until the OTel SDK wiring is implemented.
        """
        raise NotImplementedError(
            "emit(): build SDK spans with §0A attrs + CLIENT/SERVER kinds and "
            "export to config.otlp_traces_url."
        )

    def _provider_for_service(self, service_name: str) -> object:
        """Return (caching) the tracer provider bound to ``service_name``.

        A distinct OTel ``Resource`` (``service.name`` per HA domain) is needed
        so the SigNoz service map draws automation → light/climate/cover (#15).

        Raises:
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("_provider_for_service(): implement per-domain providers.")

    def shutdown(self) -> None:
        """Flush and shut down all span processors/exporters."""
        raise NotImplementedError("shutdown(): flush and close exporters.")


def span_id_hex() -> str:
    """Mint a random 64-bit span id as lowercase hex (helper for emission)."""
    return secrets.token_bytes(_SPAN_ID_BYTES).hex()
