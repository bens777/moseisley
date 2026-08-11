"""Third pass: AI Manager (§11-§17, §60) — one conversation, page context,
draft → explicit save, deterministic persistence."""
from __future__ import annotations

import json

from tests.conftest import setup_mock_provider

DRAFT_TOOL_CALL = json.dumps({
    "action": "tool", "tool": "instructions.draft",
    "args": {
        "name": "AI agent market watch", "kind": "market_watch",
        "config": {"topics": ["OpenClaw", "Claude Code"], "accounts": ["@example1"],
                   "instruction": "Report only meaningful changes."},
        "schedule": {"frequency": "daily", "time": "08:00", "timezone": "Europe/Paris"},
        "delivery": ["telegram"], "assigned_role": "radar",
    },
})
DRAFT_FOLLOWUP = json.dumps({
    "action": "reply",
    "text": "Draft ready: daily watch on OpenClaw & Claude Code at 08:00, sent to Telegram. Save it?",
})
SAVE_TOOL_CALL = json.dumps({"action": "tool", "tool": "instructions.save", "args": {}})
SAVE_FOLLOWUP = json.dumps({"action": "reply", "text": "Saved — the Radar runs it daily at 08:00."})


async def test_manager_draft_then_explicit_save(client, auth):
    """§60: conversation → structured draft → JSON visible → explicit save →
    watch exists → scheduler sees it → ledger records it."""
    await setup_mock_provider(client, auth, responses={
        "watch openclaw": DRAFT_TOOL_CALL,
        "TOOL RESULT for instructions.draft": DRAFT_FOLLOWUP,
        "save it": SAVE_TOOL_CALL,
        "TOOL RESULT for instructions.save": SAVE_FOLLOWUP,
    })

    # 1. user asks in natural language, from the Market page
    resp = await client.post("/api/manager/message", headers=auth, json={
        "text": "Watch OpenClaw and Claude Code every morning and send meaningful changes to Telegram.",
        "page_context": {"page": "market"},
    })
    body = resp.json()
    assert "Draft ready" in body["reply"]
    # structured draft visible, NOT saved
    assert body["draft"]["kind"] == "market_watch"
    assert body["draft"]["schedule"]["time"] == "08:00"
    assert (await client.get("/api/instructions", headers=auth)).json() == []

    # 2. explicit conversational confirmation → saved deterministically
    resp = await client.post("/api/manager/message", headers=auth, json={"text": "save it"})
    assert "Saved" in resp.json()["reply"]

    instructions = (await client.get("/api/instructions", headers=auth)).json()
    assert len(instructions) == 1
    ins = instructions[0]
    assert ins["created_by"] == "manager"
    assert ins["config"]["topics"] == ["OpenClaw", "Claude Code"]
    assert ins["next_run_at"] is not None  # scheduler sees it

    acts = (await client.get("/api/activity", headers=auth)).json()
    types = [e["event_type"] for e in acts]
    assert "manager_draft_created" in types and "manager_draft_saved" in types

    # 3. one persistent conversation — history retained
    msgs = (await client.get("/api/manager/messages", headers=auth)).json()
    assert len(msgs) >= 4  # 2 user + 2 assistant


async def test_manager_ui_save_button_path(client, auth):
    """Draft staged conversationally can be applied by the deterministic
    SAVE endpoint (UI button) instead of a chat confirmation."""
    await setup_mock_provider(client, auth, responses={
        "watch openclaw": DRAFT_TOOL_CALL,
        "TOOL RESULT for instructions.draft": DRAFT_FOLLOWUP,
    })
    await client.post("/api/manager/message", headers=auth, json={
        "text": "watch openclaw please"})
    assert (await client.get("/api/manager/draft", headers=auth)).json()["draft"]["name"]

    result = (await client.post("/api/manager/draft/apply", headers=auth)).json()
    assert result["saved"] is True
    assert (await client.get("/api/manager/draft", headers=auth)).json()["draft"] is None or \
           (await client.get("/api/manager/draft", headers=auth)).json()["draft"] == {}

    # applying again without a draft errors honestly
    assert "error" in (await client.post("/api/manager/draft/apply", headers=auth)).json()


async def test_manager_read_tools_and_orchestrator_guard(client, auth):
    """Manager can read real metrics; the plain orchestrator chat cannot stage
    drafts (manager-only tool)."""
    metrics_call = json.dumps({"action": "tool", "tool": "metrics.overview", "args": {}})
    metrics_reply = json.dumps({"action": "reply", "text": "Zero operations so far — fresh account."})
    await setup_mock_provider(client, auth, responses={
        "how much": metrics_call,
        "TOOL RESULT for metrics.overview": metrics_reply,
        "draft an automation": DRAFT_TOOL_CALL,
        "TOOL RESULT for instructions.draft": json.dumps(
            {"action": "reply", "text": "tool outcome received"}),
    })
    resp = await client.post("/api/manager/message", headers=auth, json={
        "text": "How much did the crew cost this week?"})
    assert "fresh account" in resp.json()["reply"]

    # same tool call from the ORCHESTRATOR chat is refused (manager-only)
    resp = await client.post("/api/chat/message", headers=auth, json={
        "text": "draft an automation"})
    assert resp.status_code == 200
    # no draft got staged through the orchestrator path
    assert (await client.get("/api/manager/draft", headers=auth)).json()["draft"] in (None, {})
