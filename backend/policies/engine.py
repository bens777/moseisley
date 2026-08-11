"""Deterministic Policy Engine (§34, §57).

Permission levels per capability: DENIED < READ < DRAFT < EXECUTE.
Grants live on the integration connection (capabilities_json) — set explicitly by
the user. Nothing here consults an LLM, and external content can never mutate grants.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch

LEVELS = ["DENIED", "READ", "DRAFT", "EXECUTE"]

# Default grant when the user connects an integration but hasn't set a level:
# read-only (§57: default READ/ANALYZE/DRAFT — write ops still need explicit EXECUTE).
DEFAULT_LEVEL = "READ"

# capability -> minimum level required to invoke it
CAPABILITY_REQUIREMENTS = {
    "gmail.read": "READ",
    "gmail.draft": "DRAFT",
    "gmail.send": "EXECUTE",
    "calendar.read": "READ",
    "calendar.write": "EXECUTE",
    "webhook.execute": "EXECUTE",
    "rest.read": "READ",
    "rest.execute": "EXECUTE",
    "mcp.read": "READ",
    "mcp.execute": "EXECUTE",
    "n8n.execute": "EXECUTE",
    "stripe.read": "READ",
    "demo.read": "READ",
    "storage.read": "READ",
    "storage.write": "EXECUTE",
}


class PolicyDenied(Exception):
    def __init__(self, capability: str, reason: str):
        self.capability = capability
        self.reason = reason
        super().__init__(f"{capability}: {reason}")


def level_index(level: str) -> int:
    try:
        return LEVELS.index(level)
    except ValueError:
        return 0


def granted_level(connection_capabilities: dict, capability: str) -> str:
    return str(connection_capabilities.get(capability, DEFAULT_LEVEL))


async def check(
    db: AsyncSession,
    user_id: str,
    capability: str,
    connection_capabilities: dict,
) -> None:
    """Raise PolicyDenied/KillSwitchEngaged unless the capability may be invoked now."""
    required = CAPABILITY_REQUIREMENTS.get(capability)
    if required is None:
        raise PolicyDenied(capability, "unknown capability")
    grant = granted_level(connection_capabilities, capability)
    if level_index(grant) < level_index(required):
        raise PolicyDenied(capability, f"requires {required}, granted {grant}")
    # EXECUTE-level actions are external actions: blocked by the global kill switch (§82).
    if required == "EXECUTE":
        await killswitch.require_off(db, user_id, killswitch.DISABLE_EXTERNAL_ACTIONS)
