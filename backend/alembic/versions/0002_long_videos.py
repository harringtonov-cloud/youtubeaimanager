"""long-form videos: videos.format and videos.parent_video_id

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("format", sa.String(10), nullable=False, server_default="short"))
    op.add_column("videos", sa.Column("parent_video_id", sa.Integer(), nullable=True))
    op.create_index("ix_videos_format", "videos", ["format"])


def downgrade() -> None:
    op.drop_index("ix_videos_format", table_name="videos")
    op.drop_column("videos", "parent_video_id")
    op.drop_column("videos", "format")
