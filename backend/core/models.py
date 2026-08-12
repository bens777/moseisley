"""SQLAlchemy models — full Moseisley.sh V0.1 schema (MASTER_SPEC §99).

Conventions:
- UUIDv4 string primary keys (portable across PostgreSQL and SQLite).
- Every user-owned entity carries user_id (§40).
- Money is integer cents + ISO currency code. No floats in Treasury paths.
- JSON columns use the portable JSON type.
- `events` is the append-only Ledger: mutation of persisted rows raises (§17).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    event as sa_event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base, TimestampMixin):
    """Application-owned identity (self-hosted auth; no external identity provider)."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(1024), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    autonomy_mode: Mapped[str] = mapped_column(String(16), default="assisted")  # advisory|assisted|autonomous
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AccessToken(Base):
    """Database-backed session tokens: logout/invalidation is a row delete."""

    __tablename__ = "access_tokens"
    token: Mapped[str] = mapped_column(String(43), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="cascade"),
                                         index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FileRef(Base, TimestampMixin):
    """BYOS file metadata (architecture update): the original file stays in the user's
    storage; Moseisley.sh stores reference metadata only."""

    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("user_id", "storage_provider", "path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    storage_provider: Mapped[str] = mapped_column(String(32))  # local|s3|byos:<connection_id>
    external_id: Mapped[str | None] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str | None] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    checksum: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexing_status: Mapped[str] = mapped_column(String(16), default="none")  # none|indexed|error
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentChunk(Base):
    """Optional retrieval chunks. Embeddings are OPTIONAL (JSON-encoded vector +
    provider/model); the system functions fully without them. pgvector can replace
    the JSON column later via migration without app changes elsewhere."""

    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding_json: Mapped[list | None] = mapped_column(JSON)
    embedding_provider: Mapped[str | None] = mapped_column(String(48))
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("user_id", "path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    content_md: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    metric: Mapped[str] = mapped_column(String(120))
    target_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))       # e.g. EUR, hours, count
    currency: Mapped[str | None] = mapped_column(String(3))
    deadline: Mapped[str | None] = mapped_column(String(10))   # ISO date YYYY-MM-DD
    constraints_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|achieved|abandoned
    progress: Mapped[float] = mapped_column(Float, default=0.0)        # 0..1
    confidence: Mapped[float | None] = mapped_column(Float)            # 0..1 on-track confidence


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|experiment|hold|killed|completed
    linked_goal_ids: Mapped[list] = mapped_column(JSON, default=list)
    strategy: Mapped[str | None] = mapped_column(Text)
    estimated_future_value_cents: Mapped[int | None] = mapped_column(Integer)
    estimated_future_cost_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    confidence: Mapped[float | None] = mapped_column(Float)
    # §62: killed = archived, never erased. §101: sunk cost recorded but not used for future value.
    past_time_minutes: Mapped[int] = mapped_column(Integer, default=0)
    past_cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Third pass §9/§42: real-world assets this project maps to.
    urls_json: Mapped[dict] = mapped_column(JSON, default=dict)  # website|repository|checkout|analytics|other
    capital_allocated_cents: Mapped[int] = mapped_column(Integer, default=0)


class RevenueEvent(Base, TimestampMixin):
    """Canonical verified-revenue record (third pass §4-§8).

    STRICT TRUTH RULE: rows exist only for money that was actually verified
    (or explicitly declared by the user, source='manual'). Opportunities,
    pipeline and unpaid invoices are NEVER RevenueEvents. Aggregations count
    the lowest defensible value and exclude reversed events.
    """
    __tablename__ = "revenue_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))  # stripe|payment_provider|platform_api|affiliate|marketplace|manual
    source_ref: Mapped[str | None] = mapped_column(String(255))  # external id (charge/invoice/subscription id)
    description: Mapped[str] = mapped_column(String(500), default="")
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_interval: Mapped[str | None] = mapped_column(String(16))  # monthly|yearly|weekly
    verification_status: Mapped[str] = mapped_column(String(16), default="verified")  # verified|reversed
    reversal_of: Mapped[str | None] = mapped_column(String(36))  # revenue_event id this reverses
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)  # reference/url/raw payload extract
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Instruction(Base, TimestampMixin):
    """User-visible automation / operating instruction (third pass §14-§16, §34).

    PostgreSQL-canonical JSON control layer: config_json is the structured
    representation shown/edited in the dashboard. Market Watches are
    Instructions with kind='market_watch'; the Dev weekly review is
    kind='dev_review'. Scheduling is realized through scheduled_jobs with
    idempotency_key = 'instruction:{id}'.
    """
    __tablename__ = "instructions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # market_watch|goal_review|budget_rule|project_instruction|dev_review|agent_policy|custom
    kind: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_role: Mapped[str | None] = mapped_column(String(32))  # crew role that executes it
    provider: Mapped[str | None] = mapped_column(String(48))  # optional model override
    model: Mapped[str | None] = mapped_column(String(128))
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)  # canonical structured instruction
    schedule_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {frequency,time,timezone,weekday}
    delivery_json: Mapped[list] = mapped_column(JSON, default=list)  # ["telegram","dashboard"]
    created_by: Mapped[str] = mapped_column(String(16), default="user")  # user|manager
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|error
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result_json: Mapped[dict] = mapped_column(JSON, default=dict)


class InstructionVersion(Base):
    """Immutable version history for instructions (third pass §47)."""
    __tablename__ = "instruction_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instruction_id: Mapped[str] = mapped_column(String(36), ForeignKey("instructions.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)  # full instruction state at this version
    changed_by: Mapped[str] = mapped_column(String(16), default="user")  # user|manager
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketReport(Base, TimestampMixin):
    """Stored result of a Market Watch run (third pass §35-§36, §64)."""
    __tablename__ = "market_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    instruction_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("instructions.id"), index=True)
    crew_run_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="completed")  # completed|failed|no_material_change
    sentiment: Mapped[str | None] = mapped_column(String(24))  # positive|mixed|negative|no_material_change
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)  # changes/narratives/opportunities/threats...
    sources_json: Mapped[list] = mapped_column(JSON, default=list)  # citations/urls
    sample_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {size,window,methodology} when quantified
    query_json: Mapped[dict] = mapped_column(JSON, default=dict)  # query/time window actually used
    delivered_json: Mapped[list] = mapped_column(JSON, default=list)  # channels actually delivered to


class DevProposal(Base, TimestampMixin):
    """Dev Agent proposal + patch lifecycle (third pass §20-§26).

    Approval is bound to patch_hash: any change to the patch invalidates a
    prior approval (enforced in backend/agents/dev.py, never inferred).
    """
    __tablename__ = "dev_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    why: Mapped[str] = mapped_column(Text, default="")
    expected_benefit: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    plan_md: Mapped[str] = mapped_column(Text, default="")
    files_affected_json: Mapped[list] = mapped_column(JSON, default=list)
    schema_impact: Mapped[str] = mapped_column(String(500), default="")
    risk: Mapped[str] = mapped_column(String(16), default="medium")  # low|medium|high
    test_plan: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    # proposed|patch_ready|approved|rejected|merged|deployed|failed
    branch_name: Mapped[str | None] = mapped_column(String(128))
    patch_hash: Mapped[str | None] = mapped_column(String(64))  # sha256 of the exact patch
    patch_stats_json: Mapped[dict] = mapped_column(JSON, default=dict)  # files +/-, insertions, deletions
    test_results_json: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_id: Mapped[str | None] = mapped_column(String(36))  # approval_requests.id
    approved_patch_hash: Mapped[str | None] = mapped_column(String(64))  # hash that was approved
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_commit: Mapped[str | None] = mapped_column(String(64))


class ProviderConnection(Base, TimestampMixin):
    __tablename__ = "provider_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(48))  # openai|xai|anthropic|custom|mock
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    display_hint: Mapped[str | None] = mapped_column(String(64))  # masked key, never the secret
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict)  # base_url, default models...


class IntegrationConnection(Base, TimestampMixin):
    __tablename__ = "integration_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    integration_type: Mapped[str] = mapped_column(String(32))  # google|mcp|rest|webhook|n8n|stripe
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="connected")  # connected|error|disconnected
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # capability -> permission level: DENIED|READ|DRAFT|EXECUTE (§34)
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean)


class AgentConfig(Base, TimestampMixin):
    __tablename__ = "agent_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    adapter_type: Mapped[str] = mapped_column(String(32))  # native|custom_http|hermes|openclaw
    display_name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)  # active agent for routing
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)  # e.g. custom agent auth header value
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok|error|unknown


class AgentInspection(Base):
    """One screening of one reply from an EXTERNAL agent runtime.

    Quarantined content lives HERE and nowhere else: it is deliberately not a
    ChatMessage, because chat history is exactly what feeds the next agent turn.
    Releasing it is a user decision that copies it into the conversation.
    """

    __tablename__ = "agent_inspections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent_name: Mapped[str] = mapped_column(String(120), default="")
    adapter_type: Mapped[str] = mapped_column(String(32), default="")
    session_id: Mapped[str | None] = mapped_column(String(36))
    verdict: Mapped[str] = mapped_column(String(16), index=True)  # none|suspicious|malicious
    stage: Mapped[str] = mapped_column(String(16), default="deterministic")
    # deterministic|llm|strict_mode|error
    reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="passed", index=True)
    # passed|quarantined|blocked|approved|discarded
    content: Mapped[str | None] = mapped_column(Text)   # held text; cleared on discard
    content_chars: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentSession(Base, TimestampMixin):
    __tablename__ = "agent_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agent_configs.id"))
    title: Mapped[str | None] = mapped_column(String(300))
    channel: Mapped[str] = mapped_column(String(16), default="web")  # canonical; shared across channels
    external_session_ref: Mapped[str | None] = mapped_column(String(128))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant|system|tool
    content: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web|telegram
    agent_id: Mapped[str | None] = mapped_column(String(36))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TelegramBinding(Base, TimestampMixin):
    __tablename__ = "telegram_bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)
    telegram_user_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(32))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    voice_reply_mode: Mapped[str] = mapped_column(String(16), default="off")  # off|voice_only|text_and_voice


class TelegramPairingCode(Base):
    __tablename__ = "telegram_pairing_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256; raw code never stored
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    """Append-only Ledger (§17). Rows are immutable once persisted."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_user_created", "user_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="system")  # user|system|agent
    actor_id: Mapped[str | None] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(48))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@sa_event.listens_for(Event, "before_update")
def _forbid_event_update(mapper, connection, target):  # pragma: no cover - guard
    raise RuntimeError("Ledger events are append-only and cannot be updated")


@sa_event.listens_for(Event, "before_delete")
def _forbid_event_delete(mapper, connection, target):  # pragma: no cover - guard
    raise RuntimeError("Ledger events are append-only and cannot be deleted")


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"))
    reason: Mapped[str] = mapped_column(Text)
    alternatives_json: Mapped[list] = mapped_column(JSON, default=list)
    selected_action: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"))
    experiment_id: Mapped[str | None] = mapped_column(String(36))
    decision_id: Mapped[str | None] = mapped_column(String(36))
    statement: Mapped[str] = mapped_column(Text)
    probability: Mapped[float | None] = mapped_column(Float)  # 0..1
    metric: Mapped[str | None] = mapped_column(String(120))
    target_value: Mapped[float | None] = mapped_column(Float)
    deadline: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Outcome(Base):
    __tablename__ = "outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    prediction_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("predictions.id"))
    observed_value: Mapped[float | None] = mapped_column(Float)
    observed_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(120), default="manual")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketSignal(Base):
    __tablename__ = "market_signals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(48))  # xai_x_search|web|manual|...
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1024))
    # Evidence ladder (§66): attention|interest|pain|commercial_intent|purchase|revenue
    evidence_level: Mapped[str] = mapped_column(String(24), default="attention")
    strength: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Opportunity(Base, TimestampMixin):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    buyer: Mapped[str | None] = mapped_column(String(300))
    problem: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)  # market_signal ids + snippets
    attention_score: Mapped[float] = mapped_column(Float, default=0.0)
    pain_score: Mapped[float] = mapped_column(Float, default=0.0)
    commercial_intent_score: Mapped[float] = mapped_column(Float, default=0.0)
    competition_score: Mapped[float] = mapped_column(Float, default=0.0)
    strategic_fit_score: Mapped[float] = mapped_column(Float, default=0.0)
    time_to_market_score: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_test_cost_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="detected")
    # detected|micro_test|validated|incubating|scaling|rejected


class Experiment(Base, TimestampMixin):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("opportunities.id"))
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id"))
    hypothesis: Mapped[str] = mapped_column(Text)
    expected_result: Mapped[str | None] = mapped_column(Text)
    metric: Mapped[str | None] = mapped_column(String(120))
    deadline: Mapped[str | None] = mapped_column(String(10))
    cash_budget_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    human_time_budget_minutes: Mapped[int] = mapped_column(Integer, default=0)
    success_criterion: Mapped[str] = mapped_column(Text, default="")
    kill_criterion: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # draft|running|succeeded|killed|inconclusive
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="treasury")  # treasury|llm
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    monthly_limit_cents: Mapped[int | None] = mapped_column(Integer)
    daily_limit_cents: Mapped[int | None] = mapped_column(Integer)
    per_transaction_hard_limit_cents: Mapped[int | None] = mapped_column(Integer)
    autonomous_threshold_cents: Mapped[int | None] = mapped_column(Integer)
    approval_threshold_cents: Mapped[int | None] = mapped_column(Integer)
    allowed_categories: Mapped[list] = mapped_column(JSON, default=list)   # empty = all allowed
    blocked_categories: Mapped[list] = mapped_column(JSON, default=list)
    allowed_vendors: Mapped[list] = mapped_column(JSON, default=list)      # empty = all allowed
    blocked_vendors: Mapped[list] = mapped_column(JSON, default=list)
    spending_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class PaymentMethod(Base, TimestampMixin):
    __tablename__ = "payment_methods"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # simulated|stripe_test
    provider_ref: Mapped[str] = mapped_column(String(128))  # provider token/id only (§75)
    display_label: Mapped[str] = mapped_column(String(64))  # e.g. "card •••• 4821"


class AgentPaymentBinding(Base, TimestampMixin):
    __tablename__ = "agent_payment_bindings"
    __table_args__ = (UniqueConstraint("user_id", "agent_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_configs.id"))
    spending_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_autonomous_cents: Mapped[int | None] = mapped_column(Integer)


class SpendIntent(Base, TimestampMixin):
    __tablename__ = "spend_intents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36))
    project_id: Mapped[str | None] = mapped_column(String(36))
    experiment_id: Mapped[str | None] = mapped_column(String(36))
    purpose: Mapped[str] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(64))
    vendor: Mapped[str | None] = mapped_column(String(120))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending|auto_approved|awaiting_approval|approved|denied|executed|failed
    decision_reason: Mapped[str | None] = mapped_column(Text)
    approval_request_id: Mapped[str | None] = mapped_column(String(36))
    transaction_id: Mapped[str | None] = mapped_column(String(36))


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    spend_intent_id: Mapped[str] = mapped_column(String(36), ForeignKey("spend_intents.id"))
    provider: Mapped[str] = mapped_column(String(32))
    provider_transaction_ref: Mapped[str | None] = mapped_column(String(128))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|success|failed|unknown


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(64))  # spend|gmail_send|calendar_write|...
    action_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[int] = mapped_column(Integer, default=3)  # §57 levels
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|denied|expired
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_channel: Mapped[str | None] = mapped_column(String(16))  # dashboard|telegram


class XRayRun(Base):
    __tablename__ = "xray_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=90)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|completed|failed
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class XRayFinding(Base):
    __tablename__ = "xray_findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("xray_runs.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    # found_money|estimated_opportunity|found_time|goal_drift|lost_commitment|project_drift|
    # automatable_work|shadow_backtest
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    value_type: Mapped[str | None] = mapped_column(String(24))  # money|time|none
    estimated_value_cents: Mapped[int | None] = mapped_column(Integer)
    estimated_time_minutes: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # verified vs estimated (§42-43)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    source_references_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|actioned|dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduledJob(Base, TimestampMixin):
    __tablename__ = "scheduled_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    # scheduled|running|done|failed|cancelled
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)  # recurring when set
    cron_hint: Mapped[str | None] = mapped_column(String(64))      # human-readable schedule label
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # idempotency: jobs with the same key won't be enqueued twice while one is scheduled/running
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)


class TradingWebhook(Base, TimestampMixin):
    """The per-user inbound endpoint for that user's own TradingView alerts.

    The URL token is `selector.verifier`: the selector is indexed so we can find
    the row without scanning, and only a SHA-256 of the verifier is stored, so a
    database leak does not hand anyone a working endpoint. The verifier itself
    is shown once, at creation.
    """

    __tablename__ = "trading_webhooks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    selector: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    verifier_hash: Mapped[str] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signal_count: Mapped[int] = mapped_column(Integer, default=0)


class TradingSignal(Base):
    """One alert received from the user's own TradingView strategy.

    This is a JOURNAL, not a portfolio: it records what arrived and what the
    assistant suggested. No order is ever placed, no position is ever held, and
    no money — real or simulated — moves anywhere in this feature.
    """

    __tablename__ = "trading_signals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    action: Mapped[str] = mapped_column(String(8))            # buy|sell|close
    price: Mapped[str] = mapped_column(String(32))            # exact decimal as text
    stop: Mapped[str | None] = mapped_column(String(32))
    strategy: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(200), default="")
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)   # sanitized
    screening: Mapped[dict] = mapped_column(JSON, default=dict)     # verdict + reasons
    recommendation: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), index=True)


class ChallengeDecision(Base):
    """One trading decision in the Darvas Challenge — FICTIONAL money only.

    Platform-level (no user_id): there is exactly one public challenge. Every
    row records WHY, not just what: the box that produced the signal. The
    public page renders this table verbatim — the transparency is the point.
    """

    __tablename__ = "challenge_decisions"
    __table_args__ = (UniqueConstraint("trade_date", "symbol", "action"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)   # ISO date
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(8))                    # buy|sell|trail
    reason: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[str] = mapped_column(String(32), default="0")       # exact decimal as text
    units: Mapped[str] = mapped_column(String(32), default="0")
    box_top: Mapped[str] = mapped_column(String(32), default="0")
    box_bottom: Mapped[str] = mapped_column(String(32), default="0")
    stop: Mapped[str] = mapped_column(String(32), default="0")
    cash_cents_after: Mapped[int] = mapped_column(Integer, default=0)
    equity_cents_after: Mapped[int] = mapped_column(Integer, default=0)
    realized_pnl_cents: Mapped[int | None] = mapped_column(Integer)   # sells only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChallengeSnapshot(Base):
    """End-of-day mark of the fictional portfolio. One row per date — this is
    the equity curve, and the record of any day the data feed was down."""

    __tablename__ = "challenge_snapshots"
    __table_args__ = (UniqueConstraint("trade_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|paused
    equity_cents: Mapped[int] = mapped_column(Integer, default=0)
    cash_cents: Mapped[int] = mapped_column(Integer, default=0)
    positions_json: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)


class LlmUsage(Base):
    """Central AI usage record (owner directive §27-28). NULL means unknown — never guessed."""

    __tablename__ = "llm_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(120))          # actual model when known
    requested_model: Mapped[str | None] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(32))
    crew_role: Mapped[str | None] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64))
    orchestrator_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)  # third pass: project attribution
    provider_request_id: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    provider_reported_cost: Mapped[float | None] = mapped_column(Float)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    cost_currency: Mapped[str] = mapped_column(String(3), default="USD")
    cost_source: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    # PROVIDER_REPORTED | ESTIMATED | UNKNOWN
    pricing_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="success")  # success | failed | unknown
    estimated_cost_millicents: Mapped[int] = mapped_column(Integer, default=0)  # legacy compat
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Memory(Base, TimestampMixin):
    """Structured memory (owner directive §23-25): canonical in PostgreSQL, exposed as a
    JSON workspace. Provenance is preserved; AI inference never silently becomes FACT."""

    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("user_id", "memory_type", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    memory_type: Mapped[str] = mapped_column(String(16), default="fact")
    # fact | preference | belief | prediction | decision | result
    key: Mapped[str] = mapped_column(String(200))
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {"value": ..., "note": ...}
    provenance: Mapped[str] = mapped_column(String(32), default="USER_EXPLICIT")
    # USER_EXPLICIT | INTEGRATION_OBSERVATION | SYSTEM_INFERENCE | CREW_ANALYSIS
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived


class CrewConfig(Base, TimestampMixin):
    """Per-user Crew ROLE configuration (role ≠ runtime). Model policy: inherit | custom."""

    __tablename__ = "crew_configs"
    __table_args__ = (UniqueConstraint("user_id", "role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    model_policy: Mapped[str] = mapped_column(String(16), default="inherit")  # inherit | custom
    provider: Mapped[str | None] = mapped_column(String(48))
    model: Mapped[str | None] = mapped_column(String(120))
    custom_prompt: Mapped[str | None] = mapped_column(Text)
    uses_default_prompt: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_version: Mapped[int] = mapped_column(Integer, default=0)


class CrewRun(Base):
    """Bounded delegation tracking (§18). Prevents uncontrolled recursion; feeds the UI."""

    __tablename__ = "crew_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36))
    orchestrator_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)  # third pass: project attribution
    crew_role: Mapped[str] = mapped_column(String(32))
    runtime: Mapped[str] = mapped_column(String(32), default="native")
    provider: Mapped[str | None] = mapped_column(String(48))
    requested_model: Mapped[str | None] = mapped_column(String(120))
    actual_model: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="running")
    # running | completed | failed | blocked
    task_summary: Mapped[str | None] = mapped_column(String(500))
    result_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelCatalogEntry(Base):
    """Server-side model discovery cache (§11). Global, refreshed per provider."""

    __tablename__ = "model_catalog"
    __table_args__ = (UniqueConstraint("provider", "model_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(48), index=True)
    model_id: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    context_window: Mapped[int | None] = mapped_column(Integer)
    pricing_json: Mapped[dict] = mapped_column(JSON, default=dict)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="live")  # live | fallback
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelPricingSnapshot(Base):
    """Versioned pricing (§32). Historical snapshots are never rewritten."""

    __tablename__ = "model_pricing_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(48), index=True)
    model_id: Mapped[str] = mapped_column(String(200), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    input_per_million: Mapped[float | None] = mapped_column(Float)
    cached_input_per_million: Mapped[float | None] = mapped_column(Float)
    output_per_million: Mapped[float | None] = mapped_column(Float)
    additional_pricing_json: Mapped[dict] = mapped_column(JSON, default=dict)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(32), default="official_docs")
    source_reference: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SubscriptionState(Base, TimestampMixin):
    """Cloud Premium subscription state, synchronized from Stripe (authoritative)."""

    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="none")
    # none | active | trialing | past_due | canceled | incomplete
    price_id: Mapped[str | None] = mapped_column(String(64))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)



# ═══ FRIENDS OF THE CANTINA — public community layer (2026-08-11) ═══
# Strictly separated from private account data and from the INTERNAL Project
# entity used by the AI crew. Nothing here is publicly visible unless the
# owner explicitly published their profile (is_published) AND the row's
# moderation_status is 'active'.

class PublicProfile(Base, TimestampMixin):
    __tablename__ = "public_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True, index=True)
    handle: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # lowercase canonical
    display_name: Mapped[str] = mapped_column(String(80))
    bio: Mapped[str] = mapped_column(String(300), default="")
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    location: Mapped[str | None] = mapped_column(String(120))  # only if user set it here
    links_json: Mapped[dict] = mapped_column(JSON, default=dict)  # website|x|github|linkedin|youtube|newsletter|other
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    moderation_status: Mapped[str] = mapped_column(String(16), default="active")  # active|hidden
    moderation_reason: Mapped[str | None] = mapped_column(String(300))


class PublicProject(Base, TimestampMixin):
    """A public Friends project — NOT the internal operational Project."""

    __tablename__ = "public_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    tagline: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    category: Mapped[str] = mapped_column(String(24), default="other", index=True)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|building|launched|paused
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # future explicit bridge to an internal project — never auto-synced (§23)
    source_internal_project_id: Mapped[str | None] = mapped_column(String(36))
    moderation_status: Mapped[str] = mapped_column(String(16), default="active")
    moderation_reason: Mapped[str | None] = mapped_column(String(300))


class PublicUpdate(Base, TimestampMixin):
    __tablename__ = "public_updates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("public_projects.id"), index=True)
    text: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    moderation_status: Mapped[str] = mapped_column(String(16), default="active")
    moderation_reason: Mapped[str | None] = mapped_column(String(300))
