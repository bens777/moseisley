"""Application configuration.

All configuration comes from environment variables (see .env.example).
"""
from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Moseisley.sh"
    environment: str = "development"  # development | production | test
    debug: bool = False

    # Database. PostgreSQL in production (postgresql+asyncpg://...), SQLite for local/dev.
    database_url: str = "sqlite+aiosqlite:///./mychief.db"

    # Secret used to sign dev-mode JWTs and internal tokens.
    app_secret: str = "change-me-dev-secret"

    # 32-byte key (base64 or hex) for AES-256-GCM secret encryption.
    # If unset, derived from app_secret (acceptable for dev only).
    master_encryption_key: str | None = None

    # Self-hosted authentication (application-owned, PostgreSQL-backed).
    session_lifetime_seconds: int = 30 * 24 * 3600
    auth_rate_limit_attempts: int = 10       # per window per client
    auth_rate_limit_window_seconds: int = 300

    # Email delivery abstraction: console | smtp | memory
    email_provider: str = "console"
    email_from: str = "Moseisley.sh <no-reply@localhost>"
    support_email: str = "cantina@moseisley.sh"  # official support/contact address
    smtp_host: str | None = None
    smtp_port: int = 465                     # implicit SSL (OVH, most providers)
    smtp_user: str | None = None             # canonical name
    smtp_username: str | None = None         # deprecated alias for SMTP_USER
    smtp_password: str | None = None
    smtp_from: str | None = None             # envelope sender; defaults to email_from
    smtp_from_name: str = "Moseisley Cantina"
    smtp_use_tls: bool = False               # STARTTLS; ignored when port == 465

    def smtp_configured(self) -> bool:
        """SMTP is live as soon as a host is set — no second flag to remember."""
        return bool(self.smtp_host)

    def smtp_login(self) -> str | None:
        return self.smtp_user or self.smtp_username or None

    def smtp_sender(self) -> str:
        """RFC 5322 From: "Moseisley Cantina <cantina@moseisley.sh>"."""
        addr = self.smtp_from or self.email_from
        if "<" in addr:                      # already a full mailbox spec
            return addr
        return f"{self.smtp_from_name} <{addr}>" if self.smtp_from_name else addr

    # Moseisley.sh-owned object storage: local | s3  (S3-compatible protocol, not AWS-specific)
    storage_backend: str = "local"
    storage_local_path: str = "./data/storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str | None = None

    # Telegram
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_mode: str = "webhook"  # webhook | polling | disabled

    # Google OAuth
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    # Payments
    payment_provider: str = "simulated"  # simulated | stripe_test
    stripe_api_key: str | None = None
    real_payments_enabled: bool = False  # hard feature flag; simulator is the default

    # Stripe Billing (hosted subscriptions — owner directive final pricing:
    # Basic $9/mo, Pro $19/mo; Community = self-hosted, free, no Stripe).
    stripe_price_id_basic: str | None = None
    stripe_price_id_pro: str | None = None
    # Legacy single-price config; treated as the Pro price if the split ids are unset.
    stripe_price_id: str | None = None
    stripe_webhook_secret: str | None = None

    # Factory mode (platform-provided AI): ONE server-side OpenRouter key with
    # prepaid credits as the hard spend ceiling. Never stored in the DB, never
    # returned by any API — read only inside providers/registry + factory_pool.
    # Unset (self-host default) = factory mode unavailable, BYOK-only.
    factory_openrouter_api_key: str | None = None
    factory_trial_days: int = 14
    # Per-user daily request caps, one per audience. Basic and Pro differ on
    # purpose — the bigger tank is part of what Pro sells.
    factory_trial_daily_requests: int = 40
    factory_basic_daily_requests: int = 150
    factory_pro_daily_requests: int = 400
    # Deprecated (pre-split single paid cap). Kept so existing deployments keep
    # booting: when set it supplies BOTH caps above. Remove once .env files are
    # migrated to the two settings.
    factory_paid_daily_requests: int | None = None

    # Frontend origin for CORS
    frontend_origin: str = "http://localhost:3000"

    # Worker
    worker_poll_seconds: float = 5.0

    # Dev Agent (third pass §20-§26): tests run inside the isolated worktree only.
    dev_test_command: str = ".venv/bin/python -m pytest -q -x"

    @model_validator(mode="after")
    def _apply_deprecated_paid_cap(self):
        """FACTORY_PAID_DAILY_REQUESTS (pre-split) still configures both plans."""
        if self.factory_paid_daily_requests is not None:
            self.factory_basic_daily_requests = self.factory_paid_daily_requests
            self.factory_pro_daily_requests = self.factory_paid_daily_requests
            logging.getLogger("mychief.config").warning(
                "FACTORY_PAID_DAILY_REQUESTS is deprecated: applying %s to both "
                "FACTORY_BASIC_DAILY_REQUESTS and FACTORY_PRO_DAILY_REQUESTS. "
                "Set the two variables separately to give Pro a bigger tank.",
                self.factory_paid_daily_requests,
            )
        return self

    def encryption_key_bytes(self) -> bytes:
        raw = self.master_encryption_key
        if raw:
            try:
                key = base64.b64decode(raw, validate=True)
                if len(key) == 32:
                    return key
            except Exception:
                pass
            try:
                key = bytes.fromhex(raw)
                if len(key) == 32:
                    return key
            except ValueError:
                pass
            raise ValueError("MASTER_ENCRYPTION_KEY must be 32 bytes, base64 or hex encoded")
        # Dev fallback: deterministic derivation from app secret.
        # NOTE: the "mychief-master-key" label is intentionally retained from the pre-rebrand
        # product name. It is a KDF input, not a user-visible string; changing a single byte
        # would make every secret encrypted under a derived key undecryptable.
        return hashlib.sha256(f"mychief-master-key:{self.app_secret}".encode()).digest()


@lru_cache
def get_settings() -> Settings:
    return Settings()
