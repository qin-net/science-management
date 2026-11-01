"""literature search: paper abstract/venue/links, arXiv uniqueness, source state and cache

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("abstract", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("venue", sa.Text(), nullable=True))
    op.add_column(
        "papers", sa.Column("source_links", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    # P0.2 never enforced this. If it fails, two papers in one project share an arXiv ID:
    # resolve them by hand, the migration will not pick one to delete.
    op.create_index(
        "uq_papers_project_arxiv",
        "papers",
        ["project_id", "arxiv_id"],
        unique=True,
        postgresql_where=sa.text("arxiv_id IS NOT NULL"),
    )
    op.create_table(
        "source_state",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_reason", sa.String(length=32), nullable=True),
        sa.Column("quota_remaining_usd", sa.Float(), nullable=True),
        sa.Column("quota_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("interval_seconds", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source"),
    )
    op.create_table(
        "search_cache",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_search_cache_expires_at"), "search_cache", ["expires_at"])
    op.create_table(
        "search_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_calls_user_created", "search_calls", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("search_calls")
    op.drop_table("search_cache")
    op.drop_table("source_state")
    op.drop_index("uq_papers_project_arxiv", table_name="papers")
    op.drop_column("papers", "source_links")
    op.drop_column("papers", "venue")
    op.drop_column("papers", "abstract")
