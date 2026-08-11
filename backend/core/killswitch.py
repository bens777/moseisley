"""Global deterministic kill switches (§82).

Checked in code at execution boundaries. Never implemented as prompt instructions.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import SystemSetting

PAUSE_ALL_AGENTS = "pause_all_agents"
DISABLE_LLM = "disable_llm"
DISABLE_SPENDING = "disable_spending"
DISABLE_EXTERNAL_ACTIONS = "disable_external_actions"
EMERGENCY_STOP = "emergency_stop"  # master switch: engages everything at once

ALL_SWITCHES = [PAUSE_ALL_AGENTS, DISABLE_LLM, DISABLE_SPENDING, DISABLE_EXTERNAL_ACTIONS,
                EMERGENCY_STOP]


async def get_setting(db: AsyncSession, user_id: str, key: str) -> dict:
    row = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.user_id == user_id, SystemSetting.key == key)
        )
    ).scalar_one_or_none()
    return row.value_json if row else {}


async def set_setting(db: AsyncSession, user_id: str, key: str, value: dict) -> None:
    row = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.user_id == user_id, SystemSetting.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(user_id=user_id, key=key, value_json=value))
    else:
        row.value_json = value
    await db.flush()


async def is_on(db: AsyncSession, user_id: str, switch: str) -> bool:
    return bool((await get_setting(db, user_id, switch)).get("on", False))


async def set_switch(db: AsyncSession, user_id: str, switch: str, on: bool) -> None:
    if switch not in ALL_SWITCHES:
        raise ValueError(f"unknown kill switch: {switch}")
    await set_setting(db, user_id, switch, {"on": on})


class KillSwitchEngaged(Exception):
    def __init__(self, switch: str):
        self.switch = switch
        super().__init__(f"kill switch engaged: {switch}")


async def require_off(db: AsyncSession, user_id: str, switch: str) -> None:
    if await is_on(db, user_id, switch):
        raise KillSwitchEngaged(switch)


async def set_emergency_stop(db: AsyncSession, user_id: str, on: bool) -> None:
    """Emergency Stop (owner directive §54): one red switch that engages/releases
    every kill switch simultaneously. Deterministic; checked at every execution boundary."""
    for switch in ALL_SWITCHES:
        await set_switch(db, user_id, switch, on)


async def require_operational(db: AsyncSession, user_id: str, switch: str) -> None:
    """Raise if the Emergency Stop or the specific switch is engaged."""
    if await is_on(db, user_id, EMERGENCY_STOP):
        raise KillSwitchEngaged(EMERGENCY_STOP)
    if switch != EMERGENCY_STOP and await is_on(db, user_id, switch):
        raise KillSwitchEngaged(switch)
