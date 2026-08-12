"""Private per-user market data: yfinance for equities, Kraken for crypto.

The load-bearing property is the PRIVACY BOUNDARY. Equity data is licensed for
this user's own screen; the public Darvas Challenge page must never be able to
show it. That is asserted structurally here, not left to good intentions.

No test in this file touches the network: yfinance is always stubbed.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from backend.challenge.darvas import Bar
from backend.marketdata import service as marketdata
from backend.marketdata import yahoo

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_cache_between_tests():
    yahoo.clear_cache()
    yield
    yahoo.clear_cache()


def _bars(n: int = 5) -> list[Bar]:
    return [Bar(date=f"2026-08-{i + 1:02d}", high=Decimal("101"), low=Decimal("99"),
                close=Decimal("100")) for i in range(n)]


# ── the privacy boundary ────────────────────────────────────────────

def test_the_public_challenge_cannot_reach_equity_data():
    """One-way dependency: marketdata imports challenge, never the reverse."""
    for path in sorted((REPO / "backend" / "challenge").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "yfinance" not in source, path.name
        assert "marketdata" not in source, path.name


def test_the_public_route_serves_only_crypto_from_the_challenge_feed():
    route = (REPO / "backend" / "api" / "routes" / "challenge.py").read_text(encoding="utf-8")
    assert "marketdata" not in route and "yfinance" not in route
    service = (REPO / "backend" / "challenge" / "service.py").read_text(encoding="utf-8")
    assert "marketdata" not in service


def test_the_market_data_router_is_not_public():
    """It must be a plain authenticated router — no public_router in sight."""
    route = (REPO / "backend" / "api" / "routes" / "marketdata.py").read_text(encoding="utf-8")
    assert "public_router = " not in route      # the declaration, not the prose
    assert "CurrentUser" in route
    app = (REPO / "backend" / "api" / "app.py").read_text(encoding="utf-8")
    assert "marketdata.public_router" not in app


async def test_market_data_requires_a_login(client):
    assert (await client.get("/api/marketdata/AAPL")).status_code == 401
    assert (await client.get("/api/marketdata", params={"symbols": "AAPL"})).status_code == 401


async def test_every_response_is_marked_internal_only(client, auth, monkeypatch):
    async def fake(symbol, days=90):
        return _bars()

    monkeypatch.setattr(yahoo, "fetch_daily", fake)
    body = (await client.get("/api/marketdata/AAPL", headers=auth)).json()
    assert body["internal_only"] is True
    assert "not redistributed" in body["notice"]
    assert "not shown publicly" in body["notice"]


async def test_the_public_challenge_payload_never_contains_an_equity_symbol(client):
    body = (await client.get("/api/public/challenge")).json()
    symbols = {w["symbol"] for w in body["watchlist"]}
    assert symbols == set(marketdata.CRYPTO_SYMBOLS)
    assert "AAPL" not in str(body)


# ── routing: the right source per asset class ───────────────────────

def test_symbols_route_to_the_right_source():
    for symbol in ("BTC", "ETH", "SOL", "ATOM"):
        assert marketdata.classify(symbol) == "crypto"
    for symbol in ("AAPL", "SPY", "MSFT", "BRK.B", "VOO"):
        assert marketdata.classify(symbol) == "equity"
    assert marketdata.classify("btc") == "crypto"        # case-insensitive


async def test_an_equity_symbol_goes_to_yfinance(monkeypatch):
    called: dict = {}

    async def fake(symbol, days=90):
        called["symbol"] = symbol
        called["days"] = days
        return _bars()

    monkeypatch.setattr(yahoo, "fetch_daily", fake)
    series = await marketdata.fetch_daily("aapl", days=30)
    assert called == {"symbol": "AAPL", "days": 30}   # normalized before dispatch
    assert series.asset_class == "equity" and series.source == "yahoo"
    assert series.last_close == Decimal("100")


async def test_a_crypto_symbol_reuses_the_challenge_feed_not_yfinance(monkeypatch):
    from backend.challenge import data as crypto_feed

    async def fake_series(client, instrument):
        assert instrument.symbol == "BTC"
        return _bars(), "kraken"

    def explode(*_a, **_k):
        raise AssertionError("crypto must never reach yfinance")

    monkeypatch.setattr(crypto_feed, "fetch_series", fake_series)
    monkeypatch.setattr(yahoo, "fetch_daily", explode)

    series = await marketdata.fetch_daily("BTC")
    assert series.asset_class == "crypto" and series.source == "kraken"


# ── fail quiet ──────────────────────────────────────────────────────

async def test_an_unavailable_symbol_says_so_and_invents_nothing(client, auth, monkeypatch):
    async def dead(symbol, days=90):
        raise yahoo.MarketDataUnavailable(f"{symbol}: no bars returned")

    monkeypatch.setattr(yahoo, "fetch_daily", dead)
    resp = await client.get("/api/marketdata/NOSUCH", headers=auth)
    assert resp.status_code == 503
    assert "no bars returned" in resp.json()["detail"]


async def test_one_dead_symbol_does_not_blank_the_whole_panel(monkeypatch):
    async def flaky(symbol, days=90):
        if symbol == "BROKEN":
            raise yahoo.MarketDataUnavailable("BROKEN: no bars returned")
        return _bars()

    monkeypatch.setattr(yahoo, "fetch_daily", flaky)
    out = await marketdata.fetch_many(["AAPL", "BROKEN", "SPY"])
    assert [s["symbol"] for s in out["series"]] == ["AAPL", "SPY"]
    assert out["unavailable"] == [{"symbol": "BROKEN", "reason": "BROKEN: no bars returned"}]


def test_a_missing_yfinance_install_degrades_instead_of_crashing(monkeypatch):
    """A deployment without pandas should lose equities, not fail to boot."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("no module named yfinance")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(yahoo.MarketDataUnavailable, match="not installed"):
        yahoo._fetch_blocking("AAPL", 30)


def test_nonsense_symbols_are_rejected_before_any_network_call():
    for bad in ("", "   ", "'; DROP TABLE users;--", "A" * 20, "../../etc/passwd", "A B"):
        assert not yahoo.valid_symbol(bad), bad
    for good in ("AAPL", "SPY", "BRK.B", "RDS-A", " aapl "):   # whitespace is trimmed
        assert yahoo.valid_symbol(good), good


async def test_an_invalid_symbol_never_reaches_the_network(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("must not call out for an invalid symbol")

    monkeypatch.setattr(yahoo, "_fetch_blocking", explode)
    with pytest.raises(yahoo.MarketDataUnavailable):
        await yahoo.fetch_daily("not a ticker")


# ── parsing and caching ─────────────────────────────────────────────

def test_broken_rows_are_dropped_never_repaired(monkeypatch):
    class FakeFrame:
        empty = False

        def iterrows(self):
            class Stamp:
                def __init__(self, d): self._d = d
                def date(self): return self._d
            import datetime as dt
            yield Stamp(dt.date(2026, 8, 1)), {"High": 101.0, "Low": 99.0, "Close": 100.0}
            yield Stamp(dt.date(2026, 8, 2)), {"High": 50.0, "Low": 200.0, "Close": 100.0}
            yield Stamp(dt.date(2026, 8, 3)), {"High": None, "Low": 1.0, "Close": 1.0}

    class FakeTicker:
        def __init__(self, symbol): pass
        def history(self, **kwargs): return FakeFrame()

    import sys
    import types
    module = types.ModuleType("yfinance")
    module.Ticker = FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", module)

    bars = yahoo._fetch_blocking("AAPL", 30)
    assert len(bars) == 1 and bars[0].date == "2026-08-01"


async def test_repeat_requests_are_served_from_cache(monkeypatch):
    calls = {"n": 0}

    def counted(symbol, days):
        calls["n"] += 1
        return _bars()

    monkeypatch.setattr(yahoo, "_fetch_blocking", counted)
    for _ in range(3):
        await yahoo.fetch_daily("AAPL", days=30)
    assert calls["n"] == 1, "a dashboard refresh must not hit Yahoo every time"


async def test_the_assistant_tool_returns_data_and_refuses_to_guess(db_session, client,
                                                                     auth, monkeypatch):
    from backend.agents.orchestrator import _execute_tool
    from backend.core.models import User

    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    user = await db_session.get(User, uid)

    async def fake(symbol, days=90):
        return _bars()

    monkeypatch.setattr(yahoo, "fetch_daily", fake)
    out = await _execute_tool(db_session, user, "marketdata.daily",
                              type("A", (), {"symbol": "AAPL", "days": 30})(), "run-1")
    assert out["symbol"] == "AAPL" and out["source"] == "yahoo"
    assert out["last_close"] == "100" and len(out["bars"]) == 5

    async def dead(symbol, days=90):
        raise yahoo.MarketDataUnavailable("AAPL: no bars returned")

    monkeypatch.setattr(yahoo, "fetch_daily", dead)
    out = await _execute_tool(db_session, user, "marketdata.daily",
                              type("A", (), {"symbol": "AAPL", "days": 30})(), "run-1")
    assert out["error"] == "unavailable"
    assert "Never estimate a price" in out["note"]


def test_the_manager_is_told_not_to_give_financial_advice():
    prompt = (REPO / "backend" / "prompts" / "manager.md").read_text(encoding="utf-8")
    assert "marketdata.daily" in prompt
    assert "not a financial adviser" in prompt
    assert "estimating a price" in prompt
