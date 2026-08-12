"""Private market data — AUTHENTICATED ONLY.

Every route here requires a session and serves that user's own dashboard. This
router is deliberately NOT a public_router: the public surface of this product
(the Darvas Challenge) reads crypto directly from backend.challenge.data and
never touches equity data.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.core.security import CurrentUser
from backend.marketdata import service as marketdata

router = APIRouter(prefix="/marketdata")


@router.get("")
async def many(user: CurrentUser, symbols: str = Query(..., min_length=1),
               days: int = Query(default=90, ge=5, le=400)):
    """Comma-separated symbols for the user's own dashboard."""
    return await marketdata.fetch_many([s for s in symbols.split(",")], days=days)


@router.get("/{symbol}")
async def one(symbol: str, user: CurrentUser,
              days: int = Query(default=90, ge=5, le=400)):
    try:
        series = await marketdata.fetch_daily(symbol, days=days)
    except marketdata.MarketDataUnavailable as e:
        # fail quiet: say it is unavailable, never synthesize a price
        raise HTTPException(503, str(e)) from e
    return marketdata.serialize(series)
