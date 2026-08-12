"""Private EOD equity/ETF bars via yfinance.

SCOPE — read this before wiring it anywhere new:

  · PRIVATE, PER-USER, ASSISTANT MODE ONLY. This data reaches one authenticated
    user's own dashboard and their own agent context. It is never rendered on a
    public page and never redistributed.
  · The public Darvas Challenge does NOT use this module and must not. It runs
    on crypto from backend.challenge.data precisely because that surface is
    public. The dependency runs one way — marketdata imports challenge, never
    the reverse — and a test enforces it.

yfinance is an unofficial client for Yahoo endpoints, so treat it as a source
that can break or start refusing without warning. Everything here is therefore
FAIL QUIET: on any failure it raises MarketDataUnavailable and the caller says
"unavailable" rather than inventing or carrying forward a price.

The import is deliberately lazy: yfinance drags in pandas and numpy, and a
deployment without them should degrade to "equities unavailable" rather than
failing to boot.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from decimal import Decimal, InvalidOperation

from backend.challenge.darvas import Bar

logger = logging.getLogger("mychief.marketdata")

MAX_DAYS = 400
CACHE_TTL_SECONDS = 900        # 15 minutes: EOD data does not move intraday
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# symbol -> (fetched_at, bars). Process-local and small on purpose: this exists
# to keep a dashboard refresh from hitting Yahoo once per page view.
_cache: dict[tuple[str, int], tuple[float, list[Bar]]] = {}


class MarketDataUnavailable(Exception):
    """No bars. The caller reports that plainly; it never fabricates a series."""


def valid_symbol(symbol: str) -> bool:
    return bool(SYMBOL_RE.match(symbol.strip().upper()))


def _period_for(days: int) -> str:
    for limit, period in ((5, "5d"), (30, "1mo"), (90, "3mo"), (180, "6mo"),
                          (365, "1y")):
        if days <= limit:
            return period
    return "2y"


def _fetch_blocking(symbol: str, days: int) -> list[Bar]:
    """Runs off the event loop: yfinance is synchronous and does network I/O."""
    try:
        import yfinance  # noqa: PLC0415 — lazy on purpose, see the module docstring
    except ImportError as e:
        raise MarketDataUnavailable(
            "equity data is not installed on this deployment") from e

    try:
        frame = yfinance.Ticker(symbol).history(
            period=_period_for(days), interval="1d", auto_adjust=False,
            raise_errors=False)
    except Exception as e:  # noqa: BLE001 — an unofficial client fails creatively
        raise MarketDataUnavailable(f"{symbol}: {type(e).__name__}") from e

    if frame is None or frame.empty:
        raise MarketDataUnavailable(f"{symbol}: no bars returned")

    bars: list[Bar] = []
    for stamp, row in frame.iterrows():
        try:
            bar = Bar(date=stamp.date().isoformat(),
                      high=Decimal(str(round(float(row["High"]), 6))),
                      low=Decimal(str(round(float(row["Low"]), 6))),
                      close=Decimal(str(round(float(row["Close"]), 6))))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue                      # a broken row is dropped, never repaired
        if bar.high >= bar.low > 0 and bar.high >= bar.close >= bar.low:
            bars.append(bar)
    if not bars:
        raise MarketDataUnavailable(f"{symbol}: no usable bars")
    return bars[-days:]


async def fetch_daily(symbol: str, days: int = 90) -> list[Bar]:
    """Daily bars for one equity/ETF symbol. Raises MarketDataUnavailable."""
    symbol = symbol.strip().upper()
    if not valid_symbol(symbol):
        raise MarketDataUnavailable(f"{symbol!r} is not a valid symbol")
    days = max(5, min(days, MAX_DAYS))

    key = (symbol, days)
    hit = _cache.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]

    try:
        bars = await asyncio.to_thread(_fetch_blocking, symbol, days)
    except MarketDataUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — never let this take down a request
        logger.warning("yfinance failed for %s: %s", symbol, e)
        raise MarketDataUnavailable(f"{symbol}: {type(e).__name__}") from e

    _cache[key] = (time.monotonic(), bars)
    return bars


def clear_cache() -> None:
    _cache.clear()
