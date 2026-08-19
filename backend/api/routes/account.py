"""Account data portability & erasure API (owner directive §35 — GDPR).

  GET  /api/account/export  → machine-readable copy of all the caller's data
  POST /api/account/delete  → irreversible erasure of the caller's account

Deletion re-authenticates with the current password: a stolen session token
alone must not be able to destroy an account.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.ops import account as account_svc

router = APIRouter(prefix="/account")


@router.get("/export")
async def export_account(user: CurrentUser, db: DB):
    data = await account_svc.export_account(db, user)
    await db.commit()
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": "attachment; filename=moseisley-account-export.json"},
    )


class DeleteRequest(BaseModel):
    password: str
    confirm: str  # must equal "DELETE"


@router.post("/delete")
async def delete_account(body: DeleteRequest, user: CurrentUser, db: DB):
    if body.confirm != "DELETE":
        raise HTTPException(400, "confirmation phrase must be 'DELETE'")
    # Re-authenticate with the password — a session token alone cannot erase.
    from fastapi_users.password import PasswordHelper

    verified, _ = PasswordHelper().verify_and_update(
        body.password, user.hashed_password)
    if not verified:
        raise HTTPException(403, "password is incorrect")
    result = await account_svc.delete_account(db, user)
    return {"deleted": True, "tables_purged": sum(1 for c in result["counts"].values() if c)}
