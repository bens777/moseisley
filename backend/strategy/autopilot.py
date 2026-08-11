"""Autopilot Pack (§52-57): default loops for the solo-founder ICP.

Default action ceiling is READ/ANALYZE/DRAFT (§57). Real external drafts (e.g. Gmail
drafts) are attempted through the Tool Broker and its policy boundary; when denied or
unavailable, the draft is stored internally as a Markdown document instead. Nothing
here sends email or spends money.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch
from backend.core.models import User
from backend.documents import service as documents
from backend.integrations import broker
from backend.ledger import service as ledger
from backend.policies.engine import PolicyDenied
from backend.providers import registry
from backend.providers.clients import ProviderError
from backend.providers.registry import LlmBudgetExceeded, NoProviderAvailable
from backend.xray import analyzers, ingest

logger = logging.getLogger("mychief.autopilot")

LOOPS = [
    "lost_lead_recovery",
    "follow_up",
    "commitment_tracker",
    "inbox_triage",
    "goal_drift",
]


@dataclass
class AutopilotResult:
    loop: str
    status: str  # completed | no_action
    summary: str
    drafts: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)


async def _llm_draft(db: AsyncSession, user_id: str, instruction: str, fallback: str) -> str:
    """Semantic drafting with a deterministic fallback when no LLM is available."""
    try:
        result = await registry.complete(db, user_id, "chat", [
            {"role": "system",
             "content": "You draft short, warm, professional follow-up emails for a solo founder. "
                        "Reply with the email body only — no subject, no commentary."},
            {"role": "user", "content": instruction},
        ], max_tokens=300)
        text = result.text.strip()
        if text and not text.startswith("[mock]"):
            return text
    except (NoProviderAvailable, LlmBudgetExceeded, ProviderError, killswitch.KillSwitchEngaged):
        pass
    return fallback


async def _store_draft(
    db: AsyncSession, user: User, loop: str, to: str, subject: str, body: str,
) -> dict:
    """Try a real Gmail draft through the policy boundary; fall back to an internal doc."""
    delivered_as = "internal_draft"
    try:
        await broker.invoke(db, user.id, "gmail.draft", "gmail.create_draft",
                            {"to": to, "subject": subject, "body": body},
                            actor_type="agent", actor_id=f"autopilot:{loop}")
        delivered_as = "gmail_draft"
    except (PolicyDenied, broker.BrokerError, killswitch.KillSwitchEngaged, Exception) as e:
        if not isinstance(e, (PolicyDenied, broker.BrokerError, killswitch.KillSwitchEngaged)):
            logger.warning("gmail draft failed (%s); storing internally", type(e).__name__)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = f"/drafts/{loop}-{ts}-{to.split('@')[0][:24]}.md"
    await documents.upsert_document(
        db, user.id, path,
        f"# Draft: {subject}\n\nTo: {to}\n\n---\n\n{body}\n",
        actor_type="system", metadata={"loop": loop, "to": to, "delivered_as": delivered_as},
    )
    await ledger.record(db, user.id, "autopilot_draft_created", actor_type="agent",
                        actor_id=f"autopilot:{loop}",
                        payload={"to": to, "subject": subject, "delivered_as": delivered_as,
                                 "path": path})
    return {"to": to, "subject": subject, "body": body, "delivered_as": delivered_as, "path": path}


async def lost_lead_recovery(db: AsyncSession, user: User) -> AutopilotResult:
    """§53: find stale commercial conversations, rank, prepare draft follow-ups."""
    emails, _ = await ingest.ingest(db, user.id, 90)
    leads = analyzers.estimated_opportunity(emails)
    if not leads:
        return AutopilotResult("lost_lead_recovery", "no_action", "No stale leads found.")
    leads.sort(key=lambda f: (f.get("estimated_value_cents") or 0, f["confidence"]), reverse=True)
    drafts = []
    for lead in leads[:3]:
        ref = lead["source_references"][0]
        to = next((e.sender for e in emails if e.id == ref["id"]), None)
        if not to:
            continue
        subject = f"Re: {ref.get('subject', 'our conversation')}"
        body = await _llm_draft(
            db, user.id,
            f"Prospect wrote: “{lead['evidence'][0]}”. We never replied. Draft a brief follow-up "
            "re-opening the conversation and proposing a concrete next step.",
            f"Hi,\n\nFollowing up on your message — apologies for the delay. "
            f"Your request is still very much on my radar and I'd love to pick this back up. "
            f"Would a short call this week work?\n\nBest,\n{user.display_name or 'me'}",
        )
        drafts.append(await _store_draft(db, user, "lost_lead_recovery", to, subject, body))
    return AutopilotResult(
        "lost_lead_recovery", "completed",
        f"{len(leads)} stale lead(s) found; {len(drafts)} follow-up draft(s) prepared.",
        drafts=drafts, items=[{"title": lead["title"], "confidence": lead["confidence"]} for lead in leads],
    )


async def follow_up(db: AsyncSession, user: User) -> AutopilotResult:
    """§54: conversations where a next action is implied but missing."""
    emails, _ = await ingest.ingest(db, user.id, 90)
    inbound = [e for e in emails if not e.is_sent]
    sent = [e for e in emails if e.is_sent]
    items = []
    for e in inbound:
        text = f"{e.subject} {e.snippet}".lower()
        if "?" not in e.snippet and "could you" not in text and "can you" not in text:
            continue
        replied = any(s.date > e.date and analyzers._domain(s.recipient) == analyzers._domain(e.sender)
                      for s in sent)
        if not replied:
            items.append({"from": e.sender, "subject": e.subject, "date": e.date.isoformat(),
                          "suggestion": "Reply or schedule a response."})
    if not items:
        return AutopilotResult("follow_up", "no_action", "No unanswered asks detected.")
    return AutopilotResult("follow_up", "completed",
                           f"{len(items)} conversation(s) appear to await your reply.", items=items[:10])


async def commitment_tracker(db: AsyncSession, user: User) -> AutopilotResult:
    """§55: track unresolved promises; surface only material items."""
    emails, _ = await ingest.ingest(db, user.id, 90)
    commitments = analyzers.lost_commitments(emails)
    material = [c for c in commitments if c["confidence"] >= 0.5]
    if not material:
        return AutopilotResult("commitment_tracker", "no_action", "No unresolved commitments detected.")
    return AutopilotResult(
        "commitment_tracker", "completed",
        f"{len(material)} possibly unkept commitment(s).",
        items=[{"title": c["title"], "action": c["recommended_action"]} for c in material],
    )


TRIAGE_BUCKETS = ["DECISION REQUIRED", "ACTION REQUIRED", "FYI", "LOW VALUE", "NOISE"]


def _triage_bucket(e: ingest.EmailMeta) -> str:
    text = f"{e.subject} {e.snippet}".lower()
    if any(w in text for w in ("approve", "sign", "decide", "confirm by", "deadline", "contract")):
        return "DECISION REQUIRED"
    if any(w in text for w in ("could you", "can you", "please send", "proposal", "pricing", "invoice", "?")):
        return "ACTION REQUIRED"
    if any(w in text for w in ("digest", "newsletter", "top 10", "unsubscribe")):
        return "NOISE"
    if any(w in text for w in ("statement", "receipt", "notification", "no-reply", "noreply")):
        return "LOW VALUE"
    return "FYI"


async def inbox_triage(db: AsyncSession, user: User) -> AutopilotResult:
    """§56: classify the inbox. Never deletes anything."""
    emails, _ = await ingest.ingest(db, user.id, 30)
    inbound = [e for e in emails if not e.is_sent]
    if not inbound:
        return AutopilotResult("inbox_triage", "no_action", "No recent inbound email.")
    buckets: dict[str, list[dict]] = {b: [] for b in TRIAGE_BUCKETS}
    for e in inbound:
        buckets[_triage_bucket(e)].append({"from": e.sender, "subject": e.subject})
    lines = ["# Inbox Triage", ""]
    for b in TRIAGE_BUCKETS:
        if buckets[b]:
            lines.append(f"## {b} ({len(buckets[b])})")
            lines += [f"- {m['subject']} — {m['from']}" for m in buckets[b][:15]]
            lines.append("")
    await documents.upsert_document(db, user.id, "/reports/inbox-triage.md",
                                    "\n".join(lines), actor_type="system")
    counts = {b: len(v) for b, v in buckets.items() if v}
    return AutopilotResult("inbox_triage", "completed",
                           "Inbox triaged: " + ", ".join(f"{v} {k}" for k, v in counts.items()),
                           items=[{"bucket": b, "count": len(v)} for b, v in buckets.items()])


async def goal_drift_loop(db: AsyncSession, user: User) -> AutopilotResult:
    from backend.core.models import Goal

    goal = (await db.execute(
        select(Goal).where(Goal.user_id == user.id, Goal.status == "active")
    )).scalars().first()
    stated = None
    if goal is not None and any(w in goal.metric.lower()
                                for w in ("income", "revenue", "customer", "sales", "mrr")):
        stated = "sales"
    _, events = await ingest.ingest(db, user.id, 30)
    findings = analyzers.goal_drift(events, stated)
    if not findings or "drift" not in findings[0]["title"].lower():
        return AutopilotResult("goal_drift", "no_action", "No material goal drift detected.")
    f = findings[0]
    return AutopilotResult("goal_drift", "completed", f["title"],
                           items=[{"description": f["description"],
                                   "action": f.get("recommended_action")}])


RUNNERS = {
    "lost_lead_recovery": lost_lead_recovery,
    "follow_up": follow_up,
    "commitment_tracker": commitment_tracker,
    "inbox_triage": inbox_triage,
    "goal_drift": goal_drift_loop,
}


async def run_loop(db: AsyncSession, user: User, loop: str) -> AutopilotResult:
    if loop not in RUNNERS:
        raise ValueError(f"unknown autopilot loop: {loop}")
    await killswitch.require_off(db, user.id, killswitch.PAUSE_ALL_AGENTS)
    return await RUNNERS[loop](db, user)
