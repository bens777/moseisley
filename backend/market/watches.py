"""Market Watch execution (third pass §32-§38, §64).

A Market Watch is an Instruction (kind='market_watch') whose config_json holds:
  topics: [str], queries: [str], accounts: ["@handle"], excluded_topics: [str],
  lookback_days: int, instruction: str, heartbeat: bool (send even without
  material change)

Execution: Radar runs an X live search through the instrumented registry path
(current xAI Agent Tools API), asks for an evidence-based structured brief,
stores a MarketReport, and delivers to Telegram per the delivery config.

Sentiment honesty (§36): only positive|mixed|negative|no_material_change —
never invented percentages. sample_json stays empty unless a defined sample
was actually classified.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import CrewRun, Instruction, MarketReport, User
from backend.ledger import service as ledger
from backend.ops import instructions as instructions_svc
from backend.providers import registry

SENTIMENTS = ("positive", "mixed", "negative", "no_material_change")

BRIEF_PROMPT = """You are Radar, a market intelligence analyst. Using X search, review recent
activity for the topics, queries and accounts below. Report ONLY what the
search actually surfaced — never invent posts, numbers or sentiment.

TOPICS: {topics}
EXTRA QUERIES: {queries}
ACCOUNTS: {accounts}
EXCLUDE: {excluded}
USER INSTRUCTION: {instruction}

Return STRICT JSON only:
{{
  "material_changes": [{{"title": "...", "why_it_matters": "...", "evidence": "..."}}],
  "sentiment": "positive|mixed|negative|no_material_change",
  "sentiment_basis": "one sentence naming the actual evidence",
  "narratives": ["..."],
  "important_posts": ["short description with @author"],
  "emerging_topics": ["..."],
  "pain_points": ["..."],
  "competitor_movement": ["..."],
  "opportunities": ["..."],
  "threats": ["..."]
}}
Empty arrays are correct when nothing material was found; then sentiment MUST be
"no_material_change". Never output percentages for sentiment."""


def _parse_brief(text: str) -> dict:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"material_changes": [], "sentiment": "no_material_change",
                "sentiment_basis": "model returned unparseable output",
                "parse_error": True, "raw": text[:2000]}
    if data.get("sentiment") not in SENTIMENTS:
        data["sentiment"] = "no_material_change"
    data.setdefault("material_changes", [])
    return data


async def run_watch(db: AsyncSession, user: User, instruction: Instruction) -> dict:
    config = instruction.config_json or {}
    lookback = int(config.get("lookback_days") or 1)
    now = datetime.now(UTC)
    query = {"from_date": (now - timedelta(days=lookback)).date().isoformat(),
             "to_date": now.date().isoformat(),
             "topics": config.get("topics") or [],
             "queries": config.get("queries") or [],
             "accounts": config.get("accounts") or []}

    run = CrewRun(user_id=user.id, crew_role=instruction.assigned_role or "radar",
                  runtime="native", task_summary=f"market watch: {instruction.name}"[:500])
    db.add(run)
    await db.flush()

    prompt = BRIEF_PROMPT.format(
        topics=", ".join(query["topics"]) or "(none)",
        queries=", ".join(query["queries"]) or "(none)",
        accounts=", ".join(query["accounts"]) or "(any)",
        excluded=", ".join(config.get("excluded_topics") or []) or "(none)",
        instruction=config.get("instruction") or "Report only material changes.")

    try:
        search = await registry.generate_with_x_search(
            db, user.id, prompt,
            allowed_x_handles=query["accounts"] or None,
            from_date=query["from_date"], to_date=query["to_date"],
            crew_role=run.crew_role, run_id=run.id,
            project_id=instruction.project_id)
    except Exception as e:
        run.status = "failed"
        run.result_summary = str(e)[:500]
        run.finished_at = datetime.now(UTC)
        await db.flush()
        await instructions_svc.record_run_result(
            db, instruction, {"error": str(e)[:300]}, status="error")
        raise

    brief = _parse_brief(search["text"])
    material = bool(brief.get("material_changes"))
    report = MarketReport(
        user_id=user.id, instruction_id=instruction.id, crew_run_id=run.id,
        status="completed" if material else "no_material_change",
        sentiment=brief.get("sentiment"),
        summary_json=brief, sources_json=search["citations"],
        sample_json={},  # no quantified sample methodology in V0.1 — stays honest
        query_json={**query, "mock": search.get("mock", False)})
    db.add(report)
    run.status = "completed"
    run.provider = "mock" if search.get("mock") else "xai"
    run.actual_model = search.get("model")
    run.result_summary = json.dumps({"sentiment": brief.get("sentiment"),
                                     "material_changes": len(brief.get("material_changes", []))})
    run.finished_at = datetime.now(UTC)
    await db.flush()
    await ledger.record(db, user.id, "market_report_created", actor_type="system",
                        entity_type="market_report", entity_id=report.id,
                        payload={"instruction": instruction.name,
                                 "sentiment": brief.get("sentiment"),
                                 "material_changes": len(brief.get("material_changes", []))})

    delivered: list[str] = []
    wants_telegram = "telegram" in (instruction.delivery_json or [])
    heartbeat = bool(config.get("heartbeat"))
    if wants_telegram and (material or heartbeat):
        if await _deliver_telegram(db, user, instruction, report, brief):
            delivered.append("telegram")
    report.delivered_json = delivered
    await db.flush()

    result = {"report_id": report.id, "sentiment": brief.get("sentiment"),
              "material_changes": len(brief.get("material_changes", [])),
              "sources": len(search["citations"]), "delivered": delivered}
    await instructions_svc.record_run_result(db, instruction, result)
    return result


def format_brief(instruction: Instruction, report_summary: dict,
                 sources: list[str]) -> str:
    changes = report_summary.get("material_changes") or []
    lines = [f"*{instruction.name.upper()}*", ""]
    if changes:
        lines.append(f"{len(changes)} MATERIAL CHANGE{'S' if len(changes) != 1 else ''}")
        for i, c in enumerate(changes[:5], 1):
            lines.append(f"{i}. {c.get('title', '')} — {c.get('why_it_matters', '')}")
    else:
        lines.append("NO MATERIAL CHANGE")
    lines += ["", f"SENTIMENT: {report_summary.get('sentiment', 'n/a').replace('_', ' ')}"]
    basis = report_summary.get("sentiment_basis")
    if basis:
        lines.append(f"_{basis}_")
    for label, key in (("OPPORTUNITY", "opportunities"), ("THREAT", "threats")):
        vals = report_summary.get(key) or []
        if vals:
            lines += ["", f"{label}: {vals[0]}"]
    if sources:
        lines += ["", "SOURCES:"] + [f"- {s}" for s in sources[:5]]
    return "\n".join(lines)


async def _deliver_telegram(db: AsyncSession, user: User, instruction: Instruction,
                            report: MarketReport, brief: dict) -> bool:
    from sqlalchemy import select

    from backend.api.routes.telegram import get_gateway
    from backend.core.models import TelegramBinding

    gateway = get_gateway()
    if gateway is None:
        return False
    binding = (await db.execute(select(TelegramBinding).where(
        TelegramBinding.user_id == user.id))).scalar_one_or_none()
    if binding is None:
        return False
    text = format_brief(instruction, brief, report.sources_json or [])
    try:
        await gateway.client.send_message(binding.telegram_chat_id, text)
    except Exception:
        return False
    await ledger.record(db, user.id, "market_brief_delivered", actor_type="system",
                        entity_type="market_report", entity_id=report.id,
                        payload={"channel": "telegram"})
    return True
