from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core import killswitch
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.providers import factory_pool, registry

router = APIRouter(prefix="/settings")


class UpdateSettingsRequest(BaseModel):
    timezone: str | None = None
    autonomy_mode: str | None = None
    settings: dict | None = None


@router.get("")
async def get_user_settings(user: CurrentUser, db: DB):
    switches = {s: await killswitch.is_on(db, user.id, s) for s in killswitch.ALL_SWITCHES}
    factory: dict = {"available": factory_pool.factory_available()}
    if factory["available"]:
        tier = await factory_pool.get_factory_tier(db, user)
        factory.update({
            "tier": tier,
            "trial_days_left": factory_pool.trial_days_left(user),
            "fuel_used_today": await registry.factory_fuel_used_today(db, user),
            "fuel_cap": await factory_pool.daily_cap_for_user(db, user, tier),
            "fuel_balance": factory_pool.get_fuel_balance(user),  # purchased at The Bar; never expires
            "has_provider_connections": await factory_pool.has_provider_connections(db, user.id),
        })
    byok_allowed = await registry.byok_allowed(db, user.id)
    ai_mode = await factory_pool.effective_ai_mode(db, user.id, user)
    # EXPERT (internal "custom") stays subscriber-gated; DEV is open to all.
    if ai_mode == factory_pool.MODE_EXPERT and not byok_allowed:
        ai_mode = factory_pool.MODE_ROOKIE
    return {
        "timezone": user.timezone,
        "autonomy_mode": user.autonomy_mode,
        "settings": user.settings_json,
        "kill_switches": switches,
        # a stored EXPERT preference is reported as ROOKIE while BYOK is locked
        # — routing does the same, and the preference is never erased
        "ai_mode": ai_mode,
        "byok_allowed": byok_allowed,
        "dev_key_connected": await factory_pool.dev_key_connected(db, user.id),
        "factory": factory,
    }


@router.patch("")
async def update_user_settings(body: UpdateSettingsRequest, user: CurrentUser, db: DB):
    if body.timezone is not None:
        user.timezone = body.timezone
    if body.autonomy_mode is not None:
        if body.autonomy_mode not in ("advisory", "assisted", "autonomous"):
            raise HTTPException(400, "invalid autonomy mode")
        user.autonomy_mode = body.autonomy_mode
    if body.settings is not None:
        if "ai_mode" in body.settings:
            # internal values unchanged: factory=ROOKIE, dev=DEV, custom=EXPERT
            if body.settings["ai_mode"] not in factory_pool.AI_MODES:
                raise HTTPException(400, "ai_mode must be 'factory', 'dev' or 'custom'")
            if (body.settings["ai_mode"] == factory_pool.MODE_EXPERT
                    and not await registry.byok_allowed(db, user.id)):
                raise HTTPException(402, registry.BYOK_REQUIRES_SUBSCRIPTION)
        user.settings_json = {**(user.settings_json or {}), **body.settings}
    await db.commit()
    return {"ok": True}


class KillSwitchRequest(BaseModel):
    switch: str
    on: bool


class EmergencyRequest(BaseModel):
    on: bool


@router.post("/emergency-stop")
async def emergency_stop(body: EmergencyRequest, user: CurrentUser, db: DB):
    """One red switch (owner directive §54): engages/releases every kill switch."""
    await killswitch.set_emergency_stop(db, user.id, body.on)
    await ledger.record(
        db, user.id,
        "system_emergency_stopped" if body.on else "system_resumed",
        actor_type="user", payload={"emergency": body.on},
    )
    await db.commit()
    switches = {s: await killswitch.is_on(db, user.id, s) for s in killswitch.ALL_SWITCHES}
    return {"emergency_stop": body.on, "kill_switches": switches}


@router.post("/kill-switch")
async def set_kill_switch(body: KillSwitchRequest, user: CurrentUser, db: DB):
    if body.switch not in killswitch.ALL_SWITCHES:
        raise HTTPException(400, f"unknown switch: {body.switch}")
    await killswitch.set_switch(db, user.id, body.switch, body.on)
    await ledger.record(
        db, user.id, "kill_switch_changed", actor_type="user",
        payload={"switch": body.switch, "on": body.on},
    )
    if body.switch == killswitch.PAUSE_ALL_AGENTS:
        await ledger.record(db, user.id, "system_paused" if body.on else "system_resumed", actor_type="user")
    await db.commit()
    return {"switch": body.switch, "on": body.on}
