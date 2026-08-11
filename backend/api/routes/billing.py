from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.billing import stripe_billing
from backend.core.config import get_settings
from backend.core.models import User
from backend.core.security import DB, CurrentUser
from backend.friends import service as friends_svc
from backend.providers import factory_pool

router = APIRouter(prefix="/billing")


class CheckoutRequest(BaseModel):
    plan: str = "pro"  # "basic" | "pro"


class BarCheckoutRequest(BaseModel):
    sku: str
    friend_id: str | None = None  # public handle of the recipient (BUY A ROUND only)


@router.get("")
async def billing_state(user: CurrentUser, db: DB):
    state = await stripe_billing.get_state(db, user.id)
    await db.commit()
    return {
        "plan": stripe_billing.plan_for_state(state),
        "status": state.status,
        "cancel_at_period_end": state.cancel_at_period_end,
        "current_period_end": state.current_period_end,
        "configured": stripe_billing._configured(),
    }


@router.post("/checkout")
async def checkout(user: CurrentUser, db: DB, body: CheckoutRequest | None = None):
    plan = body.plan if body else "pro"
    if plan not in ("basic", "pro"):
        raise HTTPException(422, "plan must be 'basic' or 'pro'")
    try:
        url = await stripe_billing.create_checkout_session(db, user, plan=plan)
        await db.commit()
        return {"url": url}
    except stripe_billing.BillingError as e:
        raise HTTPException(424, str(e)) from e


@router.get("/bar/menu")
async def bar_menu(user: CurrentUser, db: DB):
    """The Bar: one-time fuel purchases. Never surfaced on public pages."""
    return {
        "open": stripe_billing.bar_open(),
        "items": stripe_billing.bar_menu(),
        "fuel_balance": factory_pool.get_fuel_balance(user),
        "closed_reason": None if stripe_billing.bar_open()
                         else "The bar is closed — no drinks are served on this deployment.",
    }


@router.post("/bar/checkout")
async def bar_checkout(body: BarCheckoutRequest, user: CurrentUser, db: DB):
    item = stripe_billing.BAR_BY_SKU.get(body.sku)
    if item is None:
        raise HTTPException(422, f"no such drink: {body.sku}")
    recipient: User | None = None
    recipient_name: str | None = None
    if item["gift"]:
        if not body.friend_id:
            raise HTTPException(422, "a round needs someone to drink it — pick a friend")
        profile = await friends_svc.get_profile_by_handle(db, body.friend_id)
        if profile is None or not profile.is_published or profile.moderation_status != "active":
            raise HTTPException(404, "no such table in the cantina")
        if profile.user_id == user.id:
            raise HTTPException(400, "that's just drinking alone — pick a friend")
        recipient = await db.get(User, profile.user_id)
        if recipient is None:
            raise HTTPException(404, "no such table in the cantina")
        recipient_name = profile.display_name
    elif body.friend_id:
        raise HTTPException(422, "only a round can be sent to a friend")
    try:
        url = await stripe_billing.create_bar_checkout_session(
            db, user, body.sku, recipient=recipient, recipient_name=recipient_name)
        await db.commit()
        return {"url": url}
    except stripe_billing.BillingError as e:
        raise HTTPException(424, str(e)) from e


@router.post("/portal")
async def portal(user: CurrentUser, db: DB):
    """Open the Stripe Customer Portal — cancel, change plan, update card."""
    try:
        url = await stripe_billing.create_portal_session(db, user)
        await db.commit()  # get_state() may have created the row
        return {"url": url}
    except stripe_billing.NoSubscriptionToManage as e:
        raise HTTPException(400, str(e)) from e
    except stripe_billing.BillingError as e:
        raise HTTPException(424, str(e)) from e


@router.post("/webhook")
async def webhook(request: Request, db: DB):
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(503, "stripe webhook not configured")
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    if not stripe_billing.verify_webhook_signature(payload, signature,
                                                   settings.stripe_webhook_secret):
        raise HTTPException(400, "invalid signature")
    import json

    event = json.loads(payload)
    result = await stripe_billing.handle_webhook_event(db, event)
    await db.commit()
    return {"received": True, "result": result}
