"""Environment-driven configuration for the Home APM sidecar.

All runtime knobs are read from the process environment so the same image runs
in the zero-config seeded demo (``mode=seeded``) and against a user's own Home
Assistant (``mode=byoh``). Secrets (the HA long-lived access token) are never
committed; they arrive via ``HA_TOKEN`` or a gitignored ``.ha-runtime/`` file.

Environment variables
---------------------
- ``HA_URL``          Base Home Assistant URL, e.g. ``http://localhost:8123``.
- ``HA_TOKEN``        Long-lived access token for the WebSocket API.
- ``OTLP_ENDPOINT``   OTLP HTTP collector base, e.g. ``http://localhost:4318``.
- ``HOMEAPM_MODE``    ``seeded`` (deterministic demo) or ``byoh`` (bring-your-own-HA).
- ``HOMEAPM_SERVICE_NAMESPACE``  Optional resource namespace (default ``homeapm``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    """Onboarding mode. ``SEEDED`` is the zero-config replicability demo."""

    SEEDED = "seeded"
    BYOH = "byoh"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed.

    The message is intended to be a single human-readable sentence surfaced at
    boot (see the two-mode onboarding requirement, spec §7).
    """


@dataclass(frozen=True, slots=True)
class Config:
    """Immutable, fully-resolved sidecar configuration.

    Attributes:
        ha_url: Base Home Assistant URL (no trailing slash).
        ha_token: Long-lived access token. Never logged.
        otlp_endpoint: OTLP HTTP base URL (no trailing slash); the traces
            signal is posted to ``{otlp_endpoint}/v1/traces``.
        mode: Onboarding mode.
        service_namespace: OTel ``service.namespace`` resource attribute.
    """

    ha_url: str
    ha_token: str
    otlp_endpoint: str
    mode: Mode
    service_namespace: str = "homeapm"

    @property
    def ws_url(self) -> str:
        """WebSocket API URL derived from :attr:`ha_url` (``.../api/websocket``)."""
        scheme = "wss" if self.ha_url.startswith("https") else "ws"
        host = self.ha_url.split("://", 1)[-1].rstrip("/")
        return f"{scheme}://{host}/api/websocket"

    @property
    def otlp_traces_url(self) -> str:
        """Full OTLP/HTTP traces endpoint."""
        return f"{self.otlp_endpoint.rstrip('/')}/v1/traces"


def load_config(env: dict[str, str] | None = None) -> Config:
    """Build a :class:`Config` from the environment.

    Args:
        env: Optional mapping to read from instead of :data:`os.environ`
            (used by tests).

    Returns:
        A fully-populated, validated :class:`Config`.

    Raises:
        ConfigError: If a required variable is missing, with a one-sentence
            remediation message.
    """
    src = os.environ if env is None else env

    mode_raw = src.get("HOMEAPM_MODE", Mode.SEEDED.value).strip().lower()
    try:
        mode = Mode(mode_raw)
    except ValueError as exc:
        raise ConfigError(f"HOMEAPM_MODE must be 'seeded' or 'byoh', got {mode_raw!r}.") from exc

    ha_url = src.get("HA_URL", "").strip()
    ha_token = src.get("HA_TOKEN", "").strip()
    otlp_endpoint = src.get("OTLP_ENDPOINT", "").strip()
    service_namespace = src.get("HOMEAPM_SERVICE_NAMESPACE", "homeapm").strip() or "homeapm"

    _validate(ha_url=ha_url, ha_token=ha_token, otlp_endpoint=otlp_endpoint, mode=mode)

    return Config(
        ha_url=ha_url,
        ha_token=ha_token,
        otlp_endpoint=otlp_endpoint,
        mode=mode,
        service_namespace=service_namespace,
    )


def _validate(*, ha_url: str, ha_token: str, otlp_endpoint: str, mode: Mode) -> None:
    """Validate required fields, raising :class:`ConfigError` with a human sentence."""
    if not otlp_endpoint:
        raise ConfigError("OTLP_ENDPOINT is required, e.g. OTLP_ENDPOINT=http://localhost:4318.")
    if not ha_url:
        raise ConfigError("HA_URL is required, e.g. HA_URL=http://localhost:8123.")
    if mode is Mode.BYOH and not ha_token:
        raise ConfigError(
            "HA_TOKEN is required in byoh mode; create a long-lived access token "
            "in Home Assistant (Profile → Security) and set HA_TOKEN."
        )
