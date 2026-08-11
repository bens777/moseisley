"""Friends of the Cantina API (owner directive 2026-08-11).

- /api/public/friends/* : no authentication, public serializers ONLY.
- /api/friends/*        : authenticated owner operations (server-side ownership).
- /api/friends/moderate : operator (is_superuser) moderation.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.friends import service as svc

public_router = APIRouter(prefix="/public/friends")
router = APIRouter(prefix="/friends")


# ── public (no auth) ────────────────────────────────────────────────

@public_router.get("")
async def public_people(db: DB, q: str | None = None, sort: str = "recently_active",
                        limit: int | None = None, offset: int | None = None):
    return await svc.list_public_profiles(db, q=q, sort=sort, limit=limit, offset=offset)


@public_router.get("/projects")
async def public_projects(db: DB, q: str | None = None, category: str | None = None,
                          limit: int | None = None, offset: int | None = None):
    return await svc.list_public_projects(db, q=q, category=category,
                                          limit=limit, offset=offset)


@public_router.get("/updates")
async def public_updates(db: DB, handle: str | None = None,
                         limit: int | None = None, offset: int | None = None):
    return await svc.list_public_updates(db, handle=handle, limit=limit, offset=offset)


@public_router.get("/categories")
async def public_categories():
    return {"categories": list(svc.CATEGORIES)}


@public_router.get("/{handle}")
async def public_profile(handle: str, db: DB):
    page = await svc.public_profile_page(db, handle)
    if page is None:
        raise HTTPException(404, "no such table in the cantina")
    return page


# ── owner (auth) ────────────────────────────────────────────────────

class ProfileRequest(BaseModel):
    handle: str
    display_name: str
    bio: str = ""
    avatar_url: str | None = None
    location: str | None = None
    links: dict = {}


class PublishRequest(BaseModel):
    published: bool


class ProjectRequest(BaseModel):
    name: str
    tagline: str = ""
    description: str = ""
    url: str | None = None
    image_url: str | None = None
    category: str = "other"
    tags: list[str] = []
    status: str = "active"
    is_public: bool = True


class ProjectPatch(BaseModel):
    name: str | None = None
    tagline: str | None = None
    description: str | None = None
    url: str | None = None
    image_url: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    is_public: bool | None = None


class UpdateRequest(BaseModel):
    text: str
    url: str | None = None
    image_url: str | None = None
    project_id: str | None = None
    is_public: bool = True


class UpdatePatch(BaseModel):
    text: str | None = None
    url: str | None = None
    image_url: str | None = None
    project_id: str | None = None
    is_public: bool | None = None


class ModerateRequest(BaseModel):
    entity: str  # profile|project|update
    entity_id: str
    status: str  # active|hidden
    reason: str | None = None


def _own_profile(p) -> dict:
    return {
        "handle": p.handle, "display_name": p.display_name, "bio": p.bio,
        "avatar_url": p.avatar_url, "location": p.location, "links": p.links_json or {},
        "is_published": p.is_published, "published_at": p.published_at,
        "moderation_status": p.moderation_status,
    }


def _own_project(pr) -> dict:
    return {**svc.serialize_project_public(pr), "is_public": pr.is_public,
            "moderation_status": pr.moderation_status}


def _own_update(u) -> dict:
    return {**svc.serialize_update_public(u), "project_id": u.project_id,
            "is_public": u.is_public, "moderation_status": u.moderation_status}


@router.get("/me")
async def my_profile(user: CurrentUser, db: DB):
    from sqlalchemy import select

    from backend.core.models import PublicProject, PublicUpdate

    profile = await svc.get_profile(db, user.id)
    projects = list((await db.execute(select(PublicProject).where(
        PublicProject.user_id == user.id).order_by(PublicProject.created_at))).scalars())
    updates = list((await db.execute(select(PublicUpdate).where(
        PublicUpdate.user_id == user.id)
        .order_by(PublicUpdate.created_at.desc()).limit(50))).scalars())
    return {
        "profile": _own_profile(profile) if profile else None,
        "projects": [_own_project(p) for p in projects],
        "updates": [_own_update(u) for u in updates],
        "limits": {"max_projects": svc.MAX_PUBLIC_PROJECTS_PER_USER,
                   "max_update_chars": svc.UPDATE_MAX_CHARS},
    }


@router.put("/me")
async def save_profile(body: ProfileRequest, user: CurrentUser, db: DB):
    try:
        profile = await svc.upsert_profile(
            db, user.id, handle=body.handle, display_name=body.display_name,
            bio=body.bio, avatar_url=body.avatar_url, location=body.location,
            links=body.links)
    except svc.FriendsError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return _own_profile(profile)


@router.post("/me/publish")
async def publish_profile(body: PublishRequest, user: CurrentUser, db: DB):
    try:
        profile = await svc.set_published(db, user.id, body.published)
    except svc.FriendsError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return _own_profile(profile)


@router.post("/projects")
async def create_project(body: ProjectRequest, user: CurrentUser, db: DB):
    try:
        project = await svc.create_project(db, user.id, **body.model_dump())
    except svc.FriendsError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return _own_project(project)


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, body: ProjectPatch, user: CurrentUser, db: DB):
    try:
        project = await svc.update_project(
            db, user.id, project_id,
            **{k: v for k, v in body.model_dump().items() if v is not None
               or k in ("url", "image_url")})
    except svc.FriendsError as e:
        code = 404 if "not found" in str(e) else 400
        raise HTTPException(code, str(e)) from e
    await db.commit()
    return _own_project(project)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, user: CurrentUser, db: DB):
    try:
        await svc.delete_project(db, user.id, project_id)
    except svc.FriendsError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return {"deleted": True}


@router.post("/updates")
async def create_update(body: UpdateRequest, user: CurrentUser, db: DB):
    try:
        update = await svc.create_update(db, user.id, **body.model_dump())
    except svc.FriendsError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return _own_update(update)


@router.patch("/updates/{update_id}")
async def patch_update(update_id: str, body: UpdatePatch, user: CurrentUser, db: DB):
    try:
        update = await svc.edit_update(
            db, user.id, update_id,
            **{k: v for k, v in body.model_dump().items() if v is not None
               or k in ("url", "image_url", "project_id")})
    except svc.FriendsError as e:
        code = 404 if "not found" in str(e) else 400
        raise HTTPException(code, str(e)) from e
    await db.commit()
    return _own_update(update)


@router.delete("/updates/{update_id}")
async def delete_update(update_id: str, user: CurrentUser, db: DB):
    try:
        await svc.delete_update(db, user.id, update_id)
    except svc.FriendsError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return {"deleted": True}


@router.post("/moderate")
async def moderate(body: ModerateRequest, user: CurrentUser, db: DB):
    """Operator-only content moderation (§20). Not exposed in any public UI."""
    if not user.is_superuser:
        raise HTTPException(403, "operator access required")
    try:
        await svc.moderate(db, entity=body.entity, entity_id=body.entity_id,
                           status=body.status, reason=body.reason)
    except svc.FriendsError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return {"moderated": True}
