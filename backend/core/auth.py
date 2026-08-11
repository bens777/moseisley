"""Self-hosted authentication (architecture update).

Built on fastapi-users (mature, maintained) over our PostgreSQL:
- email + password registration/login (Argon2/bcrypt via pwdlib — no hand-rolled crypto);
- email verification + password reset via expiring one-time tokens;
- database-backed sessions (logout = token row deletion → real invalidation);
- deterministic in-process rate limiting on auth endpoints.
Email flows go through the EmailProvider abstraction (SMTP/console/memory).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request
from fastapi_users import BaseUserManager, FastAPIUsers, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport
from fastapi_users.authentication.strategy.db import AccessTokenDatabase, DatabaseStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.db import get_db
from backend.core.email import get_email_provider
from backend.core.models import AccessToken, User

logger = logging.getLogger("mychief.auth")


class UserRead(schemas.BaseUser[str]):
    display_name: str | None = None
    timezone: str = "UTC"
    autonomy_mode: str = "assisted"


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None


async def get_user_db(session: AsyncSession = Depends(get_db)) -> AsyncIterator:
    yield SQLAlchemyUserDatabase(session, User)


async def get_access_token_db(session: AsyncSession = Depends(get_db)) -> AsyncIterator:
    yield SQLAlchemyAccessTokenDatabase(session, AccessToken)


RESET_TOKEN_LIFETIME_SECONDS = 3600  # 60 minutes


def _reset_email(token: str) -> tuple[str, str, str]:
    """Build (subject, text, html) for the password-reset email.

    The token itself is a signed one-time JWT (fastapi-users): it is never stored
    server-side, embeds a fingerprint of the current password hash (so it dies on use
    and invalidates all other outstanding reset tokens), and expires after 60 minutes.
    Never log it.
    """
    origin = get_settings().frontend_origin.rstrip("/")
    link = f"{origin}/reset-password?token={token}"
    subject = "Reset your moseisley.sh password"
    text = (
        "We received a request to reset your password.\n"
        "Open the link below to choose a new password.\n"
        "This link expires in 60 minutes.\n"
        "If you didn't request this, you can ignore this email.\n\n"
        f"{link}\n"
    )
    html = f"""\
<div style="background:#0b0a08;padding:32px;font-family:ui-monospace,Menlo,monospace;color:#ede6da">
  <p style="font-size:16px;font-weight:bold;margin:0 0 16px">
    <span style="color:#e8a33d">&#9656;</span> moseisley<span style="color:#e8a33d">.sh</span>
  </p>
  <p style="font-size:14px;line-height:1.6;color:#ede6da;margin:0 0 8px">
    We received a request to reset your password.<br>
    Click the button below to choose a new password.<br>
    This link expires in 60 minutes.<br>
    If you didn't request this, you can ignore this email.
  </p>
  <p style="margin:24px 0">
    <a href="{link}" style="background:#e8a33d;color:#0b0a08;text-decoration:none;
       padding:12px 24px;border-radius:6px;font-weight:bold;font-size:14px">
      Reset password
    </a>
  </p>
  <p style="font-size:12px;color:#6b6359;word-break:break-all">
    Or copy this link: {link}
  </p>
</div>"""
    return subject, text, html


class UserManager(BaseUserManager[User, str]):
    reset_password_token_lifetime_seconds = RESET_TOKEN_LIFETIME_SECONDS

    @property
    def reset_password_token_secret(self):
        return get_settings().app_secret

    @property
    def verification_token_secret(self):
        return get_settings().app_secret

    def parse_id(self, value) -> str:
        return str(value)

    async def validate_password(self, password: str, user) -> None:
        if len(password) < 8:
            from fastapi_users import InvalidPasswordException

            raise InvalidPasswordException(reason="Password must be at least 8 characters")

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("user registered: %s", user.id)
        # create the default native agent here (single point) so concurrent first
        # page loads can never race the lazy default-creation path
        try:
            from backend.agents.registry import ensure_default_agents

            await ensure_default_agents(self.user_db.session, user.id)
        except Exception:
            logger.warning("default agent creation deferred to first use")
        try:
            await self.request_verify(user, request)
        except Exception:
            pass  # verification email is best-effort at registration time

    async def on_after_request_verify(self, user: User, token: str,
                                      request: Request | None = None) -> None:
        await get_email_provider().send(
            user.email, "Verify your Moseisley.sh account",
            "Confirm your email address by submitting this token to /api/auth/verify "
            f"(the dashboard does this for you):\n\n{token}\n",
        )

    async def on_after_forgot_password(self, user: User, token: str,
                                       request: Request | None = None) -> None:
        subject, text, html = _reset_email(token)
        await get_email_provider().send(user.email, subject, text, html=html)


async def get_user_manager(user_db=Depends(get_user_db)) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="/api/auth/login")


def get_database_strategy(
    access_token_db: AccessTokenDatabase = Depends(get_access_token_db),
) -> DatabaseStrategy:
    return DatabaseStrategy(access_token_db,
                            lifetime_seconds=get_settings().session_lifetime_seconds)


auth_backend = AuthenticationBackend(
    name="db-bearer", transport=bearer_transport, get_strategy=get_database_strategy,
)

fastapi_users = FastAPIUsers[User, str](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)


# --- Deterministic rate limiting (per-process; front with a shared limiter when
# scaling to multiple API instances) ---

_attempts: dict[str, deque] = defaultdict(deque)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def auth_rate_limit(request: Request) -> None:
    settings = get_settings()
    key = _client_key(request)
    now = time.monotonic()
    window = settings.auth_rate_limit_window_seconds
    q = _attempts[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= settings.auth_rate_limit_attempts:
        raise HTTPException(429, "too many authentication attempts; try again later")
    q.append(now)


def reset_rate_limiter() -> None:
    _attempts.clear()
