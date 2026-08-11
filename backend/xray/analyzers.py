"""X-Ray analyzers (§42-49). Deterministic first; conservative estimates; evidence-backed.

Each analyzer returns finding dicts:
  {type, title, description, evidence, confidence, value_type, estimated_value_cents,
   estimated_time_minutes, verified, recommended_action, risk_level, source_references}
Findings never invent money (§120): no matching evidence → no finding.
"""
from __future__ import annotations

import re
from collections import defaultdict

from backend.xray.ingest import CalEvent, EmailMeta

_AMOUNT = re.compile(r"(?:€|EUR\s?|\$|USD\s?)\s?([0-9][0-9.,]*)", re.IGNORECASE)
_INVOICE = re.compile(r"\binvoice|receivable|payment (?:status|missing|overdue)|unpaid\b", re.IGNORECASE)
_OVERDUE = re.compile(r"\boverdue|never received|not (?:been )?paid|unpaid|missing payment|due \d+ days ago\b",
                      re.IGNORECASE)
_LEAD_INTENT = re.compile(
    r"\bsend (?:a |the )?proposal|pricing|what would .{0,40}cost|monthly plan|we'?d pay|"
    r"interested in|quote\b", re.IGNORECASE)
_COMMITMENT = re.compile(
    r"\bi(?:'|’)?ll (?:send|follow up|get back|call|prepare|share|reconnect)|"
    r"let'?s reconnect|i will (?:send|follow|get back|call)\b", re.IGNORECASE)
_SCHEDULING = re.compile(r"\bwhat times? work|happy to jump on|find a time|schedule a call|meeting time\b",
                         re.IGNORECASE)

CATEGORY_KEYWORDS = {
    "sales": ["sales", "prospect", "lead", "demo", "pitch", "proposal", "outreach"],
    "product": ["build", "product", "deep work", "code", "develop", "design", "ship"],
    "content": ["content", "blog", "post", "newsletter", "video", "podcast"],
    "admin": ["ops", "sync", "admin", "statement", "reconcile", "bookkeeping", "invoice"],
}


def parse_amount_cents(text: str) -> int | None:
    m = _AMOUNT.search(text)
    if not m:
        return None
    raw = m.group(1).rstrip(".,")
    # European formats: 2.400 or 2,400 → 2400; 2.400,50 → 2400.50
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rindex(",") > raw.rindex(".") else raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        raw = raw.replace(",", "") if len(parts[-1]) == 3 else raw.replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts[-1]) == 3:
            raw = raw.replace(".", "")
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return None


def _ref(email: EmailMeta) -> dict:
    return {"source": "email", "id": email.id, "subject": email.subject,
            "date": email.date.isoformat()}


def found_money(emails: list[EmailMeta]) -> list[dict]:
    """Verified recoverable money: explicit invoice/receivable evidence only (§42)."""
    findings = []
    for e in emails:
        text = f"{e.subject} {e.snippet}"
        if not _INVOICE.search(text):
            continue
        amount = parse_amount_cents(text)
        if amount is None:
            continue
        overdue = bool(_OVERDUE.search(text))
        if not overdue:
            continue
        who = e.recipient if e.is_sent else e.sender
        findings.append({
            "type": "found_money",
            "title": f"Unpaid/overdue invoice ≈ €{amount / 100:,.0f}",
            "description": f"Evidence of an unpaid or missing invoice in correspondence with {who}.",
            "evidence": [e.snippet[:300]],
            "confidence": 0.85,
            "value_type": "money",
            "estimated_value_cents": amount,
            "verified": True,
            "recommended_action": f"Follow up on the invoice with {who} and confirm payment.",
            "risk_level": 1,
            "source_references": [_ref(e)],
        })
    return findings


def estimated_opportunity(emails: list[EmailMeta]) -> list[dict]:
    """Warm leads without a later reply from the user (§43). Estimated, never verified."""
    findings = []
    sent_to: dict[str, list[EmailMeta]] = defaultdict(list)
    for e in emails:
        if e.is_sent:
            sent_to[_domain(e.recipient)].append(e)
    for e in emails:
        if e.is_sent:
            continue
        text = f"{e.subject} {e.snippet}"
        if not _LEAD_INTENT.search(text):
            continue
        later_replies = [s for s in sent_to[_domain(e.sender)] if s.date > e.date]
        if later_replies:
            continue
        amount = parse_amount_cents(text)
        findings.append({
            "type": "estimated_opportunity",
            "title": f"Warm lead never answered: {_domain(e.sender)}",
            "description": f"A prospect showed commercial intent ({e.subject!r}) and appears to have "
                           "received no reply.",
            "evidence": [e.snippet[:300]],
            "confidence": 0.6,
            "value_type": "money",
            "estimated_value_cents": amount,  # may be None: pipeline value unknown
            "verified": False,
            "recommended_action": f"Reply to {e.sender} — draft a follow-up referencing their request.",
            "risk_level": 1,
            "source_references": [_ref(e)],
        })
    return findings


def lost_commitments(emails: list[EmailMeta]) -> list[dict]:
    """User promises with no later sent message to the same counterparty (§46)."""
    findings = []
    sent = [e for e in emails if e.is_sent]
    for e in sent:
        text = f"{e.subject} {e.snippet}"
        m = _COMMITMENT.search(text)
        if not m:
            continue
        later = [s for s in sent if s.date > e.date and _domain(s.recipient) == _domain(e.recipient)]
        if later:
            continue
        findings.append({
            "type": "lost_commitment",
            "title": f"Possibly unkept promise to {_domain(e.recipient)}",
            "description": f"You wrote “{m.group(0)}…” and no later message to this contact was found.",
            "evidence": [e.snippet[:300]],
            "confidence": 0.55,
            "value_type": None,
            "verified": False,
            "recommended_action": f"Check whether you delivered what you promised to {e.recipient}.",
            "risk_level": 1,
            "source_references": [_ref(e)],
        })
    return findings


def found_time(emails: list[EmailMeta], events: list[CalEvent], horizon_days: int) -> list[dict]:
    """Conservative recoverable-time estimates (§44)."""
    findings = []
    # repetitive emails by sender+similar subject
    groups: dict[tuple[str, str], list[EmailMeta]] = defaultdict(list)
    for e in emails:
        if e.is_sent:
            continue
        key = (_domain(e.sender), _normalize_subject(e.subject))
        groups[key].append(e)
    for (sender, subject), msgs in groups.items():
        if len(msgs) < 4:
            continue
        minutes = len(msgs) * 4  # conservative: 4 min handling per repetitive email
        findings.append({
            "type": "found_time",
            "title": f"Repetitive email: “{subject}” from {sender} ({len(msgs)}× in {horizon_days}d)",
            "description": "Recurring near-identical emails suggest a filter/automation could handle them.",
            "evidence": [msgs[0].snippet[:200]],
            "confidence": 0.7,
            "value_type": "time",
            "estimated_time_minutes": minutes,
            "verified": False,
            "recommended_action": "Create a rule or let your crew triage these automatically.",
            "risk_level": 0,
            "source_references": [_ref(m) for m in msgs[:5]],
        })
    # scheduling ping-pong
    sched = [e for e in emails if not e.is_sent and _SCHEDULING.search(f"{e.subject} {e.snippet}")]
    if len(sched) >= 3:
        findings.append({
            "type": "found_time",
            "title": f"Manual meeting scheduling ({len(sched)} threads)",
            "description": "Back-and-forth scheduling emails could be replaced by a booking link "
                           "or calendar automation.",
            "evidence": [sched[0].snippet[:200]],
            "confidence": 0.65,
            "value_type": "time",
            "estimated_time_minutes": len(sched) * 8,
            "verified": False,
            "recommended_action": "Adopt a scheduling link; your crew can propose times automatically.",
            "risk_level": 0,
            "source_references": [_ref(m) for m in sched[:5]],
        })
    # recurring low-value meetings (same summary ≥4×, ≤1 attendee)
    recurring: dict[str, list[CalEvent]] = defaultdict(list)
    for ev in events:
        recurring[ev.summary.strip().lower()].append(ev)
    for summary, evs in recurring.items():
        if len(evs) < 4 or not summary:
            continue
        if max(len(ev.attendees) for ev in evs) > 1:
            continue
        total_min = sum(ev.minutes for ev in evs)
        if total_min < 120:
            continue
        findings.append({
            "type": "found_time",
            "title": f"Recurring meeting “{evs[0].summary}” — {total_min // 60}h in {horizon_days}d",
            "description": "A recurring 1:1/solo meeting block with unclear output. Consider shortening, "
                           "batching or replacing with an async update.",
            "evidence": [f"{len(evs)} occurrences, {total_min} minutes total"],
            "confidence": 0.5,
            "value_type": "time",
            "estimated_time_minutes": total_min // 2,  # conservative: assume half is recoverable
            "verified": False,
            "recommended_action": "Halve the cadence or convert to an async written update.",
            "risk_level": 1,
            "source_references": [{"source": "calendar", "id": ev.id} for ev in evs[:5]],
        })
    return findings


def goal_drift(events: list[CalEvent], stated_priority: str | None) -> list[dict]:
    """Declared priorities vs observed calendar allocation (§45)."""
    if not events:
        return []
    minutes: dict[str, int] = defaultdict(int)
    for ev in events:
        cat = _categorize(ev.summary)
        minutes[cat] += ev.minutes
    total = sum(minutes.values())
    if total == 0:
        return []
    allocation = {k: round(v * 100 / total) for k, v in sorted(minutes.items(), key=lambda kv: -kv[1])}
    finding = {
        "type": "goal_drift",
        "title": "Observed time allocation vs stated priorities",
        "description": "Calendar categorization is approximate (keyword-based) — treat percentages "
                       "as indicative, not exact.",
        "evidence": [f"{k}: {v}%" for k, v in allocation.items()],
        "confidence": 0.5,
        "value_type": None,
        "verified": False,
        "recommended_action": None,
        "risk_level": 0,
        "source_references": [],
        "extra": {"allocation": allocation, "stated_priority": stated_priority},
    }
    if stated_priority:
        top = next(iter(allocation), None)
        stated_share = allocation.get(stated_priority.lower(), 0)
        if top and top != stated_priority.lower() and stated_share < 30:
            finding["title"] = (f"Goal drift: stated priority “{stated_priority}” gets "
                                f"{stated_share}% of scheduled time")
            finding["description"] = (
                f"Most scheduled time goes to “{top}” ({allocation[top]}%), while the stated "
                f"priority “{stated_priority}” gets {stated_share}%. "
                "Categorization is keyword-based and approximate."
            )
            finding["confidence"] = 0.6
            finding["recommended_action"] = (
                f"Reallocate 2–3 weekly blocks toward {stated_priority}, or update the stated priority."
            )
    return [finding]


def automatable_work(emails: list[EmailMeta]) -> list[dict]:
    """Repetitive workflows the crew could take over (§48)."""
    findings = []
    inbound = [e for e in emails if not e.is_sent]
    sched_count = sum(1 for e in inbound if _SCHEDULING.search(f"{e.subject} {e.snippet}"))
    if sched_count >= 3:
        findings.append({
            "type": "automatable_work",
            "title": "Meeting scheduling could be automated",
            "description": f"{sched_count} scheduling threads in the horizon. Your crew can propose times "
                           "from your calendar automatically (calendar.write permission required).",
            "evidence": [f"{sched_count} scheduling emails"],
            "confidence": 0.7,
            "value_type": "time",
            "estimated_time_minutes": sched_count * 8,
            "verified": False,
            "recommended_action": "Enable Calendar write for your crew and turn on Meeting Prep autopilot.",
            "risk_level": 2,
            "source_references": [],
        })
    admin = [e for e in inbound if _categorize(e.subject + " " + e.snippet) == "admin"]
    if len(admin) >= 5:
        findings.append({
            "type": "automatable_work",
            "title": "Recurring admin email could be triaged automatically",
            "description": f"{len(admin)} recurring administrative emails (statements, reconciliation, "
                           "notifications) could be auto-filed with a weekly digest.",
            "evidence": [admin[0].snippet[:200]],
            "confidence": 0.65,
            "value_type": "time",
            "estimated_time_minutes": len(admin) * 3,
            "verified": False,
            "recommended_action": "Enable Inbox Triage autopilot (analyze/draft only by default).",
            "risk_level": 1,
            "source_references": [_ref(m) for m in admin[:5]],
        })
    return findings


def shadow_backtest(findings: list[dict], horizon_days: int) -> list[dict]:
    """RETROSPECTIVE SIMULATION (§49): where the crew would likely have intervened."""
    counts = {
        "revenue follow-ups": sum(1 for f in findings if f["type"] in ("found_money", "estimated_opportunity")),
        "commitment reminders": sum(1 for f in findings if f["type"] == "lost_commitment"),
        "calendar reallocations": sum(1 for f in findings if f["type"] == "goal_drift"),
        "automation take-overs": sum(1 for f in findings if f["type"] == "automatable_work"),
    }
    total = sum(counts.values())
    if total == 0:
        return []
    return [{
        "type": "shadow_backtest",
        "title": f"RETROSPECTIVE SIMULATION: ~{total} likely interventions in {horizon_days} days",
        "description": "If your Moseisley.sh crew had been active, it would likely have intervened here. "
                       "These actions did NOT occur — this is a simulation.",
        "evidence": [f"{v} {k}" for k, v in counts.items() if v],
        "confidence": 0.5,
        "value_type": None,
        "verified": False,
        "recommended_action": "Enable the Autopilot Pack to catch these going forward.",
        "risk_level": 0,
        "source_references": [],
        "extra": {"simulation": True, "counts": counts},
    }]


def _domain(address: str) -> str:
    m = re.search(r"@([A-Za-z0-9.-]+)", address)
    return m.group(1).lower() if m else address.lower()


def _normalize_subject(subject: str) -> str:
    s = re.sub(r"^(re|fwd?):\s*", "", subject.strip(), flags=re.IGNORECASE)
    return re.sub(r"\d+", "#", s).lower()


def _categorize(text: str) -> str:
    t = text.lower()
    best, best_hits = "other", 0
    for cat, words in CATEGORY_KEYWORDS.items():
        hits = sum(1 for w in words if w in t)
        if hits > best_hits:
            best, best_hits = cat, hits
    return best
