"""SQLAlchemy models. Types are portable (JSON, not JSONB) so tests can run on SQLite."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite returns naive values)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Optional[datetime], dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---- video status constants -------------------------------------------------
PLANNED = "planned"
SCRIPTED = "scripted"
SEO_READY = "seo_ready"
RENDERED = "rendered"
READY = "ready"
PUBLISHING = "publishing"
PUBLISHED = "published"
FAILED = "failed"
CANCELLED = "cancelled"

PIPELINE_ORDER = [PLANNED, SCRIPTED, SEO_READY, RENDERED, READY, PUBLISHING, PUBLISHED]
ACTIVE_STATUSES = [PLANNED, SCRIPTED, SEO_READY, RENDERED, READY, PUBLISHING]


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class YouTubeAccount(Base):
    __tablename__ = "youtube_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), unique=True)
    channel_title: Mapped[str] = mapped_column(String(255), default="")
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subscriber_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    video_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploads_playlist_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text, default="")
    # connected | needs_reauth | disconnected
    status: Mapped[str] = mapped_column(String(20), default="connected")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    code_verifier: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Idea(Base):
    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(300))
    angle: Mapped[str] = mapped_column(Text, default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(80), default="")
    hook_style: Mapped[str] = mapped_column(String(60), default="")
    source: Mapped[str] = mapped_column(String(40), default="director")  # director | trend | manual
    trend_signal: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    # new | approved | used | rejected
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    video: Mapped[Optional["Video"]] = relationship(back_populates="idea", uselist=False)


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ideas.id"), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default=PLANNED, index=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)

    script: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    seo: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    assets: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # licenses / credits / providers
    video_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    subtitle_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    thumbnail_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)

    # stage lock (compare-and-set) — survives restarts, expires via locked_at
    processing_stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failed_stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # owner approval for publishing (used when require_manual_approval = true or "publish now")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    idea: Mapped[Optional[Idea]] = relationship(back_populates="video")
    publication: Mapped[Optional["Publication"]] = relationship(back_populates="video", uselist=False)


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), unique=True)
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    # uploading | scheduled | published | failed
    status: Mapped[str] = mapped_column(String(20), default="uploading", index=True)
    privacy: Mapped[str] = mapped_column(String(20), default="public")
    publish_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    attempt_started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    upload_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_set: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    video: Mapped[Video] = relationship(back_populates="publication")
    snapshots: Mapped[list["AnalyticsSnapshot"]] = relationship(
        back_populates="publication", cascade="all, delete-orphan"
    )


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    impressions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ctr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_view_duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_view_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    subs_gained: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    publication: Mapped[Publication] = relationship(back_populates="snapshots")


class TopicPerformance(Base):
    """Aggregated performance per keyword / category / hook style — the learning memory."""

    __tablename__ = "topic_performance"
    __table_args__ = (Index("ix_topic_kind_key", "kind", "key", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # keyword | category | hook_style
    key: Mapped[str] = mapped_column(String(120))
    videos_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_views: Mapped[float] = mapped_column(Float, default=0.0)
    avg_retention: Mapped[float] = mapped_column(Float, default=0.0)
    avg_engagement: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)  # relative, 0..100
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Insight(Base):
    """AI-written analytics summary used as feedback for the Director."""

    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO", index=True)
    component: Mapped[str] = mapped_column(String(40), default="system", index=True)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    video_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)


class JobRun(Base):
    """Heartbeat/audit of scheduled jobs (last run, duration, outcome)."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="running")  # running | ok | error
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
