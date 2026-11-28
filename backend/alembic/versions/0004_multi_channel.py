"""several YouTube channels in one installation: account_id on every channel-owned table

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ["ideas", "videos", "publications", "analytics_snapshots", "topic_performance", "insights", "logs", "job_runs"]


def upgrade() -> None:
    for t in TABLES:
        op.add_column(t, sa.Column("account_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{t}_account_id", t, ["account_id"])
        # everything that exists now belongs to the channel that is connected now (the original one)
        op.execute(sa.text(f"UPDATE {t} SET account_id = (SELECT MIN(id) FROM youtube_accounts) WHERE account_id IS NULL"))
    # the learning memory is kept per channel
    op.drop_index("ix_topic_kind_key", table_name="topic_performance")
    op.create_index("ix_topic_acct_kind_key", "topic_performance", ["account_id", "kind", "key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_topic_acct_kind_key", table_name="topic_performance")
    op.create_index("ix_topic_kind_key", "topic_performance", ["kind", "key"], unique=True)
    for t in TABLES:
        op.drop_index(f"ix_{t}_account_id", table_name=t)
        op.drop_column(t, "account_id")
