"""Phase 3 acceptance (§118, §134-135): pairing, commands, text chat, voice transcription."""
from __future__ import annotations

import json

from backend.telegram.gateway import Gateway
from tests.conftest import auth_headers, setup_mock_provider
from tests.fake_telegram import FakeTelegramClient, make_text_update, make_voice_update

TG_USER = 111222333
TG_CHAT = 111222333

GOAL_JSON = json.dumps({
    "metric": "monthly_independent_income", "title": "€5,000/month independent income",
    "target": 5000, "unit": "EUR", "currency": "EUR", "deadline": None,
    "constraints": {}, "missing_critical": [],
})


async def link_telegram(client, auth, gateway, db):
    resp = await client.post("/api/telegram/pairing-code", headers=auth)
    code = resp.json()["code"]
    await gateway.process_update(db, make_text_update(TG_USER, TG_CHAT, f"/link {code}"))
    return code


async def test_pairing_flow(client, auth, db_session):
    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    # unlinked message → prompt to link
    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "hello"))
    assert "isn't linked" in fake.sent_messages[-1]["text"]

    await link_telegram(client, auth, gateway, db_session)
    assert "Linked" in fake.sent_messages[-1]["text"]

    binding = (await client.get("/api/telegram/binding", headers=auth)).json()
    assert binding["linked"] is True


async def test_pairing_code_single_use_and_invalid(client, auth, db_session):
    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    resp = await client.post("/api/telegram/pairing-code", headers=auth)
    code = resp.json()["code"]
    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, f"/link {code}"))
    # reuse by another telegram account must fail
    await gateway.process_update(db_session, make_text_update(999888, 999888, f"/link {code}"))
    assert "invalid or expired" in fake.sent_messages[-1]["text"]
    # garbage code
    await gateway.process_update(db_session, make_text_update(999888, 999888, "/link WRONG1"))
    assert "invalid or expired" in fake.sent_messages[-1]["text"]


async def test_text_chat_context_aware(client, auth, db_session):
    await setup_mock_provider(client, auth, {
        "5000": GOAL_JSON,
        "where am i": "You're tracking toward your €5,000/month goal.",
    })
    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    await link_telegram(client, auth, gateway, db_session)

    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "I want 5000 EUR monthly income"))
    assert "Goal locked in" in fake.sent_messages[-1]["text"]

    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "where am I compared with my goals?"))
    assert "5,000" in fake.sent_messages[-1]["text"]

    # canonical session shared with web chat
    messages = (await client.get("/api/chat/messages", headers=auth)).json()
    assert any(m["channel"] == "telegram" for m in messages)


async def test_commands(client, auth, db_session):
    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    await link_telegram(client, auth, gateway, db_session)

    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/status"))
    assert "Moseisley.sh status" in fake.sent_messages[-1]["text"]

    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/pause"))
    assert "paused" in fake.sent_messages[-1]["text"].lower()
    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "hello"))
    assert "paused" in fake.sent_messages[-1]["text"].lower()
    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/resume"))

    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/spending"))
    assert "OFF" in fake.sent_messages[-1]["text"]
    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/spending on"))
    assert "ENABLED" in fake.sent_messages[-1]["text"]

    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/agent"))
    assert "Native Agent" in fake.sent_messages[-1]["text"]

    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/voice on"))
    assert "ON" in fake.sent_messages[-1]["text"]


async def test_voice_note_transcription(client, auth, db_session):
    """§135: voice note → transcribe → goal flow; audio not persisted."""
    await setup_mock_provider(client, auth, {"5,000": GOAL_JSON, "5000": GOAL_JSON})
    # configure mock STT transcript
    await client.post("/api/providers", json={
        "provider": "mock", "api_key": "mock",
        "configuration": {
            "transcript": "I want to increase my independent income to 5000 EUR per month.",
            "responses": {"5000": GOAL_JSON},
        },
    }, headers=auth)

    fake = FakeTelegramClient()
    fake.files["voice42"] = b"fake-ogg-bytes"
    gateway = Gateway(fake)
    await link_telegram(client, auth, gateway, db_session)

    await gateway.process_update(db_session, make_voice_update(TG_USER, TG_CHAT, "voice42"))
    assert "Goal locked in" in fake.sent_messages[-1]["text"]

    goals = (await client.get("/api/goals", headers=auth)).json()
    assert goals and goals[0]["target_value"] == 5000


async def test_voice_reply_mode(client, auth, db_session):
    await setup_mock_provider(client, auth, {"ping": "pong"})
    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    await link_telegram(client, auth, gateway, db_session)
    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "/voice on"))
    await gateway.process_update(db_session, make_text_update(TG_USER, TG_CHAT, "ping"))
    assert fake.sent_voices and fake.sent_voices[-1]["voice"].startswith(b"OGG-MOCK:")
    assert fake.sent_messages[-1]["text"] == "pong"


async def test_webhook_endpoint_secret(client, db_session, monkeypatch):
    from backend.api.routes import telegram as tg_routes
    from backend.core.config import get_settings

    fake = FakeTelegramClient()
    tg_routes.set_gateway(Gateway(fake))
    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", "hook-secret")
    try:
        resp = await client.post("/api/telegram/webhook", json=make_text_update(1, 1, "/start"))
        assert resp.status_code == 403
        resp = await client.post(
            "/api/telegram/webhook", json=make_text_update(1, 1, "/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
        )
        assert resp.status_code == 200
        assert "Welcome" in fake.sent_messages[-1]["text"]
    finally:
        tg_routes.set_gateway(None)
        monkeypatch.setattr(get_settings(), "telegram_webhook_secret", None)


async def test_telegram_tenancy(client, db_session):
    """A second user's telegram cannot read the first user's state."""
    h_a = await auth_headers(client, "tga@example.com")
    h_b = await auth_headers(client, "tgb@example.com")
    await setup_mock_provider(client, h_a, {"6000": GOAL_JSON})
    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    # link A
    code_a = (await client.post("/api/telegram/pairing-code", headers=h_a)).json()["code"]
    await gateway.process_update(db_session, make_text_update(1001, 1001, f"/link {code_a}"))
    # link B with different telegram id
    code_b = (await client.post("/api/telegram/pairing-code", headers=h_b)).json()["code"]
    await gateway.process_update(db_session, make_text_update(2002, 2002, f"/link {code_b}"))
    # A creates a goal
    await gateway.process_update(db_session, make_text_update(1001, 1001, "income goal 6000 by june"))
    # B's status shows no goals
    await gateway.process_update(db_session, make_text_update(2002, 2002, "/status"))
    assert "Goals: 0 active" in fake.sent_messages[-1]["text"]
