"""CustomHTTPAgentAdapter (§27): the guaranteed escape hatch for any external agent.

Contract (documented in README):
  POST {endpoint}
  headers: {auth_header: auth_value}  (value stored encrypted)
  body: {"user_id", "session_id", "message", "context", "agent": <optional identifier>}
  reply: JSON with one of the keys reply|response|message|text|output (string)
Health: POST {endpoint} with {"type": "health_check"} → HTTP 2xx.
"""
from __future__ import annotations

import httpx

from backend.agents.adapters.base import AgentAdapter, register
from backend.core.models import AgentConfig


@register
class CustomHTTPAgentAdapter(AgentAdapter):
    adapter_type = "custom_http"

    def _config(self, agent: AgentConfig) -> dict:
        return agent.configuration_json or {}

    def _headers(self, agent: AgentConfig) -> dict:
        cfg = self._config(agent)
        headers = {"Content-Type": "application/json"}
        secret = self._secret(agent)
        if secret:
            headers[cfg.get("auth_header", "Authorization")] = secret
        return headers

    async def health_check(self, agent: AgentConfig) -> bool:
        cfg = self._config(agent)
        endpoint = cfg.get("endpoint")
        if not endpoint:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(endpoint, json={"type": "health_check"},
                                         headers=self._headers(agent))
            return 200 <= resp.status_code < 300
        except httpx.HTTPError:
            return False

    async def capabilities(self, agent: AgentConfig) -> dict:
        return {"chat": True, "transport": "http"}

    async def send_message(self, agent: AgentConfig, user_id, session_id, message, context) -> str:
        from backend.agents.adapters.base import AgentAdapterError

        cfg = self._config(agent)
        endpoint = cfg.get("endpoint")
        if not endpoint:
            raise AgentAdapterError("custom agent endpoint not configured")
        timeout = float(cfg.get("timeout", 60))
        payload = {
            "user_id": user_id, "session_id": session_id, "message": message,
            "context": context,
        }
        if cfg.get("agent_identifier"):
            payload["agent"] = cfg["agent_identifier"]
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=payload, headers=self._headers(agent))
        except httpx.HTTPError as e:
            raise AgentAdapterError(f"custom agent unreachable: {type(e).__name__}") from e
        if resp.status_code >= 300:
            raise AgentAdapterError(f"custom agent returned {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise AgentAdapterError("custom agent returned non-JSON") from e
        for key in ("reply", "response", "message", "text", "output"):
            if isinstance(data.get(key), str):
                return data[key]
        raise AgentAdapterError("custom agent response missing reply field")

    async def cancel(self, agent: AgentConfig, session_id: str) -> None:
        return None
