"""Tests for the env-driven :mod:`homeapm.config` loader (no fixtures needed)."""

from __future__ import annotations

import pytest

from homeapm.config import Config, ConfigError, Mode, load_config


def _seeded_env() -> dict[str, str]:
    return {
        "HA_URL": "http://localhost:8123",
        "HA_TOKEN": "tok",
        "OTLP_ENDPOINT": "http://localhost:4318",
        "HOMEAPM_MODE": "seeded",
    }


def test_load_seeded_config() -> None:
    cfg = load_config(_seeded_env())
    assert cfg.mode is Mode.SEEDED
    assert cfg.ws_url == "ws://localhost:8123/api/websocket"
    assert cfg.otlp_traces_url == "http://localhost:4318/v1/traces"


def test_missing_otlp_endpoint_raises() -> None:
    env = _seeded_env()
    del env["OTLP_ENDPOINT"]
    with pytest.raises(ConfigError, match="OTLP_ENDPOINT"):
        load_config(env)


def test_byoh_requires_token() -> None:
    env = _seeded_env()
    env["HOMEAPM_MODE"] = "byoh"
    env["HA_TOKEN"] = ""
    with pytest.raises(ConfigError, match="HA_TOKEN"):
        load_config(env)


def test_bad_mode_raises() -> None:
    env = _seeded_env()
    env["HOMEAPM_MODE"] = "nonsense"
    with pytest.raises(ConfigError, match="HOMEAPM_MODE"):
        load_config(env)


def test_https_url_yields_wss() -> None:
    env = _seeded_env()
    env["HA_URL"] = "https://ha.example.com"
    cfg = load_config(env)
    assert cfg.ws_url == "wss://ha.example.com/api/websocket"


def test_config_is_frozen() -> None:
    cfg = load_config(_seeded_env())
    assert isinstance(cfg, Config)
    with pytest.raises(AttributeError):
        cfg.ha_url = "mutated"  # type: ignore[misc]
