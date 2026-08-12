"""Position sizing. Deterministic arithmetic, no model, no guessing.

The user declares two numbers: the capital they are willing to trade with, and
the percentage of it they are willing to lose on one trade. Everything below
follows from those and the signal.

TWO CASES, and the difference matters enough to say out loud in the message:

  · STOP GIVEN — the professional case. Risk is the distance to the stop, so
        shares = (capital × risk%) ÷ |entry − stop|
    If the trade goes to the stop, the loss is the declared risk budget. This is
    the sizing a Darvas-style rule actually calls for.

  · NO STOP — risk cannot be bounded, so we do not pretend it can. The whole
    position is treated as the money at risk:
        shares = (capital × risk%) ÷ entry
    A total loss then costs exactly the risk budget. It is deliberately small,
    and the assistant says why.

Both cases are capped at the declared capital, floored to whole shares, and
computed in Decimal cents so the arithmetic is exact.

NOT INVESTMENT ADVICE. This is arithmetic on numbers the user supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

MIN_CAPITAL_CENTS = 1_00           # a dollar; below this there is nothing to size
MAX_RISK_PCT = Decimal("100")
DEFAULT_RISK_PCT = Decimal("1")


class SizingError(ValueError):
    pass


@dataclass(frozen=True)
class Recommendation:
    action: str
    ticker: str
    shares: int
    entry: Decimal
    stop: Decimal | None
    notional_cents: int
    risk_cents: int                # what a stop-out (or total loss) would cost
    capital_cents: int
    risk_pct: Decimal
    basis: str                     # stop_distance | full_position | none
    reason: str
    capped: bool = False

    @property
    def capital_fraction_pct(self) -> Decimal:
        if self.capital_cents <= 0:
            return Decimal(0)
        return (Decimal(self.notional_cents) / Decimal(self.capital_cents) * 100
                ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    def as_dict(self) -> dict:
        return {
            "action": self.action, "ticker": self.ticker, "shares": self.shares,
            "entry": str(self.entry), "stop": str(self.stop) if self.stop else None,
            "notional_cents": self.notional_cents, "risk_cents": self.risk_cents,
            "capital_cents": self.capital_cents, "risk_pct": str(self.risk_pct),
            "basis": self.basis, "reason": self.reason,
            "capital_fraction_pct": str(self.capital_fraction_pct),
            "capped": self.capped,
        }


def _cents(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def size_position(*, capital_cents: int, risk_pct: Decimal, entry: Decimal,
                  stop: Decimal | None, action: str, ticker: str) -> Recommendation:
    """The whole sizing rule. Raises SizingError on inputs that cannot be sized."""
    try:
        risk_pct = Decimal(str(risk_pct))
        entry = Decimal(str(entry))
        stop = Decimal(str(stop)) if stop is not None else None
    except (InvalidOperation, TypeError) as e:
        raise SizingError("capital, risk and price must be numbers") from e

    if capital_cents < MIN_CAPITAL_CENTS:
        raise SizingError("declare the capital you are trading with first")
    if not (0 < risk_pct <= MAX_RISK_PCT):
        raise SizingError("risk per trade must be between 0 and 100 percent")
    if entry <= 0:
        raise SizingError("the signal carried no usable price")

    risk_budget = Decimal(capital_cents) * risk_pct / 100      # in cents

    if stop is not None and stop > 0 and stop != entry:
        distance = abs(entry - stop)
        # risk budget is in cents, the distance is in currency units
        shares_exact = risk_budget / (distance * 100)
        basis = "stop_distance"
        reason = (f"risking {risk_pct}% of your capital over the "
                  f"{distance} distance from {entry} to your stop at {stop}")
    else:
        shares_exact = risk_budget / (entry * 100)
        basis = "full_position"
        reason = (f"no stop in the signal, so the whole position is treated as the "
                  f"money at risk — {risk_pct}% of your capital")

    shares = int(shares_exact.quantize(Decimal("1"), rounding=ROUND_DOWN))
    if shares <= 0:
        raise SizingError(
            "that risk budget does not buy a single share at this price — "
            "raise the capital or the risk percentage, or trade a cheaper instrument")

    capped = False
    notional = Decimal(shares) * entry * 100
    if notional > Decimal(capital_cents):
        # never recommend borrowing: fall back to what the capital actually buys
        shares = int((Decimal(capital_cents) / (entry * 100)).quantize(
            Decimal("1"), rounding=ROUND_DOWN))
        if shares <= 0:
            raise SizingError("one share costs more than your declared capital")
        notional = Decimal(shares) * entry * 100
        capped = True
        reason += " — capped at your declared capital, so the position is smaller"

    if basis == "stop_distance" and stop is not None:
        risk_cents = _cents(Decimal(shares) * abs(entry - stop) * 100)
    else:
        risk_cents = _cents(notional)

    return Recommendation(
        action=action, ticker=ticker, shares=shares, entry=entry, stop=stop,
        notional_cents=_cents(notional), risk_cents=risk_cents,
        capital_cents=capital_cents, risk_pct=risk_pct, basis=basis, reason=reason,
        capped=capped)
