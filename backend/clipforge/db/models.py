"""ORM models — PostgreSQL is the authoritative store for all application data.

Relationships preserved end to end (Part 10): source -> project -> clips ->
renders -> output packages -> publications -> metric snapshots -> learning
records, plus the strategy version each clip was produced under.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, new_id, utcnow

JSON = JSONB


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=new_id)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# Accounts, sessions, audit, secrets, settings
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner|admin|editor|viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = _created()
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))

    user: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class Secret(Base):
    """Encrypted credential (AES-256-GCM). Plaintext never leaves the backend."""

    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = _pk()
    purpose: Mapped[str] = mapped_column(String(120), index=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(16))
    hint: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# ---------------------------------------------------------------------------
# AI Model Connection Hub
# ---------------------------------------------------------------------------


class ProviderConnection(Base):
    __tablename__ = "provider_connections"

    id: Mapped[uuid.UUID] = _pk()
    provider_type: Mapped[str] = mapped_column(String(40))  # openai|gemini|anthropic|openai_compatible|ollama
    name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str | None] = mapped_column(String(500))
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    models: Mapped[list[ProviderModel]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", order_by="ProviderModel.model_id"
    )


class ProviderModel(Base):
    __tablename__ = "provider_models"
    __table_args__ = (UniqueConstraint("connection_id", "model_id"),)

    id: Mapped[uuid.UUID] = _pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    context_window: Mapped[int | None] = mapped_column(Integer)
    quality_tier: Mapped[int] = mapped_column(Integer, default=2)  # 1 fast/cheap .. 3 best
    price_in_per_mtok: Mapped[float | None] = mapped_column(Float)
    price_out_per_mtok: Mapped[float | None] = mapped_column(Float)
    price_source: Mapped[str] = mapped_column(String(60), default="unknown")
    latency_ms_ema: Mapped[float | None] = mapped_column(Float)
    success_ema: Mapped[float] = mapped_column(Float, default=1.0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()

    connection: Mapped[ProviderConnection] = relationship(back_populates="models")


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_created", "created_at"),)

    id: Mapped[uuid.UUID] = _pk()
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="SET NULL")
    )
    provider_type: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    task: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_kind: Mapped[str | None] = mapped_column(String(40))
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = _created()


class LLMCache(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeItem(Base):
    """Cross-model knowledge (Part 9): never blindly trusted, always attributed."""

    __tablename__ = "knowledge_items"

    id: Mapped[uuid.UUID] = _pk()
    topic: Mapped[str] = mapped_column(String(200), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    responses: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    providers: Mapped[list[str]] = mapped_column(JSON, default=list)
    agreement: Mapped[float | None] = mapped_column(Float)
    disagreements: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.3)
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified")
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ---------------------------------------------------------------------------
# Brain chat
# ---------------------------------------------------------------------------


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # user|assistant|system
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------------------
# Projects, sources, media
# ---------------------------------------------------------------------------


class Source(Base):
    """A submitted link (or upload). Deduplicated on (platform, external_id)."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_source_platform_external"),)

    id: Mapped[uuid.UUID] = _pk()
    original_url: Mapped[str | None] = mapped_column(Text)
    normalized_url: Mapped[str | None] = mapped_column(Text, index=True)
    platform: Mapped[str] = mapped_column(String(40))  # youtube|instagram|direct|gdrive|upload|live_ingest
    source_type: Mapped[str] = mapped_column(String(40))  # video|livestream|reel|post|file|stream
    external_id: Mapped[str] = mapped_column(String(300))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    access_status: Mapped[str] = mapped_column(String(40), default="UNRESOLVED")
    access_detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    live_status: Mapped[str | None] = mapped_column(String(30))
    transcript_status: Mapped[str] = mapped_column(String(30), default="none")
    media_status: Mapped[str] = mapped_column(String(30), default="none")
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL", use_alter=True, name="fk_source_media_asset")
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    last_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), default="Untitled project")
    instruction: Mapped[str] = mapped_column(Text, default="")
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # parsed parameters
    status: Mapped[str] = mapped_column(String(40), default="created")
    status_label: Mapped[str] = mapped_column(String(80), default="Created")
    analysis_level: Mapped[str | None] = mapped_column(String(30))  # full_media|transcript_only|metadata_only
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    source: Mapped[Source | None] = relationship()


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE", use_alter=True, name="fk_asset_source"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))  # source|mezzanine|audio|clip|thumbnail|subtitle|frame|live_segment|library
    storage_key: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    duration_s: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = _pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(40))  # asr|platform_captions|live
    engine: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(120), default="")
    language: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(30), default="running")
    word_level: Mapped[bool] = mapped_column(Boolean, default=True)
    storage_key: Mapped[str | None] = mapped_column(Text)  # JSON words file
    text_preview: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_confidence: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class TranscriptChunk(Base):
    """Checkpoint unit for chunked, resumable transcription of long media."""

    __tablename__ = "transcript_chunks"
    __table_args__ = (UniqueConstraint("transcript_id", "idx"),)

    id: Mapped[uuid.UUID] = _pk()
    transcript_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _updated()


class Analysis(Base):
    """Reusable analysis artifacts (scenes, loudness, silences, faces, audio events)."""

    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("source_id", "kind", "version"),)

    id: Mapped[uuid.UUID] = _pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(20), default="1")
    data: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(300), default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    transcript_text: Mapped[str] = mapped_column(Text, default="")
    scores: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scoring_method: Mapped[str] = mapped_column(String(60), default="local_heuristic")
    status: Mapped[str] = mapped_column(String(30), default="candidate")  # candidate|selected|rendered|rejected|failed
    edit_plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: First-3-seconds hook and loop-seam scores (None until those stages ran).
    hook_score: Mapped[float | None] = mapped_column(Float)
    loop_score: Mapped[float | None] = mapped_column(Float)
    #: hook, loop, retention_plan, packaging_variants, originality_flags, publish_gate_result, analytics
    shorts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    experiment_variant: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Render(Base):
    __tablename__ = "renders"

    id: Mapped[uuid.UUID] = _pk()
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    aspect: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    video_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    srt_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    vtt_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    thumbnail_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    captions_burned: Mapped[bool] = mapped_column(Boolean, default=True)
    edit_decisions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    subtitle_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class OutputPackage(Base):
    """Platform-specific publishing package (Part 16)."""

    __tablename__ = "output_packages"
    __table_args__ = (UniqueConstraint("clip_id", "platform"),)

    id: Mapped[uuid.UUID] = _pk()
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    render_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("renders.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(20))  # youtube|instagram
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    thumbnail_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    cover_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    subtitle_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    metadata_method: Mapped[str] = mapped_column(String(60), default="local_template")
    privacy: Mapped[str] = mapped_column(String(20), default="private")
    # draft|ready|approved|scheduled|published|failed|missed
    status: Mapped[str] = mapped_column(String(20), default="ready")
    #: When the owner wants it to go live (None = as soon as it is approved).
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ---------------------------------------------------------------------------
# Social accounts & publishing
# ---------------------------------------------------------------------------


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("platform", "external_account_id"),)

    id: Mapped[uuid.UUID] = _pk()
    platform: Mapped[str] = mapped_column(String(20))  # youtube|instagram|google_drive
    external_account_id: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(300), default="")
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="connected")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    connected_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_verifier: Mapped[str | None] = mapped_column(String(200))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------------------
# Batches: many videos, one recipe (a folder picked in the browser or watched)
# ---------------------------------------------------------------------------


class WatchFolder(Base):
    """A folder under the inbox root whose videos are processed automatically."""

    __tablename__ = "watch_folders"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(1000), unique=True)  # relative to the inbox root ("" = the root)
    recipe: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=True)
    after_processing: Mapped[str] = mapped_column(String(20), default="keep")  # keep|move
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("batches.id", ondelete="SET NULL", use_alter=True,
                                                                  name="fk_watch_folder_batch"))
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # files seen but not yet stable
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(20))  # upload|folder
    watch_folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("watch_folders.id", ondelete="SET NULL"))
    recipe: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|running|paused|completed|cancelled
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # e.g. the next free posting slot
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class BatchItem(Base):
    __tablename__ = "batch_items"
    __table_args__ = (UniqueConstraint("batch_id", "filename", "size_bytes", "mtime"),)

    id: Mapped[uuid.UUID] = _pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(1000))  # path inside the folder, or the uploaded file's name
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|copying|started|duplicate|failed|cancelled
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[uuid.UUID] = _pk()
    package_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("output_packages.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("social_accounts.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(20))
    #: Unique per (platform, account, rendered file content) — the duplicate
    #: publication guard (acceptance test 25).
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    external_id: Mapped[str | None] = mapped_column(String(200), index=True)
    external_url: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    upload_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class CaptionTrack(Base):
    __tablename__ = "caption_tracks"
    __table_args__ = (UniqueConstraint("publication_id", "language"),)

    id: Mapped[uuid.UUID] = _pk()
    publication_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str | None] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class PlatformQuotaUsage(Base):
    __tablename__ = "platform_quota_usage"
    __table_args__ = (UniqueConstraint("platform", "day"),)

    id: Mapped[uuid.UUID] = _pk()
    platform: Mapped[str] = mapped_column(String(20))
    day: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (Pacific time for YouTube)
    units: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Analytics, strategies, experiments, learning
# ---------------------------------------------------------------------------


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (Index("ix_metric_pub_collected", "publication_id", "collected_at"),)

    id: Mapped[uuid.UUID] = _pk()
    publication_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(20))
    collected_at: Mapped[datetime] = _created()
    age_hours: Mapped[float | None] = mapped_column(Float)
    #: Only metrics the platform actually returned — never invented.
    metrics: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    unavailable: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(60))


class Strategy(Base):
    """Versioned production strategy. Never overwritten — new versions only."""

    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("version"),)

    id: Mapped[uuid.UUID] = _pk()
    version: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="candidate")  # active|candidate|retired|experiment
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence_record_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _created()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    hypothesis: Mapped[str] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(20))
    control_strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id"))
    variant_strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id"))
    metric: Mapped[str] = mapped_column(String(60))
    allocation: Mapped[float] = mapped_column(Float, default=0.5)
    min_samples_per_arm: Mapped[int] = mapped_column(Integer, default=8)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|concluded|stopped
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningRecord(Base):
    __tablename__ = "learning_records"

    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(30))  # correlation|experiment_result|knowledge|operational
    finding: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    related_publication_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    platform: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    limitations: Mapped[str] = mapped_column(Text, default="")
    causal_claim: Mapped[bool] = mapped_column(Boolean, default=False)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    next_experiment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|superseded|rejected
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------------------
# Durable jobs
# ---------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "queue", "status", "run_after", "priority"),
        Index(
            "uq_jobs_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND status IN ('queued','running','waiting','paused')"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    type: Mapped[str] = mapped_column(String(60), index=True)
    queue: Mapped[str] = mapped_column(String(30), default="default")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    status_label: Mapped[str] = mapped_column(String(80), default="Queued")
    stage: Mapped[str | None] = mapped_column(String(60))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resume_value: Mapped[Any | None] = mapped_column(JSON)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    wait_group: Mapped[str | None] = mapped_column(String(80))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = _created()
    locked_by: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated()


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job", "job_id", "created_at"),)

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(10), default="info")
    stage: Mapped[str | None] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()


class Schedule(Base):
    __tablename__ = "schedules"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(60))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = _created()
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    queues: Mapped[str] = mapped_column(String(120))
    hostname: Mapped[str] = mapped_column(String(200))
    pid: Mapped[int] = mapped_column(Integer)
    has_secrets: Mapped[bool] = mapped_column(Boolean, default=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = _created()
    seen_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------------------
# Live sessions
# ---------------------------------------------------------------------------


class LiveSession(Base):
    __tablename__ = "live_sessions"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    ingest_protocol: Mapped[str] = mapped_column(String(10), default="srt")
    stream_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"))
    port: Mapped[int | None] = mapped_column(Integer)
    stream_key_hint: Mapped[str] = mapped_column(String(12), default="")
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting|live|reconnecting|ended|failed|stopped
    retention_minutes: Mapped[int] = mapped_column(Integer, default=120)
    next_segment_idx: Mapped[int] = mapped_column(Integer, default=0)
    processed_until_idx: Mapped[int] = mapped_column(Integer, default=-1)
    connections: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class LiveSegment(Base):
    __tablename__ = "live_segments"
    __table_args__ = (UniqueConstraint("session_id", "idx"),)

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(Text)
    stream_offset_s: Mapped[float] = mapped_column(Float)
    duration_s: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="recorded")  # recorded|transcribed|deleted
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    energy: Mapped[float | None] = mapped_column(Float)
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class LibraryItem(Base):
    """Owner-provided assets: licensed music, SFX, B-roll, logos, master files."""

    __tablename__ = "library_items"

    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(20))  # music|sfx|broll|logo|master
    title: Mapped[str] = mapped_column(String(300))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    license_note: Mapped[str] = mapped_column(Text, default="")
    rights_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()
