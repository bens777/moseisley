"""Demo integration (§110): serves clearly-synthetic Gmail/Calendar-shaped fixture data
so X-Ray, Autopilot and tests run end-to-end without real Google credentials.

All fixture data is deterministic per connection and marked synthetic.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.integrations.base import IntegrationAdapter, IntegrationError


def _days_ago(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


def demo_messages() -> list[dict]:
    """Synthetic 90-day inbox for a solo founder. [SYNTHETIC DEMO DATA]"""
    msgs = [
        # verified money: overdue invoice
        dict(id="m-inv-001", days=35, frm="anna@brightlabs.example", to="me@founder.example",
             subject="Invoice #2041 — payment status?",
             snippet="Hi, just confirming you'll re-send invoice #2041 for €2,400 — we never received it."),
        dict(id="m-inv-002", days=12, frm="me@founder.example", to="billing@nordicops.example",
             subject="Invoice #2055 (€1,800) overdue",
             snippet="Following up on invoice #2055 for €1,800, due 30 days ago. Could you check payment status?"),
        # warm lead gone stale
        dict(id="m-lead-001", days=28, frm="jonas@ferrytech.example", to="me@founder.example",
             subject="Re: automation pilot",
             snippet="This looks great. Can you send a proposal with pricing for the pilot?"),
        dict(id="m-lead-002", days=55, frm="maria@quietclinic.example", to="me@founder.example",
             subject="AI receptionist demo",
             snippet="We'd pay for something like this — what would a monthly plan cost for 2 locations?"),
        # commitment made by user, unresolved
        dict(id="m-comm-001", days=21, frm="me@founder.example", to="jonas@ferrytech.example",
             subject="Re: automation pilot",
             snippet="Great — I'll send the proposal by Friday."),
        dict(id="m-comm-002", days=40, frm="me@founder.example", to="peter@oldclient.example",
             subject="Re: catch up",
             snippet="I'll get back to you next week with the maintenance quote."),
        # repetitive admin
        *[dict(id=f"m-rep-{i:03d}", days=3 + i * 7, frm="noreply@bankstatements.example",
               to="me@founder.example", subject=f"Weekly statement week {i}",
               snippet="Your weekly account statement is ready. Download and reconcile.")
          for i in range(1, 11)],
        *[dict(id=f"m-sched-{i:03d}", days=2 + i * 6, frm=f"client{i}@various.example",
               to="me@founder.example", subject="Meeting time?",
               snippet="What times work for you next week? Happy to jump on a 30 min call.")
          for i in range(1, 9)],
        # noise
        *[dict(id=f"m-news-{i:03d}", days=1 + i * 4, frm="digest@technews.example",
               to="me@founder.example", subject=f"Daily digest #{i}",
               snippet="Top 10 stories in AI today...")
          for i in range(1, 16)],
    ]
    out = []
    for m in msgs:
        dt = _days_ago(m["days"])
        out.append({
            "id": m["id"],
            "internalDate": str(int(dt.timestamp() * 1000)),
            "payload": {"headers": [
                {"name": "From", "value": m["frm"]},
                {"name": "To", "value": m["to"]},
                {"name": "Subject", "value": m["subject"]},
                {"name": "Date", "value": dt.strftime("%a, %d %b %Y %H:%M:%S +0000")},
            ]},
            "snippet": m["snippet"],
            "labelIds": ["INBOX"] + (["SENT"] if m["frm"].startswith("me@") else []),
        })
    return out


def demo_events() -> list[dict]:
    events = []
    # fragmented recurring low-value meetings + product-heavy allocation
    for week in range(0, 12):
        base = _days_ago(84 - week * 7)
        events.append({
            "id": f"e-sync-{week}", "summary": "Weekly ops sync",
            "start": {"dateTime": (base.replace(hour=9)).isoformat()},
            "end": {"dateTime": (base.replace(hour=10)).isoformat()},
            "attendees": [{"email": "va@founder.example"}],
        })
        for d in range(3):
            day = base + timedelta(days=d + 1)
            events.append({
                "id": f"e-build-{week}-{d}", "summary": "Deep work: product build",
                "start": {"dateTime": day.replace(hour=10).isoformat()},
                "end": {"dateTime": day.replace(hour=14).isoformat()},
            })
        if week % 2 == 0:
            day = base + timedelta(days=4)
            events.append({
                "id": f"e-sales-{week}", "summary": "Sales call — prospect",
                "start": {"dateTime": day.replace(hour=15).isoformat()},
                "end": {"dateTime": day.replace(hour=16).isoformat()},
            })
    return events


class DemoAdapter(IntegrationAdapter):
    integration_type = "demo"

    def capabilities(self) -> list[str]:
        return ["gmail.read", "calendar.read", "demo.read"]

    async def health_check(self) -> bool:
        return True

    async def read(self, operation: str, params: dict) -> dict:
        if operation == "gmail.list_messages":
            msgs = demo_messages()
            return {"messages": [{"id": m["id"]} for m in msgs], "resultSizeEstimate": len(msgs),
                    "synthetic": True}
        if operation == "gmail.get_message":
            for m in demo_messages():
                if m["id"] == params.get("id"):
                    return {**m, "synthetic": True}
            raise IntegrationError("message not found")
        if operation == "gmail.get_all_messages":
            return {"messages": demo_messages(), "synthetic": True}
        if operation == "calendar.list_events":
            return {"items": demo_events(), "synthetic": True}
        raise IntegrationError(f"unknown read operation: {operation}")

    async def execute(self, operation: str, params: dict) -> dict:
        raise IntegrationError("demo adapter is read-only")
