"""The fictional portfolio: replays the Darvas signals and books the results.

FICTIONAL MONEY. There is no broker, no order, no account, no real funds
anywhere in this module or anywhere it touches.

The simulation is a pure function of (series, rules), so each run replays the
whole history and writes any decision it has not already recorded. Same input,
same log — and if the process dies mid-run, the next run reproduces exactly the
same state instead of half of it.

Money is integer cents end to end; unit sizes are Decimal quantized to 8 places
and rounded DOWN, so the portfolio can never spend cash it does not have.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.challenge import data as feed
from backend.challenge.darvas import Action, Bar, DarvasTracker, Signal
from backend.core.models import ChallengeDecision, ChallengeSnapshot

logger = logging.getLogger("mychief.challenge")

START_CENTS = 1_000_000            # $10,000.00 of fictional money
MAX_POSITIONS = 5
POSITION_PCT = Decimal("0.20")     # 20% of equity per new position
MIN_TRADE_CENTS = 10_000           # don't open a $12 position
UNIT_PLACES = Decimal("0.00000001")

# Below this many usable series, the run is PAUSED rather than simulated on a
# partial universe. We never fill the gap with invented data.
MIN_SERIES = 8

LEGAL_NOTICE = ("Simulated portfolio. Fictional money. Educational demonstration "
                "— not investment advice or a solicitation.")


def _cents(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class Position:
    symbol: str
    units: Decimal
    entry_price: Decimal
    entry_date: str
    cost_cents: int
    stop: Decimal


@dataclass
class Portfolio:
    cash_cents: int = START_CENTS
    positions: dict[str, Position] = field(default_factory=dict)

    def market_value_cents(self, prices: dict[str, Decimal]) -> int:
        total = 0
        for symbol, pos in self.positions.items():
            price = prices.get(symbol)
            if price is not None:
                total += _cents(pos.units * price * 100)
        return total

    def equity_cents(self, prices: dict[str, Decimal]) -> int:
        return self.cash_cents + self.market_value_cents(prices)


@dataclass
class BookedDecision:
    trade_date: str
    symbol: str
    action: str
    reason: str
    price: Decimal
    units: Decimal
    box_top: Decimal
    box_bottom: Decimal
    stop: Decimal
    cash_cents_after: int
    equity_cents_after: int
    realized_pnl_cents: int | None = None


@dataclass
class DaySnapshot:
    trade_date: str
    equity_cents: int
    cash_cents: int
    positions: list[dict]


@dataclass
class SimulationResult:
    decisions: list[BookedDecision]
    snapshots: list[DaySnapshot]


def simulate(series: dict[str, list[Bar]]) -> SimulationResult:
    """Replay every series day by day. Deterministic and side-effect free."""
    portfolio = Portfolio()
    trackers = {symbol: DarvasTracker(symbol) for symbol in series}
    bars_by_date: dict[str, dict[str, Bar]] = {}
    for symbol, bars in series.items():
        for bar in bars:
            bars_by_date.setdefault(bar.date, {})[symbol] = bar

    decisions: list[BookedDecision] = []
    snapshots: list[DaySnapshot] = []
    last_price: dict[str, Decimal] = {}

    for date in sorted(bars_by_date):
        day = bars_by_date[date]
        # symbols in a fixed order so the same data always books the same way
        for symbol in sorted(day):
            bar = day[symbol]
            last_price[symbol] = bar.close
            for signal in trackers[symbol].feed(bar):
                booked = _book(portfolio, symbol, signal, last_price)
                if booked is not None:
                    decisions.append(booked)
        snapshots.append(DaySnapshot(
            trade_date=date,
            equity_cents=portfolio.equity_cents(last_price),
            cash_cents=portfolio.cash_cents,
            positions=[_position_row(p, last_price.get(p.symbol))
                       for p in portfolio.positions.values()],
        ))
    return SimulationResult(decisions=decisions, snapshots=snapshots)


def _position_row(pos: Position, price: Decimal | None) -> dict:
    value_cents = _cents(pos.units * price * 100) if price is not None else pos.cost_cents
    return {
        "symbol": pos.symbol,
        "units": str(pos.units),
        "entry_price": str(pos.entry_price),
        "entry_date": pos.entry_date,
        "stop": str(pos.stop),
        "last_price": str(price) if price is not None else None,
        "cost_cents": pos.cost_cents,
        "value_cents": value_cents,
        "unrealized_pnl_cents": value_cents - pos.cost_cents,
    }


def _book(portfolio: Portfolio, symbol: str, signal: Signal,
          prices: dict[str, Decimal]) -> BookedDecision | None:
    if signal.action is Action.BUY:
        if symbol in portfolio.positions or len(portfolio.positions) >= MAX_POSITIONS:
            return None
        equity = portfolio.equity_cents(prices)
        budget = min(_cents(Decimal(equity) * POSITION_PCT), portfolio.cash_cents)
        if budget < MIN_TRADE_CENTS or signal.price <= 0:
            return None
        units = (Decimal(budget) / 100 / signal.price).quantize(UNIT_PLACES,
                                                                rounding=ROUND_DOWN)
        if units <= 0:
            return None
        cost = _cents(units * signal.price * 100)
        if cost > portfolio.cash_cents:          # never overdraw, even by a cent
            return None
        portfolio.cash_cents -= cost
        portfolio.positions[symbol] = Position(
            symbol=symbol, units=units, entry_price=signal.price,
            entry_date=signal.date, cost_cents=cost, stop=signal.stop)
        return BookedDecision(
            trade_date=signal.date, symbol=symbol, action="buy", reason=signal.reason,
            price=signal.price, units=units, box_top=signal.box_top,
            box_bottom=signal.box_bottom, stop=signal.stop,
            cash_cents_after=portfolio.cash_cents,
            equity_cents_after=portfolio.equity_cents(prices))

    if signal.action is Action.SELL:
        pos = portfolio.positions.pop(symbol, None)
        if pos is None:
            return None
        proceeds = _cents(pos.units * signal.price * 100)
        portfolio.cash_cents += proceeds
        return BookedDecision(
            trade_date=signal.date, symbol=symbol, action="sell", reason=signal.reason,
            price=signal.price, units=pos.units, box_top=signal.box_top,
            box_bottom=signal.box_bottom, stop=signal.stop,
            cash_cents_after=portfolio.cash_cents,
            equity_cents_after=portfolio.equity_cents(prices),
            realized_pnl_cents=proceeds - pos.cost_cents)

    if signal.action is Action.TRAIL:
        pos = portfolio.positions.get(symbol)
        if pos is None:
            return None
        pos.stop = signal.stop
        return BookedDecision(
            trade_date=signal.date, symbol=symbol, action="trail", reason=signal.reason,
            price=signal.price, units=pos.units, box_top=signal.box_top,
            box_bottom=signal.box_bottom, stop=signal.stop,
            cash_cents_after=portfolio.cash_cents,
            equity_cents_after=portfolio.equity_cents(prices))
    return None


# ── persistence ─────────────────────────────────────────────────────

async def _existing_keys(db: AsyncSession) -> set[tuple[str, str, str]]:
    rows = (await db.execute(select(ChallengeDecision.trade_date, ChallengeDecision.symbol,
                                    ChallengeDecision.action))).all()
    return {(r[0], r[1], r[2]) for r in rows}


async def persist(db: AsyncSession, result: SimulationResult, *,
                  note: str = "") -> dict:
    """Write decisions we have not recorded yet, and upsert every day's mark."""
    existing = await _existing_keys(db)
    written = 0
    for d in result.decisions:
        if (d.trade_date, d.symbol, d.action) in existing:
            continue
        db.add(ChallengeDecision(
            trade_date=d.trade_date, symbol=d.symbol, action=d.action, reason=d.reason,
            price=str(d.price), units=str(d.units), box_top=str(d.box_top),
            box_bottom=str(d.box_bottom), stop=str(d.stop),
            cash_cents_after=d.cash_cents_after, equity_cents_after=d.equity_cents_after,
            realized_pnl_cents=d.realized_pnl_cents))
        written += 1

    known = {row.trade_date: row for row in (await db.execute(
        select(ChallengeSnapshot))).scalars()}
    for snap in result.snapshots:
        row = known.get(snap.trade_date)
        if row is None:
            db.add(ChallengeSnapshot(
                trade_date=snap.trade_date, status="running",
                equity_cents=snap.equity_cents, cash_cents=snap.cash_cents,
                positions_json=snap.positions, note=note))
        else:
            row.status = "running"
            row.equity_cents = snap.equity_cents
            row.cash_cents = snap.cash_cents
            row.positions_json = snap.positions
            row.note = note
    await db.flush()
    return {"decisions_written": written, "days": len(result.snapshots)}


async def pause(db: AsyncSession, note: str) -> dict:
    """Record that the feed was down. No decisions, no invented prices."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    row = (await db.execute(select(ChallengeSnapshot).where(
        ChallengeSnapshot.trade_date == today))).scalar_one_or_none()
    latest = (await db.execute(select(ChallengeSnapshot).where(
        ChallengeSnapshot.status == "running"
    ).order_by(ChallengeSnapshot.trade_date.desc()).limit(1))).scalars().first()
    equity = latest.equity_cents if latest else START_CENTS
    cash = latest.cash_cents if latest else START_CENTS
    positions = latest.positions_json if latest else []
    if row is None:
        db.add(ChallengeSnapshot(trade_date=today, status="paused", equity_cents=equity,
                                 cash_cents=cash, positions_json=positions, note=note))
    else:
        row.status = "paused"
        row.note = note
    await db.flush()
    logger.warning("darvas challenge paused: %s", note)
    return {"status": "paused", "note": note}


async def run_challenge(db: AsyncSession) -> dict:
    """One daily run. Never raises on feed problems — it pauses instead."""
    try:
        series, unavailable, sources = await feed.fetch_all()
    except Exception as e:  # noqa: BLE001 — a feed problem is a pause, not a crash
        return await pause(db, f"data feed down ({type(e).__name__})")

    if len(series) < MIN_SERIES:
        return await pause(
            db, "data feed down — only "
                f"{len(series)} of {len(feed.WATCHLIST)} series available "
                f"({', '.join(unavailable) or 'none'} missing)")

    note = ""
    if unavailable:
        note = (f"{', '.join(unavailable)} unavailable from both venues; "
                "simulated on the rest")
    result = simulate(series)
    outcome = await persist(db, result, note=note)
    return {"status": "running", "sources": sorted(set(sources.values())),
            "unavailable": unavailable, **outcome}
