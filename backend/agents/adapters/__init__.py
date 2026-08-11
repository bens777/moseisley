"""Agent adapters (§26-27). Native chat is handled directly by the router; external
adapter classes are resolved here."""
from __future__ import annotations


def get_adapter(agent_config):
    from backend.agents.adapters.base import ADAPTER_TYPES

    cls = ADAPTER_TYPES.get(agent_config.adapter_type)
    return cls() if cls else None
