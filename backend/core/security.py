"""Authentication dependencies (self-hosted; architecture update).

Identity always comes from the validated session token — never from a
client-supplied user_id (§40). See backend/core/auth.py for the auth stack.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import current_active_user
from backend.core.db import get_db
from backend.core.models import User

CurrentUser = Annotated[User, Depends(current_active_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
