"""long videos made straight from an idea: videos.source_idea_id

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("source_idea_id", sa.Integer(), nullable=True))
    op.create_index("ix_videos_source_idea_id", "videos", ["source_idea_id"])


def downgrade() -> None:
    op.drop_index("ix_videos_source_idea_id", table_name="videos")
    op.drop_column("videos", "source_idea_id")
