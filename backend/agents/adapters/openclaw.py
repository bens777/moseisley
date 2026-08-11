"""OpenClawAdapter (§27): talks to a user-run OpenClaw Gateway via its documented
OpenAI-compatible HTTP API (default port 18789, bearer token auth).

Moseisley.sh keeps Telegram ownership (§19): OpenClaw is used purely as a reasoning
worker over HTTP; its own channel integrations stay unused.
Configuration: {"base_url": "http://localhost:18789", "model": optional}
Credentials: gateway token, stored encrypted.
"""
from __future__ import annotations

import httpx

from backend.agents.adapters.base import AgentAdapter, register
from backend.core.models import AgentConfig

DEFAULT_BASE_URL = "http://localhost:18789"


@register
class OpenClawAdapter(AgentAdapter):
    adapter_type = "openclaw"

    def _base_url(self, agent: AgentConfig) -> str:
        return (agent.configuration_json or {}).get("base_url", DEFAULT_BASE_URL).rstrip("/")

    def _headers(self, agent: AgentConfig) -> dict:
        headers = {"Content-Type": "application/json"}
        secret = self._secret(agent)
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        return headers

    async def health_check(self, agent: AgentConfig) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url(agent)}/v1/models",
                                        headers=self._headers(agent))
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def capabilities(self, agent: AgentConfig) -> dict:
        return {"chat": True, "transport": "openai_compatible", "runtime": "openclaw"}

    async def send_message(self, agent: AgentConfig, user_id, session_id, message, context) -> str:
        from backend.agents.adapters.base import AgentAdapterError

        cfg = agent.configuration_json or {}
        system = (
            "You are acting as a worker agent in the user's AI crew on Moseisley.sh. "
            "Platform context follows.\n\n"
            f"## Focus\n{context.get('focus_md', '')}\n"
        )
        payload: dict = {
            "model": cfg.get("model", "openclaw"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            "user": session_id,  # stable per-session identity for the gateway
        }
        try:
            async with httpx.AsyncClient(timeout=float(cfg.get("timeout", 120))) as client:
                resp = await client.post(f"{self._base_url(agent)}/v1/chat/completions",
                                         json=payload, headers=self._headers(agent))
        except httpx.HTTPError as e:
            raise AgentAdapterError(f"openclaw gateway unreachable: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise AgentAdapterError(f"openclaw gateway returned {resp.status_code}")
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as e:
            raise AgentAdapterError("openclaw gateway returned unexpected payload") from e

    async def cancel(self, agent: AgentConfig, session_id: str) -> None:
        return None
