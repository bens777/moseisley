"""EmailProvider abstraction (architecture update).

Moseisley.sh never depends on a specific SaaS mailer. Implementations:
- SMTPEmailProvider: any SMTP server (self-hosted mail, Mailpit in dev, or a
  transactional service's SMTP endpoint if the operator chooses one);
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
    def __init__(self, host: str, port: int, username: str | None, password: str | None,
                 use_tls: bool, sender: str):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.use_tls = use_tls
        self.sender = sender

    async def send(self, to: str, subject: str, body: str, html: str | None = None) -> bool:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib

        if html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html, "html"))
        else:
            msg = MIMEText(body)
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg["Reply-To"] = get_settings().support_email
        try:
            await aiosmtplib.send(
                msg, hostname=self.host, port=self.port,
                username=self.username or None, password=self.password or None,
                start_tls=self.use_tls,
            )
            return True
        except Exception:
            logger.exception("smtp send failed")
            return False


_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    global _provider
    if _provider is None:
        settings = get_settings()
        if settings.email_provider == "smtp":
            _provider = SMTPEmailProvider(
                settings.smtp_host or "localhost", settings.smtp_port,
                settings.smtp_username, settings.smtp_password,
                settings.smtp_use_tls, settings.email_from,
            )
        elif settings.email_provider == "memory":
            _provider = MemoryEmailProvider()
        else:
            _provider = ConsoleEmailProvider()
    return _provider


def reset_email_provider() -> None:
    global _provider
    _provider = None
