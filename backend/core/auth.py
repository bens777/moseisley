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
    from backend.email.templates import reset_password_email

    origin = get_settings().frontend_origin.rstrip("/")
    link = f"{origin}/reset-password?token={token}"
    return reset_password_email(link, RESET_TOKEN_LIFETIME_SECONDS // 60)


async def _deliver(user_email: str, subject: str, text: str, html: str | None = None) -> None:
    """Send an email without blocking the caller when a real mail server is
    involved. Registration used to await two SMTP round-trips inline (~84s with
    an unreachable server); SMTP now goes to a background task while the
    console/memory providers stay synchronous so tests remain deterministic."""
    from backend.core.email import SMTPEmailProvider
    from backend.email.sender import spawn_send

    provider = get_email_provider()
    if isinstance(provider, SMTPEmailProvider):
        spawn_send(provider.send(user_email, subject, text, html=html))
        return
    await provider.send(user_email, subject, text, html=html)


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
        # No email is sent at registration: accounts are usable immediately.
        # Password reset is the ONLY email this platform sends.

    async def on_after_forgot_password(self, user: User, token: str,
                                       request: Request | None = None) -> None:
        subject, text, html = _reset_email(token)
        await _deliver(user.email, subject, text, html)


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
