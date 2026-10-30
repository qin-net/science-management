"""Alembic environment. The URL comes from settings, so it never lives in alembic.ini."""

from alembic import context

from researchos.db import get_engine
from researchos.models import Base


def run_migrations() -> None:
    with get_engine().connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
