"""Minimal raw JSON-RPC client for the SigNoz MCP server (streamable HTTP).

Copies the proven handshake from ``warmup-agent/mcp_demo.py``: ``initialize`` →
capture the ``Mcp-Session-Id`` response header → ``notifications/initialized`` →
``tools/call``. Responses may arrive as a plain JSON body or as Server-Sent
Events (``data:`` lines); both are handled.

Deliberately dependency-light (only ``httpx``, already a project dep) so the
"ask your house" CLI needs no new packages.
"""

from __future__ import annotations

import json
from types import TracebackType
from typing import Any

import httpx

DEFAULT_URL = "http://localhost:8000/mcp"
_PROTOCOL_VERSION = "2025-03-26"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class McpError(RuntimeError):
    """Raised when the MCP server returns a JSON-RPC error or an auth failure."""


def _parse(resp: httpx.Response) -> dict[str, Any] | None:
    """Decode a JSON-RPC response body (plain JSON or an SSE ``data:`` frame)."""
    content_type = resp.headers.get("content-type", "")
    if "event-stream" in content_type:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                parsed: dict[str, Any] = json.loads(line[5:].strip())
                return parsed
        return None
    if not resp.text:
        return None
    body: dict[str, Any] = resp.json()
    return body


class McpClient:
    """A single-session MCP client. Use as a context manager.

    Example:
        >>> with McpClient() as mcp:
        ...     rows = mcp.call_tool("signoz_list_services", {"timeRange": "1h"})
    """

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 30.0) -> None:
        self._url = url
        self._client = httpx.Client(timeout=timeout)
        self._session: str | None = None
        self._id = 0

    def __enter__(self) -> McpClient:
        self._initialize()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._client.close()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        request_id: int | None = None,
    ) -> tuple[httpx.Response, dict[str, Any] | None]:
        headers = dict(_HEADERS)
        if self._session is not None:
            headers["Mcp-Session-Id"] = self._session
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if request_id is not None:
            body["id"] = request_id
        try:
            resp = self._client.post(self._url, headers=headers, json=body)
        except httpx.HTTPError as err:  # pragma: no cover - network failure path
            raise McpError(f"MCP transport error calling {method}: {err}") from err
        if resp.status_code in (401, 403):
            raise McpError(
                f"MCP auth failed ({resp.status_code}) on {method}: the service-account "
                f"key may have expired. See tools/ask/README.md 'MCP auth' for the fix."
            )
        return resp, _parse(resp)

    def _initialize(self) -> None:
        resp, out = self._rpc(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "home-apm-ask", "version": "1.0"},
            },
            request_id=self._next_id(),
        )
        if out is None or "result" not in out:
            raise McpError(f"MCP initialize returned no result: {out!r}")
        self._session = resp.headers.get("Mcp-Session-Id")
        self._rpc("notifications/initialized", {})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call an MCP tool and return the concatenated text content.

        Raises:
            McpError: If the server returns a JSON-RPC error.
        """
        _, out = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
            request_id=self._next_id(),
        )
        if out is None:
            raise McpError(f"empty response from tool {name}")
        if "error" in out:
            raise McpError(f"tool {name} errored: {json.dumps(out['error'])[:300]}")
        content = out.get("result", {}).get("content", [])
        return "\n".join(item.get("text", "") for item in content)
