"""Trader Assistant: inbound TradingView alerts, the journal, and the advice.

WHAT THIS IS NOT: there is no broker connection, no order, no position and no
money — real or simulated — anywhere in this feature. TradingView has no public
API for placing orders on a user's account, and we do not pretend otherwise. The
user's own strategy fires an alert, we receive it, we do arithmetic, the user
decides and executes. That is the whole loop.

The webhook is a PUBLIC endpoint authenticated only by its URL token, so every
byte arriving there is untrusted: strict schema, hard length limits, the
Prompt-5 deterministic screening on any text that could reach an agent, and a
per-token rate limit.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import inspection
from backend.core.models import TradingSignal, TradingWebhook, User
from backend.ledger import service as ledger
from backend.trading import sizing

logger = logging.getLogger("mychief.trading")

DISCLAIMER = ("Not investment advice. Signals come from YOUR TradingView strategies. "
              "You alone execute and are responsible.")

SETTINGS_KEY = "trader_assistant"     # user.settings_json
MAX_STRATEGY_CHARS = 64
MAX_NOTE_CHARS = 200
MAX_SIGNALS_PER_MINUTE = 10
MAX_SIGNALS_PER_HOUR = 120
REPLAY_WINDOW_SECONDS = 300

# token -> [timestamps]. Process-local: this is abuse control on a public
# endpoint, not billing, and it must not need a new dependency.
_hits: dict[str, list[float]] = {}


class WebhookRejected(Exception):
    """Bad token, rate limit, or a payload we will not accept."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


# ── token ───────────────────────────────────────────────────────────

def _hash(verifier: str) -> str:
    return hashlib.sha256(verifier.encode()).hexdigest()


def split_token(token: str) -> tuple[str, str] | None:
    selector, _, verifier = token.partition(".")
    if not selector or not verifier or len(selector) > 32:
        return None
    return selector, verifier


async def issue_token(db: AsyncSession, user: User) -> str:
    """Mint a fresh endpoint token, revoking whatever came before it."""
    for row in (await db.execute(select(TradingWebhook).where(
            TradingWebhook.user_id == user.id,
            TradingWebhook.revoked_at.is_(None)))).scalars():
        row.revoked_at = datetime.now(UTC)

    selector = secrets.token_hex(8)
    verifier = secrets.token_urlsafe(32)
    db.add(TradingWebhook(user_id=user.id, selector=selector,
                          verifier_hash=_hash(verifier)))
    await db.flush()
    await ledger.record(db, user.id, "trading_webhook_issued", actor_type="user",
                        entity_type="trading_webhook", entity_id=selector)
    return f"{selector}.{verifier}"


async def revoke_tokens(db: AsyncSession, user: User) -> int:
    rows = list((await db.execute(select(TradingWebhook).where(
        TradingWebhook.user_id == user.id,
        TradingWebhook.revoked_at.is_(None)))).scalars())
    for row in rows:
        row.revoked_at = datetime.now(UTC)
    if rows:
        await db.flush()
        await ledger.record(db, user.id, "trading_webhook_revoked", actor_type="user",
                            entity_type="trading_webhook", entity_id=rows[0].selector)
    return len(rows)


async def resolve_token(db: AsyncSession, token: str) -> TradingWebhook:
    """Find the endpoint this token belongs to, in constant time on the secret."""
    parts = split_token(token or "")
    if parts is None:
        raise WebhookRejected(404, "unknown endpoint")
    selector, verifier = parts

    row = (await db.execute(select(TradingWebhook).where(
        TradingWebhook.selector == selector))).scalar_one_or_none()
    if row is None:
        # still spend the comparison so a missing selector and a wrong verifier
        # cost the same
        secrets.compare_digest(_hash(verifier), _hash("nothing"))
        raise WebhookRejected(404, "unknown endpoint")
    if not secrets.compare_digest(row.verifier_hash, _hash(verifier)):
        raise WebhookRejected(404, "unknown endpoint")
    if row.revoked_at is not None:
        raise WebhookRejected(404, "unknown endpoint")
    return row


def check_rate_limit(selector: str, *, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    hits = [t for t in _hits.get(selector, []) if now - t < 3600]
    if len([t for t in hits if now - t < 60]) >= MAX_SIGNALS_PER_MINUTE:
        raise WebhookRejected(429, "too many signals — slow the alert down")
    if len(hits) >= MAX_SIGNALS_PER_HOUR:
        raise WebhookRejected(429, "hourly signal limit reached for this endpoint")
    hits.append(now)
    _hits[selector] = hits


def reset_rate_limits() -> None:
    _hits.clear()


# ── payload ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CleanSignal:
    ticker: str
    action: str
    price: Decimal
    stop: Decimal | None
    strategy: str
    note: str
    screening: dict
    raw: dict


ACTIONS = {"buy", "long", "sell", "short", "close", "exit"}
_CANONICAL = {"long": "buy", "short": "sell", "exit": "close"}


def _text_field(value, limit: int, name: str) -> tuple[str, list[str]]:
    """Short, screened text or nothing. Long free text is refused, not truncated
    silently — an alert template that overflows is a misconfiguration."""
    if value is None:
        return "", []
    text = str(value)
    if len(text) > limit:
        raise WebhookRejected(422, f"{name} is longer than {limit} characters — "
                                   "keep alert text short")
    verdict, reasons, cleaned = inspection.screen_deterministic(text)
    if verdict == inspection.MALICIOUS:
        raise WebhookRejected(422, f"{name} was refused by content screening")
    return cleaned, reasons


def parse_payload(payload: dict) -> CleanSignal:
    """Strict: unknown shapes are refused rather than guessed at."""
    if not isinstance(payload, dict):
        raise WebhookRejected(422, "the alert body must be a JSON object")

    raw_ticker = str(payload.get("ticker") or payload.get("symbol") or "").strip().upper()
    if not (1 <= len(raw_ticker) <= 24) or not all(
            c.isalnum() or c in ".:-_/" for c in raw_ticker):
        raise WebhookRejected(422, "ticker is missing or not a plausible symbol")

    raw_action = str(payload.get("action") or payload.get("side") or "").strip().lower()
    if raw_action not in ACTIONS:
        raise WebhookRejected(422, f"action must be one of {sorted(ACTIONS)}")
    action = _CANONICAL.get(raw_action, raw_action)

    def decimal_field(key: str, *, required: bool) -> Decimal | None:
        value = payload.get(key)
        if value in (None, ""):
            if required:
                raise WebhookRejected(422, f"{key} is required")
            return None
        try:
            out = Decimal(str(value).replace(",", ""))
        except Exception as e:  # noqa: BLE001 — any parse failure is a bad payload
            raise WebhookRejected(422, f"{key} is not a number") from e
        if out <= 0 or out > Decimal("1e12"):
            raise WebhookRejected(422, f"{key} is out of range")
        return out

    price = decimal_field("price", required=True)
    stop = decimal_field("stop", required=False)

    strategy, s_reasons = _text_field(payload.get("strategy"), MAX_STRATEGY_CHARS,
                                      "strategy")
    note, n_reasons = _text_field(payload.get("note"), MAX_NOTE_CHARS, "note")
    reasons = s_reasons + n_reasons

    # only the fields we understand are kept; anything else is dropped, never
    # stored and never shown
    raw = {"ticker": raw_ticker, "action": raw_action, "price": str(price),
           "stop": str(stop) if stop else None, "strategy": strategy, "note": note}
    return CleanSignal(ticker=raw_ticker, action=action, price=price, stop=stop,
                       strategy=strategy, note=note, raw=raw,
                       screening={"verdict": inspection.SUSPICIOUS if reasons
                                  else inspection.NONE,
                                  "reasons": reasons, "stage": "deterministic"})


# ── assistant settings ──────────────────────────────────────────────

def settings_for(user: User) -> dict:
    raw = (user.settings_json or {}).get(SETTINGS_KEY) or {}
    return {
        "enabled": bool(raw.get("enabled")),
        "capital_cents": int(raw.get("capital_cents") or 0),
        "risk_pct": str(raw.get("risk_pct") or sizing.DEFAULT_RISK_PCT),
    }


async def save_settings(db: AsyncSession, user: User, *, enabled: bool,
                        capital_cents: int, risk_pct: Decimal) -> dict:
    if capital_cents < 0:
        raise ValueError("capital cannot be negative")
    if not (0 < Decimal(str(risk_pct)) <= sizing.MAX_RISK_PCT):
        raise ValueError("risk per trade must be between 0 and 100 percent")
    if enabled and capital_cents < sizing.MIN_CAPITAL_CENTS:
        raise ValueError("declare the capital you are trading with before turning "
                         "the assistant on")
    user.settings_json = {**(user.settings_json or {}), SETTINGS_KEY: {
        "enabled": bool(enabled), "capital_cents": int(capital_cents),
        "risk_pct": str(risk_pct)}}
    await db.flush()
    return settings_for(user)


# ── the message the user reads ──────────────────────────────────────

def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def compose_message(signal: CleanSignal,
                    rec: sizing.Recommendation | None,
                    problem: str | None) -> str:
    """Deterministic on purpose. Every number in a message about the user's own
    money is computed, not generated — a model is never asked to phrase a size."""
    strategy = signal.strategy or "your strategy"
    head = (f"Signal: {strategy} — {signal.action.upper()} {signal.ticker} "
            f"at {signal.price}.")
    if rec is None:
        return (f"{head}\n\nI cannot size this one: {problem}\n\n"
                f"Logged it on your trading journal either way.\n\n{DISCLAIMER}")

    stop_line = (f" Your stop sits at {rec.stop}, so a stop-out costs about "
                 f"{_money(rec.risk_cents)}."
                 if rec.basis == "stop_distance"
                 else f" No stop came with the signal, so I sized the whole position at "
                      f"your risk budget — a total loss costs {_money(rec.risk_cents)}.")
    return (f"{head}\n\n"
            f"Suggested: {rec.action.upper()} ~{rec.shares} shares "
            f"(~{_money(rec.notional_cents)}, {rec.capital_fraction_pct}% of your "
            f"declared capital).{stop_line}\n\n"
            f"Sizing: {rec.reason}.\n\n"
            f"You execute — I never touch your account, and I cannot place orders "
            f"anywhere.\n\n{DISCLAIMER}")


# ── the whole inbound path ──────────────────────────────────────────

async def receive(db: AsyncSession, token: str, payload: dict) -> dict:
    """Authenticate, screen, journal, advise. Never raises past WebhookRejected."""
    hook = await resolve_token(db, token)
    check_rate_limit(hook.selector)

    signal = parse_payload(payload)
    user = await db.get(User, hook.user_id)
    if user is None:
        raise WebhookRejected(404, "unknown endpoint")

    # collapse a duplicate alert fired twice within the replay window
    key = hashlib.sha256(
        f"{hook.user_id}|{signal.ticker}|{signal.action}|{signal.price}|"
        f"{signal.strategy}|{int(time.time()) // REPLAY_WINDOW_SECONDS}".encode()
    ).hexdigest()[:64]
    duplicate = (await db.execute(select(TradingSignal).where(
        TradingSignal.idempotency_key == key))).scalar_one_or_none()
    if duplicate is not None:
        return {"ok": True, "duplicate": True, "signal_id": duplicate.id}

    settings = settings_for(user)
    rec: sizing.Recommendation | None = None
    problem: str | None = None
    if settings["enabled"] and signal.action in ("buy", "sell"):
        try:
            rec = sizing.size_position(
                capital_cents=settings["capital_cents"],
                risk_pct=Decimal(settings["risk_pct"]),
                entry=signal.price, stop=signal.stop,
                action=signal.action, ticker=signal.ticker)
        except sizing.SizingError as e:
            problem = str(e)

    row = TradingSignal(
        user_id=user.id, ticker=signal.ticker, action=signal.action,
        price=str(signal.price), stop=str(signal.stop) if signal.stop else None,
        strategy=signal.strategy, note=signal.note, raw_payload=signal.raw,
        screening=signal.screening,
        recommendation=rec.as_dict() if rec else ({"error": problem} if problem else {}),
        idempotency_key=key)
    db.add(row)
    hook.signal_count += 1
    hook.last_used_at = datetime.now(UTC)
    await db.flush()

    await ledger.record(db, user.id, "trading_signal_received", actor_type="system",
                        entity_type="trading_signal", entity_id=row.id,
                        payload={"ticker": signal.ticker, "action": signal.action,
                                 "strategy": signal.strategy,
                                 "screening": signal.screening.get("verdict")})

    posted = False
    if settings["enabled"] and (rec is not None or problem is not None):
        await _post_to_manager(db, user, compose_message(signal, rec, problem))
        posted = True
    return {"ok": True, "signal_id": row.id, "advised": posted}


async def _post_to_manager(db: AsyncSession, user: User, text: str) -> None:
    from backend.agents import manager as manager_svc
    from backend.core.models import ChatMessage

    session = await manager_svc.get_session(db, user)
    db.add(ChatMessage(user_id=user.id, session_id=session.id, role="assistant",
                       content=text, channel="web",
                       metadata_json={"trader_assistant": True}))
    await db.flush()


# ── reads ───────────────────────────────────────────────────────────

def serialize(row: TradingSignal) -> dict:
    return {
        "id": row.id, "received_at": row.received_at, "ticker": row.ticker,
        "action": row.action, "price": row.price, "stop": row.stop,
        "strategy": row.strategy, "note": row.note, "raw_payload": row.raw_payload,
        "screening": row.screening, "recommendation": row.recommendation,
    }


async def signal_count(db: AsyncSession, user_id: str) -> int:
    return int((await db.execute(select(func.count()).select_from(TradingSignal).where(
        TradingSignal.user_id == user_id))).scalar() or 0)


async def journal(db: AsyncSession, user: User, limit: int = 100) -> dict:
    rows = list((await db.execute(
        select(TradingSignal).where(TradingSignal.user_id == user.id)
        .order_by(TradingSignal.received_at.desc()).limit(min(limit, 500)))).scalars())
    hook = (await db.execute(select(TradingWebhook).where(
        TradingWebhook.user_id == user.id,
        TradingWebhook.revoked_at.is_(None)))).scalars().first()
    return {
        "disclaimer": DISCLAIMER,
        "settings": settings_for(user),
        "endpoint": {"configured": hook is not None,
                     "selector": hook.selector if hook else None,
                     "signal_count": hook.signal_count if hook else 0,
                     "last_used_at": hook.last_used_at if hook else None},
        "signals": [serialize(r) for r in rows],
        "total": await signal_count(db, user.id),
    }
