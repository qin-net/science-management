"""block translations, and translate calls in the model call ledger

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "block_translations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("paper_id", sa.UUID(), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("language in ('zh','en')", name="ck_translation_language"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "paper_id", "content_hash", "language", name="uq_translation_paper_hash_language"
        ),
    )
    op.create_index("ix_block_translations_workspace_id", "block_translations", ["workspace_id"])
    op.create_index("ix_block_translations_project_id", "block_translations", ["project_id"])
    op.drop_constraint("ck_model_call_kind", "model_calls", type_="check")
    op.create_check_constraint(
        "ck_model_call_kind", "model_calls", "kind in ('ask','summary','translate')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM model_calls WHERE kind = 'translate'")
    op.drop_constraint("ck_model_call_kind", "model_calls", type_="check")
    op.create_check_constraint("ck_model_call_kind", "model_calls", "kind in ('ask','summary')")
    op.drop_table("block_translations")
