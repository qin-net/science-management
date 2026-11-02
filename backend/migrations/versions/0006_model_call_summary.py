"""allow summary calls in the model call ledger

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_model_call_kind", "model_calls", type_="check")
    op.create_check_constraint("ck_model_call_kind", "model_calls", "kind in ('ask','summary')")


def downgrade() -> None:
    op.execute("DELETE FROM model_calls WHERE kind <> 'ask'")
    op.drop_constraint("ck_model_call_kind", "model_calls", type_="check")
    op.create_check_constraint("ck_model_call_kind", "model_calls", "kind in ('ask')")
