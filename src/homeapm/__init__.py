"""Home APM — Home Assistant automation traces as OTLP span trees on SigNoz.

Pipeline: HA WebSocket ``trace/get`` payloads → pure reconstruction into a
span tree (:mod:`homeapm.trace_reconstruct`) → OTLP export to SigNoz
(:mod:`homeapm.otlp_emit`).
"""

__version__ = "0.1.0"
