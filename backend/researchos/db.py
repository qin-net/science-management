"""Engine and session management."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from researchos.config import get_settings
from researchos.errors import ApiError


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if not url:
        raise ApiError(503, "database_not_configured", "DATABASE_URL is not set; see .env.example")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with session_factory()() as session:
        yield session
