"""notes and block-anchored annotations

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=True),
        sa.Column("version_id", sa.UUID(), nullable=True),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("anchor", postgresql.JSONB(astext_type=sa.Text(), none_as_null=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(body) <= 20000", name="ck_note_body_length"),
        sa.CheckConstraint(
            "anchor IS NULL OR version_id IS NOT NULL", name="ck_note_anchor_version"
        ),
        sa.CheckConstraint(
            "version_id IS NULL OR paper_id IS NOT NULL", name="ck_note_version_paper"
        ),
        sa.CheckConstraint(
            "anchor IS NOT NULL OR char_length(body) >= 1", name="ck_note_body_present"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["paper_versions.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notes_workspace_id", "notes", ["workspace_id"])
    op.create_index("ix_notes_project_id", "notes", ["project_id"])
    op.create_index(
        "ix_notes_project_paper_time", "notes", ["project_id", "paper_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("notes")
