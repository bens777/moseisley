"""Daily candles for the challenge watchlist. Kraken first, Coinbase as fallback.

Crypto, not equities, and deliberately so: exchange market-data licensing makes
publishing equity prices on a public page a paid, contractual matter, while
these endpoints are public and unauthenticated. The Darvas method is pure price
action and does not care which series it reads.

FAIL QUIET: when a feed is unavailable this raises DataFeedDown. Nothing here
ever invents, interpolates or carries a bar forward — the challenge pauses and
says so instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from backend.challenge.darvas import Bar

logger = logging.getLogger("mychief.challenge")

KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"

TIMEOUT = 20.0
HISTORY_BARS = 365          # a year of daily boxes: plenty, and it bounds the work


class DataFeedDown(Exception):
    """No source could supply this series. The caller must pause, not guess."""


@dataclass(frozen=True)
class Instrument:
    symbol: str             # what we call it everywhere in the product
    name: str
    kraken_pair: str        # what we ASK Kraken for; it answers with its own key
    coinbase_product: str


# Ten liquid USD pairs. Verified 2026-08-12: all ten return 721 daily bars from
# Kraken. Constant in code on purpose — no user-supplied tickers, no surprises.
WATCHLIST: tuple[Instrument, ...] = (
    Instrument("BTC", "Bitcoin", "XBTUSD", "BTC-USD"),
    Instrument("ETH", "Ethereum", "ETHUSD", "ETH-USD"),
    Instrument("SOL", "Solana", "SOLUSD", "SOL-USD"),
    Instrument("ADA", "Cardano", "ADAUSD", "ADA-USD"),
    Instrument("XRP", "XRP", "XRPUSD", "XRP-USD"),
    Instrument("DOT", "Polkadot", "DOTUSD", "DOT-USD"),
    Instrument("LINK", "Chainlink", "LINKUSD", "LINK-USD"),
    Instrument("AVAX", "Avalanche", "AVAXUSD", "AVAX-USD"),
    Instrument("LTC", "Litecoin", "LTCUSD", "LTC-USD"),
    Instrument("ATOM", "Cosmos", "ATOMUSD", "ATOM-USD"),
)

BY_SYMBOL: dict[str, Instrument] = {i.symbol: i for i in WATCHLIST}


def _day(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(int(epoch_seconds), tz=UTC).strftime("%Y-%m-%d")


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _sane(bar: Bar) -> bool:
    """A bar that contradicts itself is corrupt input, not data."""
    return (bar.high >= bar.low > 0 and bar.high >= bar.close >= bar.low)


def parse_kraken(payload: dict) -> list[Bar]:
    """Kraken: result[<its own key>] = [[time, o, h, l, c, vwap, vol, count], …]."""
    if payload.get("error"):
        raise DataFeedDown(f"kraken error: {payload['error']}")
    result = payload.get("result") or {}
    keys = [k for k in result if k != "last"]
    if not keys:
        raise DataFeedDown("kraken returned no series")
    rows = result[keys[0]]
    bars: list[Bar] = []
    for row in rows:
        try:
            bar = Bar(date=_day(row[0]), high=_dec(row[2]), low=_dec(row[3]),
                      close=_dec(row[4]))
        except (IndexError, TypeError, InvalidOperation) as e:
            raise DataFeedDown(f"kraken returned an unreadable row: {type(e).__name__}") from e
        if _sane(bar):
            bars.append(bar)
    if not bars:
        raise DataFeedDown("kraken returned no usable bars")
    return bars


def parse_coinbase(payload: list) -> list[Bar]:
    """Coinbase: [[time, low, high, open, close, volume], …], newest first."""
    if not isinstance(payload, list) or not payload:
        raise DataFeedDown("coinbase returned no series")
    bars: list[Bar] = []
    for row in payload:
        try:
            bar = Bar(date=_day(row[0]), high=_dec(row[2]), low=_dec(row[1]),
                      close=_dec(row[4]))
        except (IndexError, TypeError, InvalidOperation) as e:
            raise DataFeedDown(f"coinbase returned an unreadable row: {type(e).__name__}") from e
        if _sane(bar):
            bars.append(bar)
    if not bars:
        raise DataFeedDown("coinbase returned no usable bars")
    return sorted(bars, key=lambda b: b.date)


async def fetch_series(client: httpx.AsyncClient, instrument: Instrument) -> tuple[list[Bar], str]:
    """(bars, source). Tries Kraken, then Coinbase. Raises DataFeedDown if both fail."""
    errors: list[str] = []
    try:
        resp = await client.get(KRAKEN_URL,
                                params={"pair": instrument.kraken_pair, "interval": 1440})
        resp.raise_for_status()
        bars = parse_kraken(resp.json())
        return bars[-HISTORY_BARS:], "kraken"
    except (httpx.HTTPError, DataFeedDown, ValueError) as e:
        errors.append(f"kraken: {type(e).__name__}")
        logger.warning("kraken failed for %s: %s", instrument.symbol, e)

    try:
        resp = await client.get(COINBASE_URL.format(product=instrument.coinbase_product),
                                params={"granularity": 86400},
                                headers={"User-Agent": "moseisley.sh/darvas-challenge"})
        resp.raise_for_status()
        bars = parse_coinbase(resp.json())
        return bars[-HISTORY_BARS:], "coinbase"
    except (httpx.HTTPError, DataFeedDown, ValueError) as e:
        errors.append(f"coinbase: {type(e).__name__}")
        logger.warning("coinbase failed for %s: %s", instrument.symbol, e)

    raise DataFeedDown("; ".join(errors))


async def fetch_all() -> tuple[dict[str, list[Bar]], list[str], dict[str, str]]:
    """(series by symbol, symbols that failed, source used per symbol)."""
    series: dict[str, list[Bar]] = {}
    unavailable: list[str] = []
    sources: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for instrument in WATCHLIST:
            try:
                bars, source = await fetch_series(client, instrument)
            except DataFeedDown:
                unavailable.append(instrument.symbol)
                continue
            series[instrument.symbol] = bars
            sources[instrument.symbol] = source
    return series, unavailable, sources
