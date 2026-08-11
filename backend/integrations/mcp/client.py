"""MCP client (Model Context Protocol) over streamable HTTP (JSON-RPC 2.0).

Supports initialize / tools/list / tools/call against a remote MCP server URL,
with optional bearer auth (encrypted at rest). MCP outputs are untrusted content (§80).
"""
from __future__ import annotations

import itertools
import json

import httpx

from backend.core.crypto import decrypt_secret
from backend.integrations.base import IntegrationAdapter, IntegrationError

PROTOCOL_VERSION = "2025-03-26"


class McpAdapter(IntegrationAdapter):
    integration_type = "mcp"

    def __init__(self, connection):
        super().__init__(connection)
        self._ids = itertools.count(1)
        self._session_id: str | None = None

    def capabilities(self) -> list[str]:
        return ["mcp.read", "mcp.execute"]

    @property
    def _url(self) -> str:
        url = (self.connection.configuration_json or {}).get("url")
        if not url:
            raise IntegrationError("mcp connection missing url")
        return url

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.connection.encrypted_credentials:
            headers["Authorization"] = f"Bearer {decrypt_secret(self.connection.encrypted_credentials)}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self._url, json=payload, headers=self._headers())
        if resp.status_code not in (200, 202):
            raise IntegrationError(f"mcp server returned {resp.status_code}")
        if sid := resp.headers.get("Mcp-Session-Id"):
            self._session_id = sid
        body = resp.text
        # streamable-http servers may reply as SSE; extract the data line
        if body.startswith("event:") or "\ndata:" in body or body.startswith("data:"):
            for line in body.splitlines():
                if line.startswith("data:"):
                    body = line[5:].strip()
                    break
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            raise IntegrationError("mcp server returned invalid JSON") from e
        if isinstance(data, dict) and data.get("error"):
            raise IntegrationError(f"mcp error: {data['error'].get('message')}")
        return data.get("result", {}) if isinstance(data, dict) else {}

    async def initialize(self) -> dict:
        result = await self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mychief", "version": "0.1.0"},
        })
        try:
            await self._rpc("notifications/initialized")
        except IntegrationError:
            pass
        return result

    async def health_check(self) -> bool:
        try:
            await self.initialize()
            return True
        except Exception:
            return False

    async def read(self, operation: str, params: dict) -> dict:
        if operation == "tools.list":
            await self.initialize()
            return await self._rpc("tools/list")
        raise IntegrationError(f"unknown read operation: {operation}")

    async def execute(self, operation: str, params: dict) -> dict:
        if operation == "tools.call":
            await self.initialize()
            return await self._rpc("tools/call", {
                "name": params["name"], "arguments": params.get("arguments", {}),
            })
        raise IntegrationError(f"unknown execute operation: {operation}")
