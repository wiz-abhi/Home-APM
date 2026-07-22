"""``state_changed`` -> OTLP gauges per the FROZEN METRIC CONTRACT (spec CORE).

Observes Home Assistant ``state_changed`` events and republishes them as two
OTel gauges, exactly as every agent agreed to name them:

- ``ha.sensor.value`` (float) - numeric entity states. Attrs: ``entity_id``,
  ``friendly_name``, ``device_class``, ``unit``, ``room``.
- ``ha.entity.state`` (0/1)  - binary / on-off entities (lights, covers,
  ``input_boolean``, ...). Attrs: ``entity_id``, ``domain``, ``room``.

Both are implemented as **observable** gauges: each ``state_changed`` updates an
in-memory latest-value table, and the periodic reader re-exports the whole table
every interval, giving continuous lines in SigNoz. ``room`` is derived from a
static seeded-house map with a substring fallback, then ``whole_house``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from opentelemetry.metrics import CallbackOptions, Meter, Observation

from homeapm.config import Config

# --- room derivation for the seeded house ------------------------------------

# Explicit entity -> room map for the seeded demo house. Anything not listed
# falls back to a substring match on the entity_id, then ``whole_house``.
_ENTITY_ROOMS: dict[str, str] = {
    "light.hallway": "hallway",
    "input_boolean.hallway_light_state": "hallway",
    "input_boolean.motion_hallway": "hallway",
    "light.bedroom": "bedroom",
    "input_boolean.bedroom_light_state": "bedroom",
    "input_boolean.motion_bedroom": "bedroom",
    "input_boolean.morning_alarm": "bedroom",
    "light.living_room": "living_room",
    "input_boolean.living_room_light_state": "living_room",
    "input_boolean.heater_living_room": "living_room",
    "input_boolean.motion_living_room": "living_room",
    "sensor.living_room_temperature": "living_room",
    "input_number.living_room_temp_raw": "living_room",
    "climate.living_room_thermostat": "living_room",
    "cover.garage_door": "garage",
    "sensor.garage_door_battery": "garage",
    "input_boolean.garage_door_state": "garage",
    "input_number.garage_battery": "garage",
}

# Ordered substring probes (longest / most specific first).
_ROOM_HINTS: tuple[tuple[str, str], ...] = (
    ("living_room", "living_room"),
    ("hallway", "hallway"),
    ("bedroom", "bedroom"),
    ("garage", "garage"),
    ("kitchen", "kitchen"),
    ("bathroom", "bathroom"),
)

_ROOM_FALLBACK = "whole_house"

# --- binary state vocabulary --------------------------------------------------

# Domains whose state is on/off-ish rather than numeric.
_BINARY_DOMAINS = frozenset(
    {
        "light",
        "switch",
        "cover",
        "input_boolean",
        "binary_sensor",
        "lock",
        "fan",
        "climate",
        "media_player",
        "automation",
        "person",
        "device_tracker",
    }
)

# States that map to 1.0 in ``ha.entity.state``; the complement maps to 0.0.
_ON_STATES = frozenset(
    {"on", "open", "opened", "home", "unlocked", "playing", "active", "heat", "cool", "detected"}
)
_OFF_STATES = frozenset(
    {"off", "closed", "not_home", "away", "locked", "idle", "paused", "standby", "clear"}
)

# Non-values we never publish.
_SKIP_STATES = frozenset({"unavailable", "unknown", "none", ""})


def room_for(entity_id: str) -> str:
    """Return the room label for ``entity_id`` (map -> substring -> whole_house)."""
    mapped = _ENTITY_ROOMS.get(entity_id)
    if mapped is not None:
        return mapped
    lowered = entity_id.lower()
    for needle, room in _ROOM_HINTS:
        if needle in lowered:
            return room
    return _ROOM_FALLBACK


@dataclass(frozen=True, slots=True)
class GaugeSample:
    """One observation extracted from a ``state_changed`` event.

    Exactly one of the two frozen gauges is targeted, selected by :attr:`metric`.

    Attributes:
        metric: ``"ha.sensor.value"`` or ``"ha.entity.state"``.
        entity_id: Source entity, e.g. ``sensor.living_room_temperature``.
        value: Parsed numeric state (0.0/1.0 for the binary gauge).
        attributes: The frozen attribute set for this metric.
    """

    metric: str
    entity_id: str
    value: float
    attributes: dict[str, str]


_SENSOR_METRIC = "ha.sensor.value"
_STATE_METRIC = "ha.entity.state"


def sample_from_event(event: dict[str, Any]) -> GaugeSample | None:
    """Extract a :class:`GaugeSample` from a ``state_changed`` event ``data`` dict.

    Args:
        event: The HA event ``data`` object
            (``{"entity_id", "new_state", "old_state"}``).

    Returns:
        A sample if the new state is a publishable numeric or on/off value,
        else ``None`` (missing state, ``unavailable``/``unknown``, or an
        unrecognized non-numeric state).
    """
    entity_id = event.get("entity_id")
    new_state = event.get("new_state")
    if not isinstance(entity_id, str) or not isinstance(new_state, dict):
        return None

    raw = new_state.get("state")
    if not isinstance(raw, str) or raw.strip().lower() in _SKIP_STATES:
        return None

    attributes = new_state.get("attributes")
    attrs: dict[str, Any] = attributes if isinstance(attributes, dict) else {}
    domain = entity_id.split(".", 1)[0]
    room = room_for(entity_id)

    # Numeric, non-binary domain -> ha.sensor.value.
    numeric = _as_float(raw)
    if numeric is not None and domain not in _BINARY_DOMAINS:
        return GaugeSample(
            metric=_SENSOR_METRIC,
            entity_id=entity_id,
            value=numeric,
            attributes={
                "entity_id": entity_id,
                "friendly_name": _str(attrs.get("friendly_name"), entity_id),
                "device_class": _str(attrs.get("device_class"), "none"),
                "unit": _str(attrs.get("unit_of_measurement"), "none"),
                "room": room,
            },
        )

    # On/off state -> ha.entity.state (0/1).
    binary = _as_binary(raw, domain)
    if binary is not None:
        return GaugeSample(
            metric=_STATE_METRIC,
            entity_id=entity_id,
            value=binary,
            attributes={"entity_id": entity_id, "domain": domain, "room": room},
        )

    return None


def _as_float(raw: str) -> float | None:
    """Parse a state string to ``float`` or ``None`` if not numeric."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_binary(raw: str, domain: str) -> float | None:
    """Map an on/off-ish state to ``1.0``/``0.0``; ``None`` if not classifiable."""
    token = raw.strip().lower()
    if token in _ON_STATES:
        return 1.0
    if token in _OFF_STATES:
        return 0.0
    # A binary-domain entity in some other concrete state (e.g. climate "heat"
    # already caught above) counts as "on" if clearly not an off token.
    if domain in _BINARY_DOMAINS:
        return 0.0 if token in _OFF_STATES else 1.0
    return None


def _str(value: Any, default: str) -> str:
    """Coerce an attribute value to a non-empty string, else ``default``."""
    if isinstance(value, str) and value:
        return value
    if value is not None and not isinstance(value, str):
        return str(value)
    return default


class MetricsBridge:
    """Maps ``state_changed`` events to the two frozen OTel gauges over OTLP.

    Both gauges are observable: :meth:`handle_event` refreshes an in-memory
    latest-value table keyed by ``entity_id``, and the meter's periodic reader
    calls the registered callbacks to re-export the whole table each interval.

    Args:
        config: Resolved sidecar configuration.
        meter: The shared OTel meter (from :func:`homeapm.otlp_metrics.build_meter_provider`).
    """

    def __init__(self, config: Config, meter: Meter) -> None:
        self._config = config
        # entity_id -> (value, attributes) for each gauge.
        self._sensor: dict[str, tuple[float, dict[str, str]]] = {}
        self._state: dict[str, tuple[float, dict[str, str]]] = {}
        meter.create_observable_gauge(
            _SENSOR_METRIC,
            callbacks=[self._observe_sensor],
            description="Numeric Home Assistant entity states.",
        )
        meter.create_observable_gauge(
            _STATE_METRIC,
            callbacks=[self._observe_state],
            description="Binary Home Assistant entity states (0/1).",
        )

    def handle_event(self, event: dict[str, Any]) -> GaugeSample | None:
        """Ingest one ``state_changed`` ``data`` dict, updating the gauge tables.

        Returns:
            The :class:`GaugeSample` recorded, or ``None`` if the event was not
            publishable (so callers/tests can assert on the outcome).
        """
        sample = sample_from_event(event)
        if sample is None:
            return None
        self.record(sample)
        return sample

    def record(self, sample: GaugeSample) -> None:
        """Store one observation in the appropriate latest-value table."""
        table = self._sensor if sample.metric == _SENSOR_METRIC else self._state
        table[sample.entity_id] = (sample.value, sample.attributes)

    def _observe_sensor(self, _options: CallbackOptions) -> Iterable[Observation]:
        """Yield the current ``ha.sensor.value`` observations (periodic callback)."""
        return [Observation(value, attrs) for value, attrs in self._sensor.values()]

    def _observe_state(self, _options: CallbackOptions) -> Iterable[Observation]:
        """Yield the current ``ha.entity.state`` observations (periodic callback)."""
        return [Observation(value, attrs) for value, attrs in self._state.values()]

    @property
    def tracked_entities(self) -> int:
        """Count of distinct entities currently held across both gauges (tests)."""
        return len(set(self._sensor) | set(self._state))
