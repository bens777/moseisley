"""Plan entitlements (owner directive — final pricing).

Community (self-hosted, Stripe unconfigured) always has the full product.
When Stripe billing is configured (hosted deployment), the Basic plan covers
the core command center and the Pro plan additionally unlocks the autonomous /
full-crew functionality below. Enforcement reads Stripe-synced server state
only — browser-supplied entitlement is never trusted.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.billing import stripe_billing

# Functionality sold only on Pro ($19/mo) in hosted mode. Everything not listed
# here (orchestrator, chat, goals, memory, providers, ledger, emergency stop, …)
# is included in Basic.
# Crew roles that are sold as Pro capabilities. Anything that assigns a role —
# the agent wizard, a skill — gates on the same entry the role's own routes use.
ROLE_FEATURES: dict[str, str] = {
    "strategist": "strategist", "challenger": "challenger", "xray": "xray",
    "auditor": "auditor", "radar": "market_radar",
}

PRO_FEATURES = frozenset({
    "telegram",
    "xray",
    "market_radar",
    "strategist",
    "challenger",
    "auditor",
    "experiments",
    "treasury",
    "autopilot",
    "scheduled_autonomy",
})


def billing_enforced() -> bool:
    """Plan gating applies only where Stripe billing is configured (hosted)."""
    return stripe_billing._configured()


async def user_plan(db: AsyncSession, user_id: str) -> str:
    state = await stripe_billing.get_state(db, user_id)
    return stripe_billing.plan_for_state(state)


def plan_allows(plan: str, feature: str) -> bool:
    if not billing_enforced():
        return True
    if feature not in PRO_FEATURES:
        return True
    return plan == "pro"


async def check_feature(db: AsyncSession, user_id: str, feature: str) -> bool:
    if not billing_enforced() or feature not in PRO_FEATURES:
        return True
    return plan_allows(await user_plan(db, user_id), feature)


async def require_feature(db: AsyncSession, user_id: str, feature: str) -> None:
    """Raise 402 when a hosted user's plan does not include the feature."""
    if not await check_feature(db, user_id, feature):
        raise HTTPException(
            status_code=402,
            detail=f"'{feature}' requires the Pro plan ($19/month). "
                   "Upgrade in Settings → Billing.",
        )
