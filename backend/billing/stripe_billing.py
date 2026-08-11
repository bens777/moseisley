"""Stripe Billing for Cloud Premium (owner directive §51-53).

Server-side subscription state synchronized from Stripe is authoritative; browser-sent
entitlement is never trusted. Webhook signatures are verified (HMAC-SHA256 per Stripe's
signing scheme). Uses the Stripe REST API directly over httpx; test mode first.
External blocker for live billing: a real Stripe account + price IDs.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from functools import lru_cache

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.models import Event, SubscriptionState, User
from backend.friends import service as friends_svc
from backend.ledger import service as ledger
from backend.providers import factory_pool

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 300

ENTITLED_STATUSES = ("active", "trialing")

# ---------------------------------------------------------------------------
# The Bar — one-time drink purchases that top up factory fuel. Strictly
# separate from subscriptions: mode="payment" Checkout, no recurring state,
# never advertised on the public pricing page. Menu is a code constant.
# ---------------------------------------------------------------------------

BAR_MENU: list[dict] = [
    {"sku": "nebula_ale", "name": "Nebula Ale", "price_usd": 2, "fuel": 50, "gift": False,
     "tagline": "A cold one for the road. +50 requests for your crew."},
    {"sku": "purple_tentacle_punch", "name": "Purple Tentacle Punch", "price_usd": 5,
     "fuel": 150, "gift": False,
     "tagline": "The house special. +150 requests, served with a tentacle."},
    {"sku": "buy_a_round", "name": "Buy a Round", "price_usd": 5, "fuel": 100, "gift": True,
     "tagline": "Send +100 requests to a friend in the cantina. They get the fuel, you get the credit."},
]
BAR_BY_SKU = {item["sku"]: item for item in BAR_MENU}

# Ledger bookkeeping for bar orders. `entity_id` holds the Stripe Checkout
# session id, which makes webhook credits idempotent.
BAR_ORDER_ENTITY = "bar_order"


class _BarPriceSettings(BaseSettings):
    """One-time Stripe price ids for the Bar SKUs. Declared here (same .env
    loading semantics as core Settings) because backend/core/config.py is
    outside this change's scope. Unset = the bar is closed."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    stripe_price_id_ale: str | None = None
    stripe_price_id_punch: str | None = None
    stripe_price_id_round: str | None = None


@lru_cache
def bar_settings() -> _BarPriceSettings:
    return _BarPriceSettings()


def _bar_price_ids() -> dict[str, str | None]:
    s = bar_settings()
    return {
        "nebula_ale": s.stripe_price_id_ale,
        "purple_tentacle_punch": s.stripe_price_id_punch,
        "buy_a_round": s.stripe_price_id_round,
    }


def bar_open() -> bool:
    """The bar serves only when Stripe is configured AND every drink has a
    price id. Otherwise the menu renders greyed out — nothing crashes."""
    return _configured() and all(_bar_price_ids().values())


def bar_menu() -> list[dict]:
    prices = _bar_price_ids()
    return [{**item, "available": bool(prices.get(item["sku"]))} for item in BAR_MENU]


class BillingError(Exception):
    pass


class NoSubscriptionToManage(BillingError):
    """The user has no Stripe billing history at all — there is no customer
    record to open a portal for. Surfaced as 400 (bad request for this user),
    distinct from 424 (the deployment has no Stripe configured)."""


class _PortalSettings(BaseSettings):
    """Where Stripe returns the user after the Customer Portal. Declared here
    (same .env loading as core Settings) because backend/core/config.py is
    outside this change's scope. Unset → the frontend origin's /settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    stripe_portal_return_url: str | None = None


@lru_cache
def portal_settings() -> _PortalSettings:
    return _PortalSettings()


def _price_ids() -> dict[str, str | None]:
    """Configured Stripe price ids per plan. Legacy STRIPE_PRICE_ID acts as Pro."""
    s = get_settings()
    return {
        "basic": s.stripe_price_id_basic,
        "pro": s.stripe_price_id_pro or s.stripe_price_id,
    }


def _configured() -> bool:
    s = get_settings()
    return bool(s.stripe_api_key) and any(_price_ids().values())


async def _stripe_post(path: str, data: dict) -> dict:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{STRIPE_API}{path}", auth=(s.stripe_api_key, ""), data=data)
    if resp.status_code >= 300:
        raise BillingError(f"stripe returned {resp.status_code}")
    return resp.json()


async def get_state(db: AsyncSession, user_id: str) -> SubscriptionState:
    row = (await db.execute(select(SubscriptionState).where(
        SubscriptionState.user_id == user_id
    ))).scalar_one_or_none()
    if row is None:
        row = SubscriptionState(user_id=user_id)
        db.add(row)
        await db.flush()
    return row


def is_entitled(state: SubscriptionState) -> bool:
    return state.status in ENTITLED_STATUSES


def plan_for_state(state: SubscriptionState) -> str:
    """Derive the plan ('community' | 'basic' | 'pro') from Stripe-synced state.

    The synced price id is authoritative. An entitled subscription on an
    unrecognized price maps to Basic (least privilege while still entitled).
    """
    if not is_entitled(state):
        return "community"
    prices = _price_ids()
    if state.price_id and state.price_id == prices["pro"]:
        return "pro"
    if state.price_id and state.price_id == prices["basic"]:
        return "basic"
    # Legacy subscriptions created before the basic/pro split were sold as the
    # full product; grant Pro only if no basic price is configured at all.
    if prices["basic"] is None:
        return "pro"
    return "basic"


async def _ensure_customer(db: AsyncSession, user: User, state: SubscriptionState) -> str:
    if state.stripe_customer_id:
        return state.stripe_customer_id
    customer = await _stripe_post("/customers", {
        "email": user.email, "metadata[moseisley_user_id]": user.id,
    })
    state.stripe_customer_id = customer["id"]
    await db.flush()
    return state.stripe_customer_id


async def create_checkout_session(db: AsyncSession, user: User, plan: str = "pro") -> str:
    if not _configured():
        raise BillingError("Stripe billing is not configured (STRIPE_API_KEY / price ids)")
    price_id = _price_ids().get(plan)
    if not price_id:
        raise BillingError(f"no Stripe price configured for plan '{plan}'")
    s = get_settings()
    state = await get_state(db, user.id)
    customer_id = await _ensure_customer(db, user, state)
    origin = s.frontend_origin.rstrip("/")
    session = await _stripe_post("/checkout/sessions", {
        "mode": "subscription",
        "customer": customer_id,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": f"{origin}/settings?billing=success",
        "cancel_url": f"{origin}/settings?billing=canceled",
        "metadata[moseisley_user_id]": user.id,
    })
    return session["url"]


async def create_portal_session(db: AsyncSession, user: User) -> str:
    """Stripe Customer Portal session — the ONLY place users cancel, change
    plan or update payment details. Requires the portal to be enabled in the
    Stripe dashboard (Settings → Billing → Customer portal)."""
    if not _configured():
        raise BillingError("Stripe billing is not configured")
    state = await get_state(db, user.id)
    if not state.stripe_customer_id:
        raise NoSubscriptionToManage("no subscription to manage on this account")
    session = await _stripe_post("/billing_portal/sessions", {
        "customer": state.stripe_customer_id,
        "return_url": portal_return_url(),
    })
    return session["url"]


def portal_return_url() -> str:
    """STRIPE_PORTAL_RETURN_URL, or the frontend origin's settings page."""
    configured = (portal_settings().stripe_portal_return_url or "").strip()
    if configured:
        return configured
    return f"{get_settings().frontend_origin.rstrip('/')}/settings"


async def create_bar_checkout_session(db: AsyncSession, user: User, sku: str,
                                      *, recipient: User | None = None,
                                      recipient_name: str | None = None) -> str:
    """One-time (mode="payment") Checkout for a drink. Fuel is credited ONLY
    by the verified webhook — never from the browser redirect."""
    item = BAR_BY_SKU.get(sku)
    if item is None:
        raise BillingError(f"no such drink: {sku}")
    if not _configured():
        raise BillingError("Stripe billing is not configured (STRIPE_API_KEY / price ids)")
    price_id = _bar_price_ids().get(sku)
    if not price_id:
        raise BillingError("the bar is closed (no Stripe price configured for this drink)")
    state = await get_state(db, user.id)
    customer_id = await _ensure_customer(db, user, state)
    origin = get_settings().frontend_origin.rstrip("/")
    metadata = {
        "metadata[moseisley_user_id]": user.id,
        "metadata[bar_sku]": sku,
        "metadata[bar_fuel]": str(item["fuel"]),
    }
    if item["gift"]:
        if recipient is None:
            raise BillingError("a round needs someone to drink it — pick a friend")
        metadata["metadata[bar_gift_to]"] = recipient.id
        metadata["metadata[bar_gift_to_name]"] = (recipient_name or "")[:80]
    session = await _stripe_post("/checkout/sessions", {
        "mode": "payment",
        "customer": customer_id,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": f"{origin}/bar?purchase=success",
        "cancel_url": f"{origin}/bar?purchase=canceled",
        **metadata,
    })
    return session["url"]


async def _bar_order_processed(db: AsyncSession, session_id: str) -> bool:
    """Idempotency: the Checkout session id is stored on the ledger event."""
    return (await db.execute(
        select(Event.id).where(Event.entity_type == BAR_ORDER_ENTITY,
                               Event.entity_id == session_id).limit(1)
    )).scalar_one_or_none() is not None


def _bar_event(db: AsyncSession, user_id: str, event_type: str, session_id: str,
               payload: dict) -> None:
    """Append-only bar bookkeeping. Written directly as an Event row because
    the ledger's EVENT_TYPES whitelist lives outside this change's scope; the
    row shape is identical to ledger.record()'s."""
    db.add(Event(user_id=user_id, event_type=event_type, actor_type="system",
                 entity_type=BAR_ORDER_ENTITY, entity_id=session_id,
                 payload_json=payload))


async def _credit_bar_purchase(db: AsyncSession, session: dict) -> str:
    """Credit fuel for a paid one-time Checkout session. Idempotent."""
    session_id = session.get("id")
    meta = session.get("metadata") or {}
    sku = meta.get("bar_sku")
    buyer_id = meta.get("moseisley_user_id")
    item = BAR_BY_SKU.get(sku or "")
    if not session_id or item is None or not buyer_id:
        return "ignored"
    if session.get("payment_status") not in (None, "paid", "no_payment_required"):
        return "unpaid"
    if await _bar_order_processed(db, session_id):
        return "duplicate"  # replayed webhook — never credit twice
    buyer = await db.get(User, buyer_id)
    if buyer is None:
        return "ignored"
    fuel = int(item["fuel"])

    if item["gift"]:
        recipient = await db.get(User, meta.get("bar_gift_to") or "")
        if recipient is None:
            return "ignored"
        from_name = meta.get("bar_gift_to_name") or ""
        buyer_name = await _display_name(db, buyer)
        factory_pool.credit_fuel(recipient, fuel)
        factory_pool.mark_gift_received(recipient, from_name=buyer_name, fuel=fuel)
        _bar_event(db, buyer.id, "bar_gift_sent", session_id,
                   {"sku": sku, "fuel": fuel, "to": from_name or recipient.id})
        _bar_event(db, recipient.id, "bar_gift_received", session_id,
                   {"sku": sku, "fuel": fuel, "from": buyer_name})
        await db.flush()
        return "gift_credited"

    balance = factory_pool.credit_fuel(buyer, fuel)
    _bar_event(db, buyer.id, "bar_purchase", session_id,
               {"sku": sku, "fuel": fuel, "balance": balance})
    await db.flush()
    return "credited"


async def _display_name(db: AsyncSession, user: User) -> str:
    """Public display name if the buyer published a Friends profile, else a
    neutral label — a purchase must never leak a private identity."""
    profile = await friends_svc.get_profile(db, user.id)
    if profile is not None and profile.is_published and profile.moderation_status == "active":
        return profile.display_name
    return "Someone"


def verify_webhook_signature(payload: bytes, sig_header: str, secret: str,
                             *, now: int | None = None) -> bool:
    """Stripe webhook signature scheme: v1 = HMAC-SHA256(f"{t}.{payload}", secret)."""
    try:
        parts = dict(kv.split("=", 1) for kv in sig_header.split(","))
        timestamp = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs((now if now is not None else int(time.time())) - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    provided = [v for k, v in (kv.split("=", 1) for kv in sig_header.split(",")) if k == "v1"]
    return any(hmac.compare_digest(expected, p) for p in provided)


async def _sync_subscription(db: AsyncSession, sub: dict) -> None:
    """Apply a Stripe subscription object to local state (authoritative sync)."""
    customer_id = sub.get("customer")
    state = (await db.execute(select(SubscriptionState).where(
        SubscriptionState.stripe_customer_id == customer_id
    ))).scalar_one_or_none()
    if state is None:
        user_id = (sub.get("metadata") or {}).get("moseisley_user_id")
        if not user_id:
            return  # unknown customer — nothing to map to
        state = await get_state(db, user_id)
        state.stripe_customer_id = customer_id
    old_status = state.status
    state.stripe_subscription_id = sub.get("id")
    state.status = sub.get("status", "none")
    state.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    items = (sub.get("items") or {}).get("data") or []
    if items:
        state.price_id = (items[0].get("price") or {}).get("id")
    period_end = sub.get("current_period_end")
    if period_end:
        from datetime import UTC, datetime

        state.current_period_end = datetime.fromtimestamp(int(period_end), tz=UTC)
    if sub.get("status") == "canceled" or sub.get("canceled_at"):
        state.status = "canceled"
    await db.flush()
    if state.status != old_status:
        await ledger.record(db, state.user_id, "subscription_changed", actor_type="system",
                            entity_type="subscription", entity_id=state.id,
                            payload={"from": old_status, "to": state.status})


async def handle_webhook_event(db: AsyncSession, event: dict) -> str:
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if etype in ("customer.subscription.created", "customer.subscription.updated",
                 "customer.subscription.deleted"):
        if etype.endswith("deleted"):
            obj = {**obj, "status": "canceled"}
        await _sync_subscription(db, obj)
        return "synced"
    if etype == "checkout.session.completed" and obj.get("mode") == "payment":
        return await _credit_bar_purchase(db, obj)  # The Bar: one-time fuel
    if etype == "checkout.session.completed" and obj.get("mode") == "subscription":
        # fetch the subscription for authoritative state
        sub_id = obj.get("subscription")
        if sub_id:
            s = get_settings()
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"{STRIPE_API}/subscriptions/{sub_id}",
                                        auth=(s.stripe_api_key, ""))
            if resp.status_code == 200:
                sub = resp.json()
                sub.setdefault("metadata", {}).setdefault(
                    "moseisley_user_id", (obj.get("metadata") or {}).get("moseisley_user_id"))
                await _sync_subscription(db, sub)
                return "synced"
        return "ignored"
    return "ignored"
