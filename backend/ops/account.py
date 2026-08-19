"""Account data export and erasure (owner directive §35 — GDPR foundations).

Two tenant-wide operations, both scoped strictly to the caller's ``user_id``:

  * :func:`export_account` — machine-readable copy of every tenant-owned row
    (data portability, GDPR Art. 20).
  * :func:`delete_account` — deterministic cascade purge of all tenant data and
    the user row (right to erasure, GDPR Art. 17).

The append-only Ledger (``events``) is normally immutable at both the ORM and DB
levels. Account erasure is the ONE sanctioned exception: it runs inside an
explicit :data:`LEDGER_ERASE` context so the guards permit deleting *this user's*
events, and only theirs. Everything else still cannot mutate the Ledger.
"""
from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Base, FileRef, User
from backend.storage.base import StorageError
from backend.storage.factory import get_owned_storage

logger = logging.getLogger("mychief.account")

# Set only for the duration of an account-erasure transaction. The Ledger ORM
# guard (backend/core/models.py) reads this to allow deleting events during
# erasure; it stays False for every other code path.
LEDGER_ERASE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ledger_erase", default=False)

# Tables that are NOT tenant-owned and must never be touched by a tenant purge.
_GLOBAL_TABLES = {"users", "model_catalog", "model_pricing_snapshots",
                  "challenge_decisions", "challenge_snapshots"}


def _tenant_tables_child_first() -> list:
    """Tenant-owned tables ordered child-before-parent (safe delete order).

    ``Base.metadata.sorted_tables`` lists parents before children; reversed gives
    a FK-safe deletion order. Only tables with a ``user_id`` column are returned."""
    tables = []
    for tbl in reversed(Base.metadata.sorted_tables):
        if tbl.name in _GLOBAL_TABLES:
            continue
        if "user_id" in tbl.columns:
            tables.append(tbl)
    return tables


async def export_account(db: AsyncSession, user: User) -> dict:
    """Return a JSON-serialisable copy of all of this user's data (Art. 20)."""
    out: dict = {
        "exported_at": datetime.now(UTC).isoformat(),
        "user_id": user.id,
        "account": {"id": user.id, "email": user.email,
                    "created_at": str(getattr(user, "created_at", "")),
                    "autonomy_mode": getattr(user, "autonomy_mode", None),
                    "mos_last_seen_at": str(u) if (u := getattr(user, "mos_last_seen_at", None)) else None},
        "tables": {},
    }
    for tbl in _tenant_tables_child_first():
        rows = (await db.execute(tbl.select().where(tbl.c.user_id == user.id))).mappings().all()
        if rows:
            # Never export encrypted secret material — the point of export is the
            # user's own content, not the ciphertext of their stored credentials.
            redacted = []
            for r in rows:
                d = {k: v for k, v in dict(r).items()
                     if not any(s in k.lower() for s in
                                ("encrypted", "secret", "verifier_hash", "code_hash", "token"))}
                redacted.append(d)
            # Coerce datetimes / Decimals / UUIDs to JSON-native values.
            out["tables"][tbl.name] = json.loads(json.dumps(redacted, default=str))
    from backend.ledger import service as ledger

    await ledger.record(db, user.id, "account_exported", actor_type="user",
                        payload={"tables": len(out["tables"])})
    return out


async def delete_account(db: AsyncSession, user: User) -> dict:
    """Erase all of this user's data and the user row itself (Art. 17).

    Deletes tenant tables child-first, then the user. Ledger erasure is permitted
    only inside the :data:`LEDGER_ERASE` context and only for this user."""
    user_id = user.id
    counts: dict[str, int] = {}
    dialect = db.bind.dialect.name if db.bind else "sqlite"

    token = LEDGER_ERASE.set(True)
    try:
        # On PostgreSQL the DB-level trigger also blocks DELETE on events; the
        # erasure migration teaches it to honour this per-transaction GUC.
        if dialect == "postgresql":
            await db.execute(text("SET LOCAL app.ledger_erase = 'on'"))
        # MOS Memory / Personal Vault (and any other FileRef): the generic
        # per-table purge below removes the `files` metadata rows, but never
        # touches the actual bytes in storage — deleting those explicitly here
        # is the only thing standing between "erasure" and orphaned files
        # sitting in the storage backend forever. Best-effort per file: a
        # storage error must never abort account erasure.
        owned_refs = list((await db.execute(
            select(FileRef).where(FileRef.user_id == user_id))).scalars())
        storage = get_owned_storage()
        for ref in owned_refs:
            if ref.storage_provider == storage.provider_name:
                try:
                    await storage.delete(ref.path)
                except StorageError:
                    pass
        # Every tenant table (events included) is purged. A proof-of-erasure
        # event cannot live in `events` — it would reference the user row we are
        # about to delete — so the erasure record goes to the application log,
        # which is retained under a separate legitimate-interest basis.
        for tbl in _tenant_tables_child_first():
            res = await db.execute(delete(tbl).where(tbl.c.user_id == user_id))
            counts[tbl.name] = res.rowcount or 0
        await db.execute(delete(User.__table__).where(User.__table__.c.id == user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        LEDGER_ERASE.reset(token)
    logger.info("account_erased", extra={"fields": {
        "user_id": user_id, "rows": sum(counts.values()),
        "tables_purged": sum(1 for c in counts.values() if c)}})
    return {"deleted": True, "counts": counts}
