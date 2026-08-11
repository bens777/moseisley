from __future__ import annotations

import json
import secrets as pysecrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from backend.core.crypto import encrypt_secret
from backend.core.models import IntegrationConnection, XRayFinding, XRayRun
from backend.core.security import DB, CurrentUser
from backend.integrations import broker
from backend.integrations.google import adapter as google_adapter
from backend.ledger import service as ledger
from backend.policies import engine as policy

router = APIRouter(prefix="/integrations")

_oauth_states: dict[str, str] = {}  # state -> user_id (10-min OAuth flows; in-memory is fine per instance)


def _serialize(c: IntegrationConnection) -> dict:
    return {
        "id": c.id, "integration_type": c.integration_type, "name": c.name,
        "status": c.status, "configuration": c.configuration_json,
        "capabilities": c.capabilities_json, "has_credentials": bool(c.encrypted_credentials),
        "last_health_ok": c.last_health_ok, "created_at": c.created_at,
    }


class CreateConnectionRequest(BaseModel):
    integration_type: str
    name: str
    configuration: dict = {}
    secret_headers: dict | None = None  # webhook/rest/n8n auth headers
    bearer_token: str | None = None     # mcp auth
    capabilities: dict | None = None    # capability -> level grants


@router.get("")
async def list_connections(user: CurrentUser, db: DB):
    return [_serialize(c) for c in await broker.list_connections(db, user.id)]


@router.post("")
async def create_connection(body: CreateConnectionRequest, user: CurrentUser, db: DB):
    if body.integration_type not in broker.ADAPTERS:
        raise HTTPException(400, f"unknown integration type: {body.integration_type}")
    if body.integration_type == "demo":
        # demo data is a singleton per user — repeated clicks must not duplicate it
        existing = next((c for c in await broker.list_connections(db, user.id)
                         if c.integration_type == "demo"), None)
        if existing is not None:
            return _serialize(existing)
    conn = IntegrationConnection(
        user_id=user.id, integration_type=body.integration_type, name=body.name,
        configuration_json=body.configuration, capabilities_json=body.capabilities or {},
    )
    if body.secret_headers:
        conn.encrypted_credentials = encrypt_secret(json.dumps(body.secret_headers))
    if body.bearer_token:
        conn.encrypted_credentials = encrypt_secret(body.bearer_token)
    db.add(conn)
    await db.flush()
    await ledger.record(db, user.id, "integration_connected", actor_type="user",
                        entity_type="integration", entity_id=conn.id,
                        payload={"type": body.integration_type, "name": body.name})
    await db.commit()
    return _serialize(conn)


class CapabilityGrantRequest(BaseModel):
    capability: str
    level: str  # DENIED | READ | DRAFT | EXECUTE


@router.post("/{connection_id}/grant")
async def grant_capability(connection_id: str, body: CapabilityGrantRequest, user: CurrentUser, db: DB):
    if body.level not in policy.LEVELS:
        raise HTTPException(400, "invalid level")
    if body.capability not in policy.CAPABILITY_REQUIREMENTS:
        raise HTTPException(400, "unknown capability")
    conn = (await db.execute(select(IntegrationConnection).where(
        IntegrationConnection.id == connection_id, IntegrationConnection.user_id == user.id
    ))).scalar_one_or_none()
    if conn is None:
        raise HTTPException(404, "connection not found")
    conn.capabilities_json = {**(conn.capabilities_json or {}), body.capability: body.level}
    await db.commit()
    return _serialize(conn)


@router.post("/{connection_id}/health")
async def health(connection_id: str, user: CurrentUser, db: DB):
    conn = (await db.execute(select(IntegrationConnection).where(
        IntegrationConnection.id == connection_id, IntegrationConnection.user_id == user.id
    ))).scalar_one_or_none()
    if conn is None:
        raise HTTPException(404, "connection not found")
    ok = await broker.build_adapter(conn).health_check()
    conn.last_health_ok = ok
    conn.last_health_at = datetime.now(UTC)
    await db.commit()
    return {"ok": ok}


@router.delete("/{connection_id}")
async def disconnect(connection_id: str, user: CurrentUser, db: DB, purge: bool = False):
    """Disconnect an integration; purge=true also deletes derived X-Ray data (§51)."""
    conn = (await db.execute(select(IntegrationConnection).where(
        IntegrationConnection.id == connection_id, IntegrationConnection.user_id == user.id
    ))).scalar_one_or_none()
    if conn is None:
        raise HTTPException(404, "connection not found")
    await db.delete(conn)
    await ledger.record(db, user.id, "integration_disconnected", actor_type="user",
                        entity_type="integration", entity_id=connection_id,
                        payload={"type": conn.integration_type, "purged": purge})
    if purge:
        run_ids = [r for (r,) in (await db.execute(
            select(XRayRun.id).where(XRayRun.user_id == user.id)
        )).all()]
        if run_ids:
            await db.execute(delete(XRayFinding).where(XRayFinding.user_id == user.id))
            await db.execute(delete(XRayRun).where(XRayRun.user_id == user.id))
        await ledger.record(db, user.id, "data_purged", actor_type="user",
                            payload={"scope": "xray", "runs": len(run_ids)})
    await db.commit()
    return {"ok": True, "purged": purge}


class InvokeRequest(BaseModel):
    capability: str
    operation: str
    params: dict = {}
    connection_id: str | None = None


@router.post("/invoke")
async def invoke(body: InvokeRequest, user: CurrentUser, db: DB):
    """Direct capability invocation by the user (dashboard tooling/tests)."""
    try:
        result = await broker.invoke(
            db, user.id, body.capability, body.operation, body.params,
            actor_type="user", connection_id=body.connection_id,
        )
        await db.commit()
        return {"result": result}
    except policy.PolicyDenied as e:
        await db.commit()
        raise HTTPException(403, str(e)) from e
    except broker.BrokerError as e:
        await db.commit()
        raise HTTPException(400, str(e)) from e


@router.get("/google/auth-url")
async def google_auth_url(user: CurrentUser):
    try:
        state = pysecrets.token_urlsafe(24)
        _oauth_states[state] = user.id
        return {"url": google_adapter.build_auth_url(state)}
    except Exception as e:
        raise HTTPException(424, f"Google OAuth not configured: {e}") from e


@router.get("/google/callback")
async def google_callback(state: str, code: str, db: DB):
    user_id = _oauth_states.pop(state, None)
    if user_id is None:
        raise HTTPException(400, "invalid oauth state")
    tokens = await google_adapter.exchange_code(code)
    conn = IntegrationConnection(
        user_id=user_id, integration_type="google", name="Google Workspace",
        encrypted_credentials=google_adapter.encrypt_tokens(tokens),
        capabilities_json={"gmail.read": "READ", "calendar.read": "READ"},
    )
    db.add(conn)
    await db.flush()
    await ledger.record(db, user_id, "integration_connected", actor_type="user",
                        entity_type="integration", entity_id=conn.id, payload={"type": "google"})
    await db.commit()
    return {"ok": True, "connection_id": conn.id}
