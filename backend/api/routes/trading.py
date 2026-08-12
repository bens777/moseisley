"""Trader Assistant API.

Two surfaces:
  · public_router — the inbound webhook. NO session: the URL token is the
    credential, so everything arriving is treated as untrusted.
  · router — the authenticated journal, settings and endpoint management.

No route here places an order. There is no broker in this feature.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.trading import service as trading

public_router = APIRouter(prefix="/webhooks/tradingview")
router = APIRouter(prefix="/trading")

MAX_BODY_BYTES = 8 * 1024


@public_router.post("/{token}")
async def inbound(token: str, request: Request, db: DB):
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "alert body too large")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — TradingView can be told to send plain text
        raise HTTPException(422, "the alert body must be JSON — use the message "
                                 "template from your Trading page") from None
    try:
        result = await trading.receive(db, token, payload)
    except trading.WebhookRejected as e:
        await db.rollback()
        raise HTTPException(e.status, e.detail) from e
    await db.commit()
    return result


@router.get("")
async def journal(user: CurrentUser, db: DB):
    return await trading.journal(db, user)


@router.post("/endpoint")
async def issue_endpoint(user: CurrentUser, db: DB):
    """Mint a new webhook token. Shown once — any previous one stops working."""
    token = await trading.issue_token(db, user)
    await db.commit()
    return {"token": token, "path": f"/api/webhooks/tradingview/{token}"}


@router.delete("/endpoint")
async def revoke_endpoint(user: CurrentUser, db: DB):
    revoked = await trading.revoke_tokens(db, user)
    await db.commit()
    return {"revoked": revoked}


class SettingsRequest(BaseModel):
    enabled: bool = False
    capital_cents: int = 0
    risk_pct: Decimal = Decimal("1")


@router.put("/settings")
async def save_settings(body: SettingsRequest, user: CurrentUser, db: DB):
    try:
        out = await trading.save_settings(db, user, enabled=body.enabled,
                                          capital_cents=body.capital_cents,
                                          risk_pct=body.risk_pct)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return out
