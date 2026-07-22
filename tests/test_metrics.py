"""Unit tests for the ``state_changed`` -> gauge mapping (:mod:`homeapm.metrics`)."""

from __future__ import annotations

from homeapm.metrics import GaugeSample, room_for, sample_from_event


def _event(entity_id: str, state: str, **attrs: object) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "new_state": {"entity_id": entity_id, "state": state, "attributes": dict(attrs)},
    }


def test_numeric_sensor_maps_to_sensor_value() -> None:
    sample = sample_from_event(
        _event(
            "sensor.living_room_temperature",
            "25.0",
            device_class="temperature",
            unit_of_measurement="°C",
            friendly_name="Living Room Temperature",
        )
    )
    assert isinstance(sample, GaugeSample)
    assert sample.metric == "ha.sensor.value"
    assert sample.value == 25.0
    assert sample.attributes == {
        "entity_id": "sensor.living_room_temperature",
        "friendly_name": "Living Room Temperature",
        "device_class": "temperature",
        "unit": "°C",
        "room": "living_room",
    }


def test_battery_sensor_carries_device_class_and_unit() -> None:
    sample = sample_from_event(
        _event(
            "sensor.garage_door_battery",
            "55.0",
            device_class="battery",
            unit_of_measurement="%",
        )
    )
    assert sample is not None
    assert sample.metric == "ha.sensor.value"
    assert sample.attributes["device_class"] == "battery"
    assert sample.attributes["unit"] == "%"
    assert sample.attributes["room"] == "garage"


def test_input_number_without_device_class_defaults_to_none() -> None:
    sample = sample_from_event(
        _event("input_number.garage_battery", "55.0", unit_of_measurement="%")
    )
    assert sample is not None
    assert sample.metric == "ha.sensor.value"
    assert sample.attributes["device_class"] == "none"


def test_light_on_maps_to_entity_state_one() -> None:
    sample = sample_from_event(_event("light.hallway", "on"))
    assert sample is not None
    assert sample.metric == "ha.entity.state"
    assert sample.value == 1.0
    assert sample.attributes == {
        "entity_id": "light.hallway",
        "domain": "light",
        "room": "hallway",
    }


def test_cover_closed_maps_to_zero() -> None:
    sample = sample_from_event(_event("cover.garage_door", "closed"))
    assert sample is not None
    assert sample.metric == "ha.entity.state"
    assert sample.value == 0.0
    assert sample.attributes["domain"] == "cover"


def test_input_boolean_off_maps_to_zero() -> None:
    sample = sample_from_event(_event("input_boolean.motion_bedroom", "off"))
    assert sample is not None
    assert sample.metric == "ha.entity.state"
    assert sample.value == 0.0
    assert sample.attributes["room"] == "bedroom"


def test_unavailable_state_is_dropped() -> None:
    assert sample_from_event(_event("sensor.living_room_temperature", "unavailable")) is None
    assert sample_from_event(_event("light.hallway", "unknown")) is None


def test_missing_new_state_is_dropped() -> None:
    assert sample_from_event({"entity_id": "light.hallway", "new_state": None}) is None
    assert sample_from_event({}) is None


def test_room_fallback_is_whole_house() -> None:
    assert room_for("sensor.some_unmapped_thing") == "whole_house"
    # Substring hint still resolves an unmapped-but-named entity.
    assert room_for("light.kitchen_counter") == "kitchen"
