"""X-Ray ingestion (§41, §51): privacy-conscious normalization of historical data.

Only metadata + snippets are held in memory during analysis; full bodies are never
persisted. Findings keep source references (message ids), not content dumps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations import broker


@dataclass
class EmailMeta:
    id: str
    sender: str
    recipient: str
    subject: str
    snippet: str
    date: datetime
    is_sent: bool  # sent by the user


@dataclass
class CalEvent:
    id: str
    summary: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))


def _parse_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return None


def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def normalize_message(msg: dict, own_addresses: set[str]) -> EmailMeta | None:
    ts = msg.get("internalDate")
    if ts is None:
        return None
    date = datetime.fromtimestamp(int(ts) / 1000, tz=UTC)
    sender = _header(msg, "From").lower()
    recipient = _header(msg, "To").lower()
    is_sent = "SENT" in (msg.get("labelIds") or []) or any(a in sender for a in own_addresses)
    return EmailMeta(
        id=msg.get("id", ""), sender=sender, recipient=recipient,
        subject=_header(msg, "Subject"), snippet=msg.get("snippet", ""),
        date=date, is_sent=is_sent,
    )


def normalize_event(ev: dict) -> CalEvent | None:
    start = _parse_dt((ev.get("start") or {}).get("dateTime", ""))
    end = _parse_dt((ev.get("end") or {}).get("dateTime", ""))
    if start is None or end is None:
        return None
    return CalEvent(
        id=ev.get("id", ""), summary=ev.get("summary", ""),
        start=start, end=end,
        attendees=[a.get("email", "") for a in ev.get("attendees", [])],
    )


async def ingest(
    db: AsyncSession, user_id: str, horizon_days: int
) -> tuple[list[EmailMeta], list[CalEvent]]:
    cutoff = datetime.now(UTC) - timedelta(days=horizon_days)
    emails: list[EmailMeta] = []
    events: list[CalEvent] = []
    own_addresses = {"me@"}  # own-address detection: SENT label is primary; this is a fallback

    conn = await broker.find_connection_for_capability(db, user_id, "gmail.read")
    if conn is not None:
        if conn.integration_type == "demo":
            data = await broker.invoke(db, user_id, "gmail.read", "gmail.get_all_messages",
                                       actor_type="system")
            raw = data.get("messages", [])
        else:
            listing = await broker.invoke(
                db, user_id, "gmail.read", "gmail.list_messages",
                {"q": f"newer_than:{horizon_days}d", "max_results": 200}, actor_type="system",
            )
            raw = []
            for ref in listing.get("messages", [])[:200]:
                raw.append(await broker.invoke(db, user_id, "gmail.read", "gmail.get_message",
                                               {"id": ref["id"]}, actor_type="system"))
        for msg in raw:
            meta = normalize_message(msg, own_addresses)
            if meta and meta.date >= cutoff:
                emails.append(meta)

    cal_conn = await broker.find_connection_for_capability(db, user_id, "calendar.read")
    if cal_conn is not None:
        data = await broker.invoke(
            db, user_id, "calendar.read", "calendar.list_events",
            {"timeMin": cutoff.isoformat(), "max_results": 500}, actor_type="system",
        )
        for ev in data.get("items", []):
            ce = normalize_event(ev)
            if ce and ce.start >= cutoff:
                events.append(ce)

    emails.sort(key=lambda e: e.date)
    events.sort(key=lambda e: e.start)
    return emails, events
