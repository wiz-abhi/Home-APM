"""``state_changed`` → OTLP gauges for a handful of seeded sensors (spec CORE).

Observes Home Assistant ``state_changed`` events and republishes numeric states
as OTel gauges, stamping ``automation.room`` so the room-centric dashboard (#9)
can slice them. Kept intentionally small: 4-5 seeded sensors, not a general
metrics bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeapm.config import Config


@dataclass(frozen=True, slots=True)
class GaugeSample:
    """One numeric observation extracted from a ``state_changed`` event.

    Attributes:
        entity_id: Source entity, e.g. ``sensor.living_room_temperature``.
        value: Parsed numeric state.
        room: ``automation.room`` label for dashboard slicing.
        unit: Optional unit of measurement.
    """

    entity_id: str
    value: float
    room: str
    unit: str | None = None


class MetricsBridge:
    """Maps ``state_changed`` events to OTel gauges over OTLP.

    Args:
        config: Resolved sidecar configuration.
        entity_rooms: Map of watched ``entity_id`` → room label.
    """

    def __init__(self, config: Config, entity_rooms: dict[str, str]) -> None:
        self._config = config
        self._entity_rooms = entity_rooms

    def sample_from_event(self, event: dict[str, Any]) -> GaugeSample | None:
        """Extract a :class:`GaugeSample` from a ``state_changed`` event.

        Args:
            event: The HA event ``data`` object.

        Returns:
            A sample if the entity is watched and its new state is numeric,
            else ``None``.

        Raises:
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("sample_from_event(): parse state_changed → GaugeSample.")

    def record(self, sample: GaugeSample) -> None:
        """Publish one gauge observation via the OTel meter.

        Raises:
            NotImplementedError: Until implemented.
        """
        raise NotImplementedError("record(): publish gauge via OTLP metrics exporter.")
