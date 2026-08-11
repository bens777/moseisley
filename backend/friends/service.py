"""Friends of the Cantina — public community layer (owner directive 2026-08-11).

Model: PERSON → PUBLIC PROFILE → MULTIPLE PROJECTS → SHORT UPDATES.
Deliberately NOT a social network: no follows, likes, comments, DMs or feeds.

Privacy invariants (tested):
- Nothing is publicly visible unless the owner explicitly published their
  profile; unpublishing removes profile+projects+updates from every public
  query while preserving the data privately.
- Public serializers never include user ids' private account data (email,
  billing, providers, internal projects, goals, usage).
- All owner mutations filter by user_id server-side (no IDOR).
- moderation_status is operator-only (User.is_superuser).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import String as SAString
from sqlalchemy import cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import PublicProfile, PublicProject, PublicUpdate

# ── central limits (§14, §21) ──
MAX_PUBLIC_PROJECTS_PER_USER = 20
MAX_UPDATES_PER_DAY = 20
UPDATE_MAX_CHARS = 500
BIO_MAX_CHARS = 300
PAGE_SIZE_DEFAULT = 24
PAGE_SIZE_MAX = 50

CATEGORIES = ("saas", "ai_agents", "open_source", "newsletter", "content",
              "services", "community", "dev_tool", "marketplace", "other")
STATUSES = ("active", "building", "launched", "paused")
LINK_KEYS = ("website", "x", "github", "linkedin", "youtube", "newsletter", "other")

HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,28}[a-z0-9])?$")
RESERVED_HANDLES = frozenset({
    "admin", "administrator", "api", "app", "billing", "blog", "cantina", "chat",
    "command", "connections", "crew", "dev", "docs", "friends", "goals", "help",
    "legal", "login", "logout", "manager", "market", "me", "moderator", "money",
    "moseisley", "official", "owner", "pricing", "privacy", "projects", "public",
    "register", "reset-password", "root", "settings", "signup", "staff", "support",
    "system", "terms", "www", "xray", "activity", "agents",
})


class FriendsError(ValueError):
    pass


# ── validation ──────────────────────────────────────────────────────

def normalize_handle(handle: str) -> str:
    h = (handle or "").strip().lower()
    if not HANDLE_RE.fullmatch(h) or len(h) < 2:
        raise FriendsError(
            "handle must be 2-30 chars: lowercase letters, digits and single hyphens")
    if h in RESERVED_HANDLES:
        raise FriendsError("this handle is reserved")
    return h


def validate_url(url: str | None, *, field: str = "url") -> str | None:
    """Public links: http(s) only. javascript:/data:/etc are rejected (§18)."""
    if url is None or not url.strip():
        return None
    u = url.strip()
    if len(u) > 2048:
        raise FriendsError(f"{field} is too long")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise FriendsError(f"{field} must be an http(s) URL")
    return u


def _clean_links(links: dict | None) -> dict:
    out = {}
    for key in LINK_KEYS:
        if links and links.get(key):
            validated = validate_url(str(links[key]), field=key)
            if validated:
                out[key] = validated
    return out


def _clean_tags(tags: list | None) -> list[str]:
    if not tags:
        return []
    cleaned = []
    for t in tags[:8]:
        t = str(t).strip().lower()[:24]
        if t and t not in cleaned:
            cleaned.append(t)
    return cleaned


# ── serializers: the ONLY shapes public endpoints may return ────────

def serialize_profile_public(p: PublicProfile, *, project_count: int = 0) -> dict:
    return {
        "handle": p.handle,
        "display_name": p.display_name,
        "bio": p.bio,
        "avatar_url": p.avatar_url,
        "location": p.location,
        "links": p.links_json or {},
        "project_count": project_count,
        "member_since": p.published_at or p.created_at,
        "last_active_at": p.last_active_at,
    }


def serialize_project_public(pr: PublicProject, profile: PublicProfile | None = None) -> dict:
    data = {
        "id": pr.id,
        "name": pr.name,
        "tagline": pr.tagline,
        "description": pr.description,
        "url": pr.url,
        "image_url": pr.image_url,
        "category": pr.category,
        "tags": pr.tags_json or [],
        "status": pr.status,
        "created_at": pr.created_at,
    }
    if profile is not None:
        data["owner"] = {"handle": profile.handle, "display_name": profile.display_name,
                         "avatar_url": profile.avatar_url}
    return data


def serialize_update_public(u: PublicUpdate, profile: PublicProfile | None = None,
                            project: PublicProject | None = None) -> dict:
    data = {
        "id": u.id,
        "text": u.text,
        "url": u.url,
        "image_url": u.image_url,
        "edited": u.edited,
        "created_at": u.created_at,
    }
    if profile is not None:
        data["owner"] = {"handle": profile.handle, "display_name": profile.display_name,
                         "avatar_url": profile.avatar_url}
    if project is not None:
        data["project"] = {"id": project.id, "name": project.name, "url": project.url}
    return data


# ── owner: profile ──────────────────────────────────────────────────

async def get_profile(db: AsyncSession, user_id: str) -> PublicProfile | None:
    return (await db.execute(select(PublicProfile).where(
        PublicProfile.user_id == user_id))).scalar_one_or_none()


async def get_profile_by_handle(db: AsyncSession, handle: str) -> PublicProfile | None:
    return (await db.execute(select(PublicProfile).where(
        PublicProfile.handle == (handle or "").strip().lower()))).scalar_one_or_none()


async def upsert_profile(db: AsyncSession, user_id: str, *, handle: str,
                         display_name: str, bio: str = "", avatar_url: str | None = None,
                         location: str | None = None, links: dict | None = None) -> PublicProfile:
    display_name = (display_name or "").strip()
    if not display_name:
        raise FriendsError("display name is required")
    canonical = normalize_handle(handle)
    existing_handle = await get_profile_by_handle(db, canonical)
    if existing_handle is not None and existing_handle.user_id != user_id:
        raise FriendsError("this handle is already taken")
    profile = await get_profile(db, user_id)
    if profile is None:
        profile = PublicProfile(user_id=user_id, handle=canonical, display_name="")
        db.add(profile)
    profile.handle = canonical
    profile.display_name = display_name[:80]
    profile.bio = (bio or "").strip()[:BIO_MAX_CHARS]
    profile.avatar_url = validate_url(avatar_url, field="avatar_url")
    profile.location = (location or "").strip()[:120] or None
    profile.links_json = _clean_links(links)
    profile.last_active_at = datetime.now(UTC)
    await db.flush()
    return profile


async def set_published(db: AsyncSession, user_id: str, published: bool) -> PublicProfile:
    profile = await get_profile(db, user_id)
    if profile is None:
        raise FriendsError("create your profile first")
    profile.is_published = published
    if published and profile.published_at is None:
        profile.published_at = datetime.now(UTC)
    profile.last_active_at = datetime.now(UTC)
    await db.flush()
    return profile


async def _touch(db: AsyncSession, user_id: str) -> None:
    profile = await get_profile(db, user_id)
    if profile is not None:
        profile.last_active_at = datetime.now(UTC)
        await db.flush()


# ── owner: projects ─────────────────────────────────────────────────

def _validate_project_fields(name: str, category: str, status: str) -> None:
    if not (name or "").strip():
        raise FriendsError("project name is required")
    if category not in CATEGORIES:
        raise FriendsError(f"category must be one of {CATEGORIES}")
    if status not in STATUSES:
        raise FriendsError(f"status must be one of {STATUSES}")


async def create_project(db: AsyncSession, user_id: str, *, name: str, tagline: str = "",
                         description: str = "", url: str | None = None,
                         image_url: str | None = None, category: str = "other",
                         tags: list | None = None, status: str = "active",
                         is_public: bool = True) -> PublicProject:
    _validate_project_fields(name, category, status)
    count = int((await db.execute(select(func.count(PublicProject.id)).where(
        PublicProject.user_id == user_id))).scalar_one())
    if count >= MAX_PUBLIC_PROJECTS_PER_USER:
        raise FriendsError(f"project limit reached ({MAX_PUBLIC_PROJECTS_PER_USER})")
    project = PublicProject(
        user_id=user_id, name=name.strip()[:120], tagline=(tagline or "").strip()[:160],
        description=(description or "").strip()[:4000],
        url=validate_url(url), image_url=validate_url(image_url, field="image_url"),
        category=category, tags_json=_clean_tags(tags), status=status,
        is_public=is_public, published_at=datetime.now(UTC) if is_public else None)
    db.add(project)
    await db.flush()
    await _touch(db, user_id)
    return project


async def get_own_project(db: AsyncSession, user_id: str, project_id: str) -> PublicProject:
    project = (await db.execute(select(PublicProject).where(
        PublicProject.id == project_id, PublicProject.user_id == user_id
    ))).scalar_one_or_none()
    if project is None:
        raise FriendsError("project not found")
    return project


async def update_project(db: AsyncSession, user_id: str, project_id: str, **fields) -> PublicProject:
    project = await get_own_project(db, user_id, project_id)
    name = fields.get("name", project.name)
    category = fields.get("category", project.category)
    status = fields.get("status", project.status)
    _validate_project_fields(name, category, status)
    project.name = name.strip()[:120]
    project.category = category
    project.status = status
    if "tagline" in fields and fields["tagline"] is not None:
        project.tagline = str(fields["tagline"]).strip()[:160]
    if "description" in fields and fields["description"] is not None:
        project.description = str(fields["description"]).strip()[:4000]
    if "url" in fields:
        project.url = validate_url(fields["url"])
    if "image_url" in fields:
        project.image_url = validate_url(fields["image_url"], field="image_url")
    if "tags" in fields and fields["tags"] is not None:
        project.tags_json = _clean_tags(fields["tags"])
    if "is_public" in fields and fields["is_public"] is not None:
        project.is_public = bool(fields["is_public"])
        if project.is_public and project.published_at is None:
            project.published_at = datetime.now(UTC)
    await db.flush()
    await _touch(db, user_id)
    return project


async def delete_project(db: AsyncSession, user_id: str, project_id: str) -> None:
    project = await get_own_project(db, user_id, project_id)
    # detach updates that referenced it (they survive without the project link)
    for u in (await db.execute(select(PublicUpdate).where(
            PublicUpdate.project_id == project_id))).scalars():
        u.project_id = None
    await db.delete(project)
    await db.flush()


# ── owner: updates ──────────────────────────────────────────────────

async def create_update(db: AsyncSession, user_id: str, *, text: str,
                        url: str | None = None, image_url: str | None = None,
                        project_id: str | None = None, is_public: bool = True) -> PublicUpdate:
    text = (text or "").strip()
    if not text:
        raise FriendsError("update text is required")
    if len(text) > UPDATE_MAX_CHARS:
        raise FriendsError(f"updates are limited to {UPDATE_MAX_CHARS} characters")
    since = datetime.now(UTC) - timedelta(days=1)
    recent = int((await db.execute(select(func.count(PublicUpdate.id)).where(
        PublicUpdate.user_id == user_id, PublicUpdate.created_at >= since))).scalar_one())
    if recent >= MAX_UPDATES_PER_DAY:
        raise FriendsError("daily update limit reached — try again tomorrow")
    if project_id:
        await get_own_project(db, user_id, project_id)  # ownership check
    update = PublicUpdate(
        user_id=user_id, project_id=project_id or None, text=text,
        url=validate_url(url), image_url=validate_url(image_url, field="image_url"),
        is_public=is_public)
    db.add(update)
    await db.flush()
    await _touch(db, user_id)
    return update


async def edit_update(db: AsyncSession, user_id: str, update_id: str, **fields) -> PublicUpdate:
    update = (await db.execute(select(PublicUpdate).where(
        PublicUpdate.id == update_id, PublicUpdate.user_id == user_id
    ))).scalar_one_or_none()
    if update is None:
        raise FriendsError("update not found")
    if "text" in fields and fields["text"] is not None:
        text = str(fields["text"]).strip()
        if not text:
            raise FriendsError("update text is required")
        if len(text) > UPDATE_MAX_CHARS:
            raise FriendsError(f"updates are limited to {UPDATE_MAX_CHARS} characters")
        if text != update.text:
            update.text = text
            update.edited = True
    if "url" in fields:
        update.url = validate_url(fields["url"])
    if "image_url" in fields:
        update.image_url = validate_url(fields["image_url"], field="image_url")
    if "project_id" in fields:
        if fields["project_id"]:
            await get_own_project(db, user_id, fields["project_id"])
        update.project_id = fields["project_id"] or None
    if "is_public" in fields and fields["is_public"] is not None:
        update.is_public = bool(fields["is_public"])
    await db.flush()
    return update


async def delete_update(db: AsyncSession, user_id: str, update_id: str) -> None:
    update = (await db.execute(select(PublicUpdate).where(
        PublicUpdate.id == update_id, PublicUpdate.user_id == user_id
    ))).scalar_one_or_none()
    if update is None:
        raise FriendsError("update not found")
    await db.delete(update)
    await db.flush()


# ── public discovery queries ────────────────────────────────────────

def _visible_profiles():
    return select(PublicProfile).where(
        PublicProfile.is_published.is_(True),
        PublicProfile.moderation_status == "active")


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    limit = min(int(limit or PAGE_SIZE_DEFAULT), PAGE_SIZE_MAX)
    return max(limit, 1), max(int(offset or 0), 0)


async def list_public_profiles(db: AsyncSession, *, q: str | None = None,
                               sort: str = "recently_active",
                               limit: int | None = None, offset: int | None = None) -> list[dict]:
    limit, offset = _page(limit, offset)
    query = _visible_profiles()
    if q:
        needle = f"%{q.strip().lower()}%"
        query = query.where(or_(
            func.lower(PublicProfile.display_name).like(needle),
            func.lower(PublicProfile.handle).like(needle),
            func.lower(PublicProfile.bio).like(needle)))
    order = {"recently_active": PublicProfile.last_active_at.desc(),
             "newest": PublicProfile.published_at.desc(),
             "alphabetical": func.lower(PublicProfile.display_name).asc()}
    query = query.order_by(order.get(sort, order["recently_active"])).limit(limit).offset(offset)
    profiles = list((await db.execute(query)).scalars())
    out = []
    for p in profiles:
        count = int((await db.execute(select(func.count(PublicProject.id)).where(
            PublicProject.user_id == p.user_id, PublicProject.is_public.is_(True),
            PublicProject.moderation_status == "active"))).scalar_one())
        out.append(serialize_profile_public(p, project_count=count))
    return out


async def _published_owner_ids(db: AsyncSession) -> select:
    return select(PublicProfile.user_id).where(
        PublicProfile.is_published.is_(True),
        PublicProfile.moderation_status == "active")


async def list_public_projects(db: AsyncSession, *, q: str | None = None,
                               category: str | None = None, handle: str | None = None,
                               limit: int | None = None, offset: int | None = None) -> list[dict]:
    limit, offset = _page(limit, offset)
    owner_ids = await _published_owner_ids(db)
    query = select(PublicProject, PublicProfile).join(
        PublicProfile, PublicProfile.user_id == PublicProject.user_id).where(
        PublicProject.is_public.is_(True),
        PublicProject.moderation_status == "active",
        PublicProject.user_id.in_(owner_ids))
    if category and category != "all":
        query = query.where(PublicProject.category == category)
    if handle:
        query = query.where(PublicProfile.handle == handle.strip().lower())
    if q:
        needle = f"%{q.strip().lower()}%"
        query = query.where(or_(
            func.lower(PublicProject.name).like(needle),
            func.lower(PublicProject.tagline).like(needle),
            func.lower(cast(PublicProject.tags_json, SAString)).like(needle)))
    query = query.order_by(PublicProject.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(query)).all()
    return [serialize_project_public(pr, profile) for pr, profile in rows]


async def list_public_updates(db: AsyncSession, *, handle: str | None = None,
                              limit: int | None = None, offset: int | None = None) -> list[dict]:
    limit, offset = _page(limit, offset)
    owner_ids = await _published_owner_ids(db)
    query = select(PublicUpdate, PublicProfile).join(
        PublicProfile, PublicProfile.user_id == PublicUpdate.user_id).where(
        PublicUpdate.is_public.is_(True),
        PublicUpdate.moderation_status == "active",
        PublicUpdate.user_id.in_(owner_ids))
    if handle:
        query = query.where(PublicProfile.handle == handle.strip().lower())
    query = query.order_by(PublicUpdate.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(query)).all()
    out = []
    for u, profile in rows:
        project = None
        if u.project_id:
            project = (await db.execute(select(PublicProject).where(
                PublicProject.id == u.project_id,
                PublicProject.is_public.is_(True),
                PublicProject.moderation_status == "active"))).scalar_one_or_none()
        out.append(serialize_update_public(u, profile, project))
    return out


async def public_profile_page(db: AsyncSession, handle: str) -> dict | None:
    profile = await get_profile_by_handle(db, handle)
    if profile is None or not profile.is_published or profile.moderation_status != "active":
        return None
    projects = await list_public_projects(db, handle=profile.handle, limit=PAGE_SIZE_MAX)
    updates = await list_public_updates(db, handle=profile.handle, limit=PAGE_SIZE_DEFAULT)
    return {**serialize_profile_public(profile, project_count=len(projects)),
            "projects": projects, "updates": updates}


# ── operator moderation (superuser only — enforced at the route) ────

async def moderate(db: AsyncSession, *, entity: str, entity_id: str,
                   status: str, reason: str | None = None) -> None:
    if status not in ("active", "hidden"):
        raise FriendsError("moderation status must be active|hidden")
    model = {"profile": PublicProfile, "project": PublicProject,
             "update": PublicUpdate}.get(entity)
    if model is None:
        raise FriendsError("entity must be profile|project|update")
    row = (await db.execute(select(model).where(model.id == entity_id))).scalar_one_or_none()
    if row is None:
        raise FriendsError(f"{entity} not found")
    row.moderation_status = status
    row.moderation_reason = (reason or "")[:300] or None
    await db.flush()
