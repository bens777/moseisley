"""The Darvas box method as deterministic code. No LLM, no I/O, no randomness.

Nicolas Darvas' rule, as implemented here:

  1. A BOX TOP is a high that survives `CONFIRM_DAYS` consecutive sessions
     without being exceeded.
  2. A BOX BOTTOM is the low made after that top, which then survives
     `CONFIRM_DAYS` sessions without being undercut.
  3. BUY when a close breaks above the box top.
  4. The stop-loss sits at the box bottom — never lower, never moved down.
  5. As price advances, new boxes form higher. Each confirmed box whose bottom
     is above the current stop RAISES the stop. That is the trailing rule.

The tracker consumes bars strictly in order and never looks ahead: fed the same
series it always produces the same signals, which is what makes the public
decision log auditable.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

CONFIRM_DAYS = 3           # sessions a high/low must survive to confirm


@dataclass(frozen=True)
class Bar:
    date: str              # ISO date, YYYY-MM-DD
    high: Decimal
    low: Decimal
    close: Decimal


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"
    TRAIL = "trail"


@dataclass(frozen=True)
class Signal:
    action: Action
    date: str
    price: Decimal            # close for a buy, the stop (or close on a gap) for a sell
    box_top: Decimal
    box_bottom: Decimal
    stop: Decimal
    reason: str


class _Phase(StrEnum):
    SEEKING_TOP = "seeking_top"
    SEEKING_BOTTOM = "seeking_bottom"
    BOX_SET = "box_set"


class DarvasTracker:
    """One ticker's box state. Feed it bars in date order; it emits signals."""

    def __init__(self, ticker: str, confirm_days: int = CONFIRM_DAYS):
        self.ticker = ticker
        self.confirm_days = confirm_days
        self._reset_top(None)
        self.in_position = False
        self.stop: Decimal | None = None
        self.entry: Decimal | None = None

    # ── box machinery ───────────────────────────────────────────────
    def _reset_top(self, high: Decimal | None) -> None:
        self.phase = _Phase.SEEKING_TOP
        self.top = high
        self.bottom: Decimal | None = None
        self.days_since_top = 0
        self.days_since_bottom = 0

    @property
    def box(self) -> tuple[Decimal, Decimal] | None:
        if self.phase is _Phase.BOX_SET and self.top is not None and self.bottom is not None:
            return self.bottom, self.top
        return None

    def feed(self, bar: Bar) -> list[Signal]:
        signals: list[Signal] = []

        # 1. the stop always gets first refusal — risk before opportunity
        if self.in_position and self.stop is not None and bar.low <= self.stop:
            # filled at the stop, unless the session gapped straight through it
            price = self.stop if bar.high >= self.stop else bar.close
            gapped = price != self.stop
            signals.append(Signal(
                Action.SELL, bar.date, price,
                self.top or self.stop, self.bottom or self.stop, self.stop,
                reason=(f"stop-loss at box bottom {self.stop} "
                        + ("hit on a gap below it, filled at the close"
                           if gapped else "hit intraday")),
            ))
            self.in_position = False
            self.stop = None
            self.entry = None
            self._reset_top(bar.high)
            return signals

        # 2. box formation
        if self.top is None:
            self._reset_top(bar.high)
            return signals

        if self.phase is _Phase.SEEKING_TOP:
            if bar.high > self.top:
                self.top = bar.high
                self.days_since_top = 0
            else:
                self.days_since_top += 1
                if self.days_since_top >= self.confirm_days:
                    self.phase = _Phase.SEEKING_BOTTOM
                    self.bottom = bar.low
                    self.days_since_bottom = 0

        elif self.phase is _Phase.SEEKING_BOTTOM:
            if bar.high > self.top:
                # a new high invalidates the unconfirmed box: start again
                self._reset_top(bar.high)
            elif self.bottom is None or bar.low < self.bottom:
                self.bottom = bar.low
                self.days_since_bottom = 0
            else:
                self.days_since_bottom += 1
                if self.days_since_bottom >= self.confirm_days:
                    self.phase = _Phase.BOX_SET
                    signals.extend(self._on_box_confirmed(bar))

        elif self.phase is _Phase.BOX_SET:
            assert self.bottom is not None
            if not self.in_position and bar.close > self.top:
                self.in_position = True
                self.entry = bar.close
                self.stop = self.bottom
                signals.append(Signal(
                    Action.BUY, bar.date, bar.close, self.top, self.bottom, self.bottom,
                    reason=(f"close {bar.close} broke above box top {self.top}; "
                            f"stop set at box bottom {self.bottom}"),
                ))
                # a position starts the search for the next, higher box
                self._reset_top(bar.high)
            elif self.in_position and bar.close > self.top:
                self._reset_top(bar.high)
            elif bar.low < self.bottom:
                # the box broke downward without us: start over from here
                self._reset_top(bar.high)

        return signals

    def _on_box_confirmed(self, bar: Bar) -> list[Signal]:
        """A freshly confirmed box only matters to an open position when its
        bottom is ABOVE the current stop. Stops never move down."""
        if not (self.in_position and self.stop is not None and self.bottom is not None):
            return []
        if self.bottom <= self.stop:
            return []
        previous, self.stop = self.stop, self.bottom
        return [Signal(
            Action.TRAIL, bar.date, bar.close, self.top, self.bottom, self.bottom,
            reason=(f"new box confirmed at {self.bottom}–{self.top}; "
                    f"stop trailed up from {previous} to {self.bottom}"),
        )]


def run(ticker: str, bars: list[Bar], confirm_days: int = CONFIRM_DAYS) -> list[Signal]:
    """Replay a whole series. Same input, same output, always."""
    tracker = DarvasTracker(ticker, confirm_days=confirm_days)
    out: list[Signal] = []
    for bar in bars:
        out.extend(tracker.feed(bar))
    return out
