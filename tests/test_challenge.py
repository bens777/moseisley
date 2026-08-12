"""The Darvas Challenge: deterministic strategy, exact accounting, public page.

FICTIONAL MONEY. These tests assert, among other things, that no part of this
feature can touch a broker or real funds — the point of the whole exercise is
that it is honest about being a simulation.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.challenge import data as feed
from backend.challenge import engine
from backend.challenge.darvas import Action, Bar, DarvasTracker, run
from backend.core.models import ChallengeDecision, ChallengeSnapshot

REPO = Path(__file__).resolve().parents[1]


def bar(date: str, high, low, close) -> Bar:
    return Bar(date=date, high=Decimal(str(high)), low=Decimal(str(low)),
               close=Decimal(str(close)))


def series(rows: list[tuple]) -> list[Bar]:
    """rows of (high, low, close) → dated bars starting 2026-01-01."""
    return [bar(f"2026-01-{i + 1:02d}", h, low, c) for i, (h, low, c) in enumerate(rows)]


def flat(n: int, high, low, close, start: int = 1) -> list[tuple]:
    return [(high, low, close)] * n


# ── 1. box detection ────────────────────────────────────────────────

def test_a_box_needs_a_top_and_a_bottom_that_both_survive_three_sessions():
    """Nothing fires until both halves of the box are confirmed."""
    tracker = DarvasTracker("T")
    # a high, then three quiet sessions → top confirmed, bottom search starts
    for b in series([(100, 90, 95)] + flat(3, 99, 91, 95)):
        assert tracker.feed(b) == []
    assert tracker.box is None, "a top alone is not a box"
    # three sessions that do not undercut the low → box confirmed
    for b in series(flat(3, 98, 92, 95)):
        tracker.feed(b)
    assert tracker.box is not None
    bottom, top = tracker.box
    assert top == Decimal("100")
    assert bottom == Decimal("91")


def test_breakout_above_the_box_top_buys_with_the_stop_at_the_box_bottom():
    bars = series([(100, 90, 95)] + flat(3, 99, 91, 95) + flat(3, 98, 92, 95)
                  + [(105, 96, 104)])
    signals = run("T", bars)
    assert [s.action for s in signals] == [Action.BUY]
    buy = signals[0]
    assert buy.price == Decimal("104")
    assert buy.box_top == Decimal("100") and buy.box_bottom == Decimal("91")
    assert buy.stop == Decimal("91")
    assert "broke above box top" in buy.reason and "stop set at box bottom" in buy.reason


def test_a_new_high_before_confirmation_invalidates_the_box():
    """Darvas' whole point: the top must hold. A fresh high restarts the count."""
    tracker = DarvasTracker("T")
    for b in series([(100, 90, 95)] + flat(3, 99, 91, 95) + [(110, 95, 108)]):
        tracker.feed(b)
    assert tracker.box is None
    assert tracker.top == Decimal("110"), "the box restarts from the new high"


def test_stop_loss_exits_at_the_box_bottom():
    bars = series([(100, 90, 95)] + flat(3, 99, 91, 95) + flat(3, 98, 92, 95)
                  + [(105, 96, 104), (104, 90, 92)])
    signals = run("T", bars)
    assert [s.action for s in signals] == [Action.BUY, Action.SELL]
    sell = signals[1]
    assert sell.price == Decimal("91"), "filled at the stop, not at the close"
    assert "stop-loss at box bottom" in sell.reason and "intraday" in sell.reason


def test_a_gap_straight_through_the_stop_fills_at_the_close_not_the_stop():
    """Honest simulation: you do not get the stop price when the market gaps."""
    bars = series([(100, 90, 95)] + flat(3, 99, 91, 95) + flat(3, 98, 92, 95)
                  + [(105, 96, 104), (85, 80, 82)])
    signals = run("T", bars)
    sell = signals[1]
    assert sell.action is Action.SELL
    assert sell.price == Decimal("82"), "gapped below the stop → filled at the close"
    assert "gap" in sell.reason


def test_the_stop_trails_up_with_each_higher_box_and_never_down():
    bars = series(
        [(100, 90, 95)] + flat(3, 99, 91, 95) + flat(3, 98, 92, 95)      # box 91–100
        + [(105, 96, 104)]                                                # BUY, stop 91
        + [(120, 106, 118)] + flat(3, 119, 110, 115) + flat(3, 118, 112, 115)  # box 110–120
    )
    signals = run("T", bars)
    kinds = [s.action for s in signals]
    assert kinds == [Action.BUY, Action.TRAIL]
    trail = signals[1]
    assert trail.stop == Decimal("110") > signals[0].stop
    assert "trailed up from 91 to 110" in trail.reason


def test_the_stop_never_moves_down_while_a_position_is_open():
    """A box bottom below the stop cannot happen — that bar would have stopped
    us out first — so the invariant to hold is monotonicity, not a special case."""
    tracker = DarvasTracker("T")
    stops: list[Decimal] = []
    rows = ([(100, 90, 95)] + flat(3, 99, 91, 95) + flat(3, 98, 92, 95)
            + [(105, 96, 104)]                                    # BUY, stop 91
            + flat(3, 108, 95, 100) + flat(3, 107, 96, 100)       # box 95-108
            + [(130, 110, 128)] + flat(3, 129, 115, 120) + flat(3, 128, 116, 120))
    for b in series(rows):
        tracker.feed(b)
        if tracker.in_position and tracker.stop is not None:
            stops.append(tracker.stop)
    assert stops, "the series should open a position"
    assert stops == sorted(stops), f"the stop moved down: {stops}"
    assert stops[-1] > stops[0], "higher boxes should have trailed it up"


def test_the_same_series_always_produces_the_same_signals():
    bars = series([(100, 90, 95)] + flat(3, 99, 91, 95) + flat(3, 98, 92, 95)
                  + [(105, 96, 104), (104, 90, 92)])
    first = [(s.action, s.date, s.price) for s in run("T", bars)]
    second = [(s.action, s.date, s.price) for s in run("T", bars)]
    assert first == second


# ── 2. portfolio accounting ─────────────────────────────────────────

def _stopped_out_series() -> list[Bar]:
    """Box 91-100, a close at 104 breaks out, then a drop through the 91 stop."""
    return series([(100, 90, 95)] + flat(3, 99, 91, 95) + flat(3, 98, 92, 95)
                  + [(105, 96, 104)]                       # BUY at 104
                  + [(104, 90, 92)])                       # low 90 → stopped at 91

def test_buy_spends_exactly_twenty_percent_and_never_more_cash_than_it_has():
    result = engine.simulate({"T": _stopped_out_series()})
    buy = next(d for d in result.decisions if d.action == "buy")
    # $10,000 × 20% = $2,000 at a price of 104 → 19.23076923 units, rounded DOWN
    assert buy.units == Decimal("19.23076923")
    assert buy.cash_cents_after == engine.START_CENTS - 200_000
    assert buy.equity_cents_after == engine.START_CENTS   # cash swapped for stock


def test_a_losing_exit_books_the_loss_exactly():
    result = engine.simulate({"T": _stopped_out_series()})
    sell = next(d for d in result.decisions if d.action == "sell")
    # 19.23076923 units cost $2,000.00; sold at the 91 stop for $1,750.00 → −$250
    assert sell.price == Decimal("91")
    assert sell.realized_pnl_cents == -25_000
    assert sell.cash_cents_after == engine.START_CENTS - 25_000
    assert sell.equity_cents_after == engine.START_CENTS - 25_000


def test_cash_plus_holdings_always_equals_equity():
    result = engine.simulate({"T": _stopped_out_series()})
    for snap in result.snapshots:
        holdings = sum(p["value_cents"] for p in snap.positions)
        assert snap.cash_cents + holdings == snap.equity_cents


def test_the_portfolio_can_never_go_negative():
    result = engine.simulate({"T": _stopped_out_series()})
    assert all(s.cash_cents >= 0 for s in result.snapshots)
    assert all(d.cash_cents_after >= 0 for d in result.decisions)


def test_concurrent_positions_are_capped():
    many = {f"T{i}": _stopped_out_series() for i in range(8)}
    result = engine.simulate(many)
    assert max(len(s.positions) for s in result.snapshots) <= engine.MAX_POSITIONS


def test_no_position_is_opened_twice():
    result = engine.simulate({"T": _stopped_out_series()})
    opens = [d for d in result.decisions if d.action == "buy"]
    assert len(opens) == len({(d.trade_date, d.symbol) for d in opens})


def test_a_flat_market_trades_nothing_and_holds_the_starting_equity():
    result = engine.simulate({"T": series(flat(30, 100, 99, 99.5))})
    assert result.decisions == []
    assert all(s.equity_cents == engine.START_CENTS for s in result.snapshots)


# ── 3. the decision log is complete and persisted ───────────────────

async def test_every_signal_is_recorded_with_its_reason(db_session):
    result = engine.simulate({"T": _stopped_out_series()})
    await engine.persist(db_session, result)
    await db_session.commit()

    rows = list((await db_session.execute(
        select(ChallengeDecision).order_by(ChallengeDecision.trade_date))).scalars())
    assert len(rows) == len(result.decisions) == 2
    assert [r.action for r in rows] == ["buy", "sell"]
    for row in rows:
        assert row.reason and row.box_top and row.box_bottom and row.stop
        assert row.trade_date and row.symbol == "T"


async def test_persisting_twice_does_not_duplicate_the_log(db_session):
    result = engine.simulate({"T": _stopped_out_series()})
    first = await engine.persist(db_session, result)
    second = await engine.persist(db_session, result)
    await db_session.commit()
    assert first["decisions_written"] == 2 and second["decisions_written"] == 0
    rows = (await db_session.execute(select(ChallengeDecision))).scalars().all()
    assert len(rows) == 2


async def test_snapshots_give_one_mark_per_day(db_session):
    result = engine.simulate({"T": _stopped_out_series()})
    await engine.persist(db_session, result)
    await db_session.commit()
    rows = list((await db_session.execute(
        select(ChallengeSnapshot).order_by(ChallengeSnapshot.trade_date))).scalars())
    assert len(rows) == len(result.snapshots)
    assert all(r.status == "running" for r in rows)


# ── 4. fail-quiet on missing data ───────────────────────────────────

async def test_a_dead_feed_pauses_the_challenge_and_invents_nothing(db_session, monkeypatch):
    async def dead():
        raise feed.DataFeedDown("both venues unreachable")

    monkeypatch.setattr(feed, "fetch_all", dead)
    out = await engine.run_challenge(db_session)
    await db_session.commit()

    assert out["status"] == "paused" and "data feed down" in out["note"]
    assert (await db_session.execute(select(ChallengeDecision))).scalars().all() == []
    snap = (await db_session.execute(select(ChallengeSnapshot))).scalars().one()
    assert snap.status == "paused"


async def test_too_few_series_pauses_rather_than_simulating_a_partial_universe(
        db_session, monkeypatch):
    async def thin():
        return {"BTC": _stopped_out_series()}, ["ETH", "SOL"], {"BTC": "kraken"}

    monkeypatch.setattr(feed, "fetch_all", thin)
    out = await engine.run_challenge(db_session)
    assert out["status"] == "paused"
    assert "1 of 10" in out["note"]
    assert (await db_session.execute(select(ChallengeDecision))).scalars().all() == []


async def test_a_pause_freezes_the_last_known_equity_rather_than_zeroing_it(
        db_session, monkeypatch):
    await engine.persist(db_session, engine.simulate({"T": _stopped_out_series()}))
    await db_session.commit()
    last = list((await db_session.execute(
        select(ChallengeSnapshot).order_by(ChallengeSnapshot.trade_date))).scalars())[-1]

    async def dead():
        raise feed.DataFeedDown("down")

    monkeypatch.setattr(feed, "fetch_all", dead)
    await engine.run_challenge(db_session)
    await db_session.commit()

    paused = (await db_session.execute(select(ChallengeSnapshot).where(
        ChallengeSnapshot.status == "paused"))).scalars().one()
    assert paused.equity_cents == last.equity_cents


def test_a_self_contradicting_bar_is_dropped_not_repaired():
    payload = {"error": [], "result": {"XXBTZUSD": [
        [1786492800, "1", "100", "90", "95", "0", "0", 1],     # sane
        [1786579200, "1", "50", "200", "95", "0", "0", 1],     # high < low: nonsense
    ]}}
    bars = feed.parse_kraken(payload)
    assert len(bars) == 1 and bars[0].high == Decimal("100")


def test_an_empty_feed_response_raises_rather_than_returning_nothing():
    with pytest.raises(feed.DataFeedDown):
        feed.parse_kraken({"error": ["EGeneral:Invalid arguments"], "result": {}})
    with pytest.raises(feed.DataFeedDown):
        feed.parse_coinbase([])


def test_kraken_parsing_reads_the_key_it_gets_back_not_the_one_requested():
    """Kraken answers XBTUSD with XXBTZUSD — assuming otherwise breaks silently."""
    payload = {"error": [], "result": {"XXBTZUSD": [[1786492800, "1", "100", "90", "95",
                                                     "0", "0", 1]], "last": 1786492800}}
    assert feed.parse_kraken(payload)[0].close == Decimal("95")


def test_coinbase_column_order_differs_from_kraken_and_is_handled():
    """Coinbase rows are [time, LOW, HIGH, open, close, volume] — reversed vs Kraken."""
    bars = feed.parse_coinbase([[1786492800, 90, 100, 92, 95, 1.0]])
    assert bars[0].low == Decimal("90") and bars[0].high == Decimal("100")


# ── 5. the public page ──────────────────────────────────────────────

async def test_the_challenge_is_readable_with_no_authentication(client, db_session):
    await engine.persist(db_session, engine.simulate({"BTC": _stopped_out_series()}))
    await db_session.commit()

    resp = await client.get("/api/public/challenge")      # no auth header at all
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["stats"]["start_cents"] == 1_000_000
    assert len(body["decisions"]) == 2
    assert body["equity_curve"]


async def test_the_legal_framing_is_served_with_every_response(client):
    body = (await client.get("/api/public/challenge")).json()
    for phrase in ("Simulated portfolio", "Fictional money",
                   "not investment advice or a solicitation"):
        assert phrase in body["legal"], phrase


async def test_the_public_payload_says_no_model_is_in_the_trading_loop(client):
    body = (await client.get("/api/public/challenge")).json()
    assert "Deterministic code" in body["method"]["decided_by"]
    assert len(body["method"]["rules"]) == 5
    assert len(body["watchlist"]) == 10


async def test_stats_are_computed_from_the_recorded_log(client, db_session):
    await engine.persist(db_session, engine.simulate({"BTC": _stopped_out_series()}))
    await db_session.commit()
    stats = (await client.get("/api/public/challenge")).json()["stats"]
    assert stats["closed_trades"] == 1 and stats["wins"] == 0
    assert stats["win_rate_pct"] == 0.0
    assert stats["realized_pnl_cents"] == -25_000
    assert stats["equity_cents"] == 1_000_000 - 25_000
    assert stats["pnl_pct"] == -2.5
    assert stats["max_drawdown_pct"] > 0


async def test_an_untouched_challenge_reports_not_started_rather_than_failing(client):
    body = (await client.get("/api/public/challenge")).json()
    assert body["status"] == "not_started"
    assert body["stats"]["equity_cents"] == 1_000_000
    assert body["decisions"] == [] and body["positions"] == []


# ── 6. hard rules ───────────────────────────────────────────────────

def test_no_code_in_this_feature_can_place_an_order_anywhere():
    """The strongest guarantee available in a test: the feature contains no
    trading credentials, no private endpoints and no order verbs."""
    banned = ("api_secret", "/0/private/", "addorder", "place_order",
              "submit_order", "create_order", "hmac", "authorization")
    for path in sorted((REPO / "backend" / "challenge").glob("*.py")):
        source = path.read_text(encoding="utf-8").lower()
        for token in banned:
            assert token.lower() not in source, (path.name, token)


def test_the_data_layer_only_talks_to_public_read_only_endpoints():
    assert feed.KRAKEN_URL == "https://api.kraken.com/0/public/OHLC"
    assert "/products/{product}/candles" in feed.COINBASE_URL
    source = (REPO / "backend" / "challenge" / "data.py").read_text(encoding="utf-8")
    assert "client.post" not in source and "client.put" not in source


def test_the_watchlist_is_a_constant_of_ten_pairs():
    assert len(feed.WATCHLIST) == 10
    assert len({i.symbol for i in feed.WATCHLIST}) == 10
    for i in feed.WATCHLIST:
        assert i.kraken_pair.endswith("USD") and i.coinbase_product.endswith("-USD")


def test_the_public_page_is_reachable_without_a_session():
    shell = (REPO / "apps" / "web" / "components" / "shell.tsx").read_text(encoding="utf-8")
    assert '"/challenge"' in shell.split("PUBLIC_PATHS")[1].split("]")[0]


def test_the_page_states_the_framing_prominently_not_as_fine_print():
    page = (REPO / "apps" / "web" / "app" / "challenge" / "page.tsx").read_text(encoding="utf-8")
    assert "read this first" in page
    assert "no brokerage account and no broker connection" in page
    assert "Build your own crew" in page


def test_the_manager_is_told_what_this_is_and_is_not():
    from backend.agents import actions, crew

    reference = crew.platform_reference()
    assert "Darvas Challenge" in reference
    assert "FICTIONAL money" in reference
    assert "never imply the user can trade with the platform" in reference
    assert actions.ACTION_ROUTES["challenge"] == "/challenge"


def test_the_daily_run_is_a_platform_job_with_no_user(db_session):
    from backend.jobs import handlers as job_handlers  # noqa: F401 — registers handlers
    from backend.jobs.scheduler import HANDLERS

    assert "challenge_run" in HANDLERS


async def test_the_platform_schedule_is_created_once(db_session):
    from backend.core.models import ScheduledJob
    from backend.jobs import scheduler

    await scheduler.ensure_platform_schedules(db_session)
    await scheduler.ensure_platform_schedules(db_session)
    jobs = list((await db_session.execute(select(ScheduledJob).where(
        ScheduledJob.job_type == "challenge_run"))).scalars())
    assert len(jobs) == 1
    assert jobs[0].user_id is None and jobs[0].interval_seconds == 86400
