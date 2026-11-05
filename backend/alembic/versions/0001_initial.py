"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DT = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", DT, nullable=False),
    )

    op.create_table(
        "youtube_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.String(64), nullable=False, unique=True),
        sa.Column("channel_title", sa.String(255), nullable=False, server_default=""),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("subscriber_count", sa.Integer(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column("uploads_playlist_id", sa.String(64), nullable=True),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="connected"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("connected_at", DT, nullable=False),
        sa.Column("last_verified_at", DT, nullable=True),
    )

    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(128), primary_key=True),
        sa.Column("code_verifier", sa.String(256), nullable=True),
        sa.Column("created_at", DT, nullable=False),
    )

    op.create_table(
        "ideas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic", sa.String(300), nullable=False),
        sa.Column("angle", sa.Text(), nullable=False, server_default=""),
        sa.Column("hook", sa.Text(), nullable=False, server_default=""),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False, server_default=""),
        sa.Column("hook_style", sa.String(60), nullable=False, server_default=""),
        sa.Column("source", sa.String(40), nullable=False, server_default="director"),
        sa.Column("trend_signal", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("used_at", DT, nullable=True),
    )
    op.create_index("ix_ideas_status", "ideas", ["status"])
    op.create_index("ix_ideas_created_at", "ideas", ["created_at"])

    op.create_table(
        "videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idea_id", sa.Integer(), sa.ForeignKey("ideas.id"), nullable=True, unique=True),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("scheduled_at", DT, nullable=True),
        sa.Column("script", sa.JSON(), nullable=True),
        sa.Column("seo", sa.JSON(), nullable=True),
        sa.Column("assets", sa.JSON(), nullable=True),
        sa.Column("video_key", sa.String(300), nullable=True),
        sa.Column("subtitle_key", sa.String(300), nullable=True),
        sa.Column("thumbnail_key", sa.String(300), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("processing_stage", sa.String(20), nullable=True),
        sa.Column("locked_at", DT, nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("failed_stage", sa.String(20), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
    )
    op.create_index("ix_videos_status", "videos", ["status"])
    op.create_index("ix_videos_scheduled_at", "videos", ["scheduled_at"])

    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id"), nullable=False, unique=True),
        sa.Column("youtube_video_id", sa.String(32), nullable=True, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploading"),
        sa.Column("privacy", sa.String(20), nullable=False, server_default="public"),
        sa.Column("publish_at", DT, nullable=True),
        sa.Column("url", sa.String(300), nullable=True),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("attempt_started_at", DT, nullable=True),
        sa.Column("upload_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("thumbnail_set", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False),
    )
    op.create_index("ix_publications_status", "publications", ["status"])

    op.create_table(
        "analytics_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publication_id", sa.Integer(), sa.ForeignKey("publications.id"), nullable=False),
        sa.Column("fetched_at", DT, nullable=False),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shares", sa.Integer(), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("ctr", sa.Float(), nullable=True),
        sa.Column("avg_view_duration", sa.Float(), nullable=True),
        sa.Column("avg_view_percentage", sa.Float(), nullable=True),
        sa.Column("estimated_minutes", sa.Float(), nullable=True),
        sa.Column("subs_gained", sa.Integer(), nullable=True),
    )
    op.create_index("ix_analytics_snapshots_publication_id", "analytics_snapshots", ["publication_id"])
    op.create_index("ix_analytics_snapshots_fetched_at", "analytics_snapshots", ["fetched_at"])

    op.create_table(
        "topic_performance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("videos_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_views", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_retention", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_engagement", sa.Float(), nullable=False, server_default="0"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", DT, nullable=False),
    )
    op.create_index("ix_topic_kind_key", "topic_performance", ["kind", "key"], unique=True)

    op.create_table(
        "insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.create_index("ix_insights_created_at", "insights", ["created_at"])

    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", DT, nullable=False),
        sa.Column("level", sa.String(10), nullable=False, server_default="INFO"),
        sa.Column("component", sa.String(40), nullable=False, server_default="system"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("video_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_logs_ts", "logs", ["ts"])
    op.create_index("ix_logs_level", "logs", ["level"])
    op.create_index("ix_logs_component", "logs", ["component"])
    op.create_index("ix_logs_video_id", "logs", ["video_id"])

    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("started_at", DT, nullable=False),
        sa.Column("finished_at", DT, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="running"),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_job_runs_name", "job_runs", ["name"])
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])


def downgrade() -> None:
    for t in (
        "job_runs",
        "logs",
        "insights",
        "topic_performance",
        "analytics_snapshots",
        "publications",
        "videos",
        "ideas",
        "oauth_states",
        "youtube_accounts",
        "settings",
    ):
        op.drop_table(t)
