"""Private per-user market data: one entry point, two sources.

  crypto  → backend.challenge.data (Kraken, Coinbase fallback) — reused, not
            reimplemented. The same code that powers the public challenge.
  equity  → backend.marketdata.yahoo (yfinance) — PRIVATE ONLY.

PRIVACY BOUNDARY. Everything this module returns is for one authenticated
user's own dashboard and their own assistant. It is never rendered publicly and
never redistributed. The public challenge reads backend.challenge.data
directly and never comes through here — enforced by a test.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx

from backend.challenge import data as crypto_feed
from backend.challenge.darvas import Bar
from backend.marketdata import yahoo

logger = logging.getLogger("mychief.marketdata")

INTERNAL_NOTICE = ("Private to your account. Delayed end-of-day data for your own "
                   "dashboard and your crew — not redistributed, not shown publicly.")

CRYPTO_SYMBOLS: frozenset[str] = frozenset(crypto_feed.BY_SYMBOL)


class MarketDataUnavailable(Exception):
    """Neither source could supply this series."""


@dataclass(frozen=True)
class Series:
    symbol: str
    asset_class: str          # crypto | equity
    source: str               # kraken | coinbase | yahoo
    bars: list[Bar]

    @property
    def last_close(self) -> Decimal | None:
        return self.bars[-1].close if self.bars else None


def classify(symbol: str) -> str:
    """Crypto if it is one of the pairs we already track, otherwise an equity
    ticker. Explicit set beats guessing at suffixes."""
    return "crypto" if symbol.strip().upper() in CRYPTO_SYMBOLS else "equity"


async def fetch_daily(symbol: str, days: int = 90) -> Series:
    """Daily bars for any supported symbol. Raises MarketDataUnavailable."""
    symbol = symbol.strip().upper()
    asset_class = classify(symbol)

    if asset_class == "crypto":
        instrument = crypto_feed.BY_SYMBOL[symbol]
        try:
            async with httpx.AsyncClient(timeout=crypto_feed.TIMEOUT) as client:
                bars, source = await crypto_feed.fetch_series(client, instrument)
        except crypto_feed.DataFeedDown as e:
            raise MarketDataUnavailable(str(e)) from e
        return Series(symbol=symbol, asset_class="crypto", source=source,
                      bars=bars[-days:])

    try:
        bars = await yahoo.fetch_daily(symbol, days=days)
    except yahoo.MarketDataUnavailable as e:
        raise MarketDataUnavailable(str(e)) from e
    return Series(symbol=symbol, asset_class="equity", source="yahoo", bars=bars)


def serialize(series: Series, *, limit: int = 120) -> dict:
    bars = series.bars[-limit:]
    return {
        "symbol": series.symbol,
        "asset_class": series.asset_class,
        "source": series.source,
        "internal_only": True,
        "notice": INTERNAL_NOTICE,
        "bars": [{"date": b.date, "high": str(b.high), "low": str(b.low),
                  "close": str(b.close)} for b in bars],
        "last_close": str(series.last_close) if series.last_close is not None else None,
        "as_of": bars[-1].date if bars else None,
    }


async def fetch_many(symbols: list[str], days: int = 90) -> dict:
    """Several symbols for a dashboard. Per-symbol failures are reported, not
    raised — one dead ticker must not blank the whole panel."""
    out: list[dict] = []
    unavailable: list[dict] = []
    for raw in symbols[:20]:
        symbol = raw.strip().upper()
        if not symbol:
            continue
        try:
            out.append(serialize(await fetch_daily(symbol, days=days)))
        except MarketDataUnavailable as e:
            unavailable.append({"symbol": symbol, "reason": str(e)})
    return {"series": out, "unavailable": unavailable, "internal_only": True,
            "notice": INTERNAL_NOTICE}
