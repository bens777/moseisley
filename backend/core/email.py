"""EmailProvider abstraction (architecture update).

Moseisley.sh never depends on a specific SaaS mailer. Implementations:
- SMTPEmailProvider: real delivery via backend.email.sender (SMTP_HOST etc.);
- ConsoleEmailProvider: logs emails (development default);
- MemoryEmailProvider: captures emails in memory (tests).
"""
from __future__ import annotations

import logging

from backend.core.config import get_settings

logger = logging.getLogger("mychief.email")


class EmailProvider:
    async def send(self, to: str, subject: str, body: str, html: str | None = None) -> bool:
        raise NotImplementedError


class ConsoleEmailProvider(EmailProvider):
    async def send(self, to: str, subject: str, body: str, html: str | None = None) -> bool:
        logger.info("email (console): to=%s subject=%r body=%r", to, subject, body[:500])
        return True


class MemoryEmailProvider(EmailProvider):
    sent: list[dict] = []

    async def send(self, to: str, subject: str, body: str, html: str | None = None) -> bool:
        MemoryEmailProvider.sent.append({"to": to, "subject": subject, "body": body, "html": html})
        return True


class SMTPEmailProvider(EmailProvider):
    """Thin adapter over backend.email.sender — one SMTP path for the whole app."""

    async def send(self, to: str, subject: str, body: str, html: str | None = None) -> bool:
        from backend.email.sender import send_email

        return await send_email(to, subject, html or body, text_body=body)


_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    global _provider
    if _provider is None:
        settings = get_settings()
        # explicit test/dev choices win; otherwise a configured SMTP_HOST is
        # enough to go live — operators shouldn't have to flip a second switch
        if settings.email_provider == "memory":
            _provider = MemoryEmailProvider()
        elif settings.email_provider == "smtp" or settings.smtp_configured():
            _provider = SMTPEmailProvider()
        else:
            _provider = ConsoleEmailProvider()
    return _provider


def reset_email_provider() -> None:
    global _provider
    _provider = None
