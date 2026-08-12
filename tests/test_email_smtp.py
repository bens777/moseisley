"""SMTP delivery: configuration, graceful degradation, and the reset flow.

No real SMTP connection is ever opened — aiosmtplib.send is monkeypatched.
"""
from __future__ import annotations

import re

import pytest

from backend.core.config import get_settings
from backend.core.email import MemoryEmailProvider, reset_email_provider
from backend.email import sender
from backend.email.templates import reset_password_email


@pytest.fixture()
def smtp_on(monkeypatch):
    """Configure SMTP and capture what would go over the wire."""
    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "ssl0.test.invalid")
    monkeypatch.setattr(s, "smtp_port", 465)
    monkeypatch.setattr(s, "smtp_user", "cantina@moseisley.sh")
    monkeypatch.setattr(s, "smtp_password", "test-mailbox-password")
    monkeypatch.setattr(s, "smtp_from", "cantina@moseisley.sh")
    monkeypatch.setattr(s, "smtp_from_name", "Moseisley Cantina")

    sent: list[dict] = []

    async def fake_send(msg, **kwargs):
        sent.append({"msg": msg, "kwargs": kwargs})
        return {}, "250 OK"

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    return sent


# ── graceful degradation ────────────────────────────────────────────

async def test_send_email_returns_false_when_unconfigured(monkeypatch, caplog):
    """Self-host without SMTP: no crash, a warning, and the caller carries on."""
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    with caplog.at_level("WARNING", logger="mychief.email"):
        ok = await sender.send_email("someone@example.com", "Subject", "<p>hi</p>")
    assert ok is False
    assert "not configured" in caplog.text


async def test_send_email_never_raises_and_retries_once(monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", "ssl0.test.invalid")
    attempts = []

    async def always_fails(msg, **kwargs):
        attempts.append(kwargs)
        raise OSError("connection refused")

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", always_fails)
    monkeypatch.setattr(sender, "RETRY_DELAY_SECONDS", 0)

    ok = await sender.send_email("someone@example.com", "Subject", "<p>hi</p>")
    assert ok is False            # never raises
    assert len(attempts) == 2     # original + one retry


# ── message construction ────────────────────────────────────────────

async def test_send_email_builds_a_multipart_ssl_message(smtp_on):
    ok = await sender.send_email("crew@example.com", "Your Moseisley reset link 🛸",
                                 "<p>Hello <b>there</b></p>", text_body="Hello there")
    assert ok is True
    msg = smtp_on[0]["msg"]
    kwargs = smtp_on[0]["kwargs"]

    assert kwargs["hostname"] == "ssl0.test.invalid"
    assert kwargs["port"] == 465
    assert kwargs["use_tls"] is True          # implicit SSL on 465
    assert kwargs["start_tls"] is False       # never both
    assert kwargs["username"] == "cantina@moseisley.sh"

    assert msg["To"] == "crew@example.com"
    assert msg["From"] == "Moseisley Cantina <cantina@moseisley.sh>"
    assert "reset link" in msg["Subject"]
    types = [p.get_content_type() for p in msg.walk() if p.get_content_type().startswith("text/")]
    assert types == ["text/plain", "text/html"]   # plain first, html preferred


async def test_legacy_smtp_username_still_works(monkeypatch, smtp_on):
    monkeypatch.setattr(get_settings(), "smtp_user", None)
    monkeypatch.setattr(get_settings(), "smtp_username", "legacy@moseisley.sh")
    await sender.send_email("crew@example.com", "s", "<p>b</p>")
    assert smtp_on[0]["kwargs"]["username"] == "legacy@moseisley.sh"


# ── templates ───────────────────────────────────────────────────────

def test_reset_template_contains_the_link_and_ttl():
    link = "https://moseisley.sh/reset-password?token=abc.def.ghi"
    subject, text, html = reset_password_email(link, 60)
    assert subject == "Your Moseisley reset link 🛸"
    assert link in text and link in html
    assert "60 minutes" in text and "60 minutes" in html
    # a single, unambiguous call to action
    assert html.count(f'href="{link}"') == 1
    assert "Choose a new password" in html
    # inline styles only — no external CSS an inbox would strip
    assert "<style" not in html.lower() and "stylesheet" not in html.lower()
    assert re.search(r'<div style="[^"]*background:#07040f', html)   # cantina ground


# ── the reset flow end to end (memory provider) ─────────────────────

async def test_forgot_password_sends_the_reset_email(client, auth):
    MemoryEmailProvider.sent.clear()
    me = (await client.get("/api/me", headers=auth)).json()

    resp = await client.post("/api/auth/forgot-password", json={"email": me["email"]})
    assert resp.status_code in (200, 202)

    reset = [m for m in MemoryEmailProvider.sent if "reset link" in m["subject"]]
    assert len(reset) == 1
    mail = reset[0]
    assert mail["to"] == me["email"]
    assert mail["subject"] == "Your Moseisley reset link 🛸"
    assert "/reset-password?token=" in mail["html"]
    assert "/reset-password?token=" in mail["body"]      # plain-text fallback


async def test_registration_sends_nothing_at_all(client):
    MemoryEmailProvider.sent.clear()
    resp = await client.post("/api/auth/register",
                             json={"email": "newcomer@example.com",
                                   "password": "correct-horse-battery"})
    assert resp.status_code == 201
    assert MemoryEmailProvider.sent == []   # no welcome, no verification


async def test_smtp_provider_is_selected_when_host_is_configured(monkeypatch):
    """A configured SMTP_HOST is enough — no second switch to remember."""
    from backend.core.email import SMTPEmailProvider, get_email_provider

    reset_email_provider()
    monkeypatch.setattr(get_settings(), "email_provider", "console")
    monkeypatch.setattr(get_settings(), "smtp_host", "ssl0.test.invalid")
    assert isinstance(get_email_provider(), SMTPEmailProvider)
    reset_email_provider()


# ── registration must never wait on a mail server ───────────────────

async def test_password_reset_does_not_send_email_inline(client, auth, monkeypatch):
    """The one remaining email goes out on a background task.

    Regression guard for the production incident where registration took ~84s
    on inline SMTP round-trips (20s timeout + 20s retry, twice).
    """
    import asyncio
    import time

    from backend.core.email import reset_email_provider

    s = get_settings()
    monkeypatch.setattr(s, "email_provider", "smtp")
    monkeypatch.setattr(s, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(s, "smtp_user", "cantina@moseisley.sh")
    monkeypatch.setattr(s, "smtp_password", "pw")
    reset_email_provider()

    started: list[float] = []
    finished: list[str] = []

    async def slow_send(msg, **kwargs):
        started.append(time.perf_counter())
        await asyncio.sleep(0.75)          # stands in for a stalled mail server
        finished.append(msg["Subject"])
        return {}, "250 OK"

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", slow_send)

    me = (await client.get("/api/me", headers=auth)).json()
    t0 = time.perf_counter()
    resp = await client.post("/api/auth/forgot-password", json={"email": me["email"]})
    elapsed = time.perf_counter() - t0

    assert resp.status_code in (200, 202)
    # the response beat the mail server: nothing was awaited inline
    assert elapsed < 0.5, f"forgot-password blocked on SMTP for {elapsed:.2f}s"
    assert finished == [], "an email completed before the response — it was inline"
    assert started, "no background send was spawned"

    # …and the mail still goes out once the loop gets to it
    await asyncio.sleep(1.0)
    assert finished == ["Your Moseisley reset link 🛸"]
    reset_email_provider()
