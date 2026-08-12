"""SMTP delivery for every outbound email.

One path, one set of environment variables (SMTP_HOST / SMTP_PORT / SMTP_USER /
SMTP_PASSWORD / SMTP_FROM / SMTP_FROM_NAME). Port 465 uses implicit SSL, which
is what OVH and most providers expect; any other port falls back to STARTTLS.

Contract: send_email NEVER raises. A misconfigured or unreachable mail server
must not break registration or a password reset — it logs and returns False.
"""
from __future__ import annotations

import asyncio
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid, parseaddr

from backend.core.config import get_settings

logger = logging.getLogger("mychief.email")

SSL_PORT = 465
RETRY_DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 10.0


def _build_message(to: str, subject: str, html_body: str, text_body: str | None) -> MIMEMultipart:
    s = get_settings()
    msg = MIMEMultipart("alternative")
    # plain part first: RFC 2046 says the last part is the preferred one
    msg.attach(MIMEText(text_body or _strip_html(html_body), "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    name, addr = parseaddr(s.smtp_sender())
    msg["From"] = formataddr((name, addr)) if name else addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = s.support_email
    msg["Message-ID"] = make_msgid(domain=addr.split("@")[-1] or None)
    return msg


def _strip_html(html: str) -> str:
    """Crude last-resort plain-text part. Templates supply their own."""
    import re

    text = re.sub(r"<(br|/p|/div|/h[1-6])[^>]*>", "\n", html, flags=re.I)
    return re.sub(r"<[^>]+>", "", text).strip()


async def _deliver(msg: MIMEMultipart) -> None:
    import aiosmtplib

    s = get_settings()
    await aiosmtplib.send(
        msg,
        hostname=s.smtp_host,
        port=s.smtp_port,
        username=s.smtp_login(),
        password=s.smtp_password or None,
        use_tls=s.smtp_port == SSL_PORT,          # implicit SSL on 465
        start_tls=s.smtp_use_tls and s.smtp_port != SSL_PORT,
        timeout=TIMEOUT_SECONDS,
    )


# Fire-and-forget sends. Tasks are kept referenced until they finish, otherwise
# the event loop may garbage-collect them mid-flight.
_background: set[asyncio.Task] = set()


def spawn_send(coro) -> None:
    """Deliver in the background: nothing on a request's critical path should
    ever wait on a mail server. Failures are already logged inside send_email."""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:  # no running loop (sync context) — nothing to spawn onto
        logger.warning("no event loop for background email; dropping send")
        coro.close()
        return
    _background.add(task)
    task.add_done_callback(_background.discard)


async def send_email(to: str, subject: str, html_body: str,
                     text_body: str | None = None) -> bool:
    """Deliver one email. Returns True on success, False on every failure.

    Self-hosters without SMTP configured get a warning in the logs and no
    exception — the flow that triggered the email continues normally.
    """
    s = get_settings()
    if not s.smtp_configured():
        logger.warning(
            "SMTP is not configured (set SMTP_HOST/SMTP_USER/SMTP_PASSWORD) — "
            "skipping email to %s with subject %r", to, subject)
        return False

    msg = _build_message(to, subject, html_body, text_body)
    for attempt in (1, 2):                        # one retry on a transient failure
        try:
            await _deliver(msg)
            logger.info("email sent: to=%s subject=%r", to, subject)
            return True
        except Exception as e:                    # noqa: BLE001 — delivery is best-effort
            if attempt == 1:
                logger.warning("smtp send failed (attempt 1/2): %s — retrying", type(e).__name__)
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                continue
            logger.exception("smtp send failed permanently: to=%s subject=%r", to, subject)
    return False
