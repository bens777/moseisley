"""Which X-Ray runs came from invented data, so nothing derived from them is
ever counted or shown again.

Runs built from the old demo dataset recorded `summary_json["demo_data"] = true`
at the time. That flag is now the filter: those runs and their findings are
excluded from every surface immediately, whether or not the user has got around
to clearing the connection.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import XRayRun


async def synthetic_run_ids(db: AsyncSession, user_id: str) -> set[str]:
    rows = (await db.execute(select(XRayRun.id, XRayRun.summary_json).where(
        XRayRun.user_id == user_id))).all()
    return {row[0] for row in rows if (row[1] or {}).get("demo_data")}


def is_synthetic_run(run) -> bool:
    return bool((getattr(run, "summary_json", None) or {}).get("demo_data"))
