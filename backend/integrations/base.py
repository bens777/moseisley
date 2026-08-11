"""IntegrationAdapter contract (§35). Adapters hold credentials internally;
agents only ever see capability names and sanitized results."""
from __future__ import annotations

from backend.core.models import IntegrationConnection


class IntegrationError(Exception):
    pass


class IntegrationAdapter:
    """One instance per IntegrationConnection row."""

    integration_type = "base"

    def __init__(self, connection: IntegrationConnection):
        self.connection = connection

    async def health_check(self) -> bool:
        raise NotImplementedError

    def capabilities(self) -> list[str]:
        raise NotImplementedError

    async def read(self, operation: str, params: dict) -> dict:
        raise NotImplementedError

    async def execute(self, operation: str, params: dict) -> dict:
        raise NotImplementedError
