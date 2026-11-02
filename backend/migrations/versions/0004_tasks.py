"""project tasks

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("paper_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('todo','doing','blocked','done','cancelled')", name="ck_task_status"
        ),
        sa.CheckConstraint("priority in ('low','normal','high')", name="ck_task_priority"),
        sa.CheckConstraint("char_length(title) between 1 and 200", name="ck_task_title_length"),
        sa.CheckConstraint("char_length(description) <= 5000", name="ck_task_description_length"),
        sa.CheckConstraint(
            "(status = 'blocked') = (blocked_reason IS NOT NULL AND char_length(blocked_reason) "
            "between 1 and 500)",
            name="ck_task_blocked_reason",
        ),
        sa.CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)", name="ck_task_completed"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_workspace_id", "tasks", ["workspace_id"])
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_project_status", "tasks", ["project_id", "status"])
    op.create_index("ix_tasks_project_assignee", "tasks", ["project_id", "assignee_id"])


def downgrade() -> None:
    op.drop_table("tasks")
