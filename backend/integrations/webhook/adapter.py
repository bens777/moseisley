"""Generic REST / webhook adapters and the n8n specialization.

Configuration (per connection):
  webhook: {"url": ..., "method": "POST", "headers": {...}}          → webhook.execute
  rest:    {"base_url": ..., "headers": {...}}                        → rest.read / rest.execute
  n8n:     {"url": <n8n webhook url>, "headers": {...}}               → n8n.execute
Secret header values may be stored encrypted in encrypted_credentials as JSON.
"""
from __future__ import annotations

import json

import httpx

from backend.core.crypto import decrypt_secret
from backend.integrations.base import IntegrationAdapter, IntegrationError


class _HttpAdapter(IntegrationAdapter):
    def _config(self) -> dict:
        return self.connection.configuration_json or {}

    def _headers(self) -> dict:
        headers = dict(self._config().get("headers") or {})
        if self.connection.encrypted_credentials:
            try:
                headers.update(json.loads(decrypt_secret(self.connection.encrypted_credentials)))
            except Exception as e:
                raise IntegrationError("invalid encrypted headers") from e
        return headers


class WebhookAdapter(_HttpAdapter):
    integration_type = "webhook"

    def capabilities(self) -> list[str]:
        return ["webhook.execute"]

    async def health_check(self) -> bool:
        return bool(self._config().get("url"))

    async def read(self, operation: str, params: dict) -> dict:
        raise IntegrationError("webhook adapter has no read operations")

    async def execute(self, operation: str, params: dict) -> dict:
        if operation != "trigger":
            raise IntegrationError(f"unknown execute operation: {operation}")
        url = self._config().get("url")
        if not url:
            raise IntegrationError("webhook url not configured")
        method = str(self._config().get("method", "POST")).upper()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, url, json=params.get("payload", {}), headers=self._headers())
        ok = 200 <= resp.status_code < 300
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text[:2000]}
        return {"status": "SUCCESS" if ok else "FAILED", "http_status": resp.status_code, "response": body}


class RestAdapter(_HttpAdapter):
    integration_type = "rest"

    def capabilities(self) -> list[str]:
        return ["rest.read", "rest.execute"]

    def _url(self, path: str) -> str:
        base = self._config().get("base_url", "").rstrip("/")
        if not base:
            raise IntegrationError("rest base_url not configured")
        if not path.startswith("/"):
            raise IntegrationError("path must start with /")
        return base + path

    async def health_check(self) -> bool:
        return bool(self._config().get("base_url"))

    async def read(self, operation: str, params: dict) -> dict:
        if operation != "get":
            raise IntegrationError(f"unknown read operation: {operation}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self._url(params.get("path", "/")),
                                    params=params.get("query"), headers=self._headers())
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text[:5000]}
        return {"http_status": resp.status_code, "response": body}

    async def execute(self, operation: str, params: dict) -> dict:
        if operation not in ("post", "put", "delete"):
            raise IntegrationError(f"unknown execute operation: {operation}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(operation.upper(), self._url(params.get("path", "/")),
                                        json=params.get("payload"), headers=self._headers())
        ok = 200 <= resp.status_code < 300
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text[:5000]}
        return {"status": "SUCCESS" if ok else "FAILED", "http_status": resp.status_code, "response": body}


class N8nAdapter(WebhookAdapter):
    """n8n workflows are triggered through their webhook nodes."""

    integration_type = "n8n"

    def capabilities(self) -> list[str]:
        return ["n8n.execute"]
