from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.core.auth import (
    UserCreate,
    UserRead,
    auth_backend,
    auth_rate_limit,
    fastapi_users,
)
from backend.core.security import CurrentUser

router = APIRouter()

limited = [Depends(auth_rate_limit)]

router.include_router(fastapi_users.get_auth_router(auth_backend),
                      prefix="/auth", dependencies=limited)
router.include_router(fastapi_users.get_register_router(UserRead, UserCreate),
                      prefix="/auth", dependencies=limited)
router.include_router(fastapi_users.get_reset_password_router(),
                      prefix="/auth", dependencies=limited)
router.include_router(fastapi_users.get_verify_router(UserRead),
                      prefix="/auth", dependencies=limited)


@router.get("/me")
async def me(user: CurrentUser):
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "timezone": user.timezone,
        "autonomy_mode": user.autonomy_mode,
        "is_verified": user.is_verified,
        "settings": user.settings_json,
    }
