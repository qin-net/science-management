"""Tests run against a real PostgreSQL and real PDFs, never SQLite or stubbed parsers.

The server is the embedded one in data/pg-test, or TEST_DATABASE_URL if set. Either way only
the dedicated researchos_test database is touched, so development data is safe.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
TEST_DB = "researchos_test"
EMPTY_DB = "researchos_test_empty"
EMBEDDED_PGDATA = REPO_ROOT / "data" / "pg-test"
MAX_UPLOAD_MB = 5


def _server_url() -> str:
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        return configured
    import pgserver

    EMBEDDED_PGDATA.mkdir(parents=True, exist_ok=True)
    return pgserver.get_server(EMBEDDED_PGDATA).get_uri()


def _database_url(server_url: str, name: str) -> str:
    url = make_url(server_url).set(drivername="postgresql+psycopg", database=name)
    return url.render_as_string(hide_password=False)


def _recreate(server_url: str, *names: str) -> None:
    plain = make_url(server_url).set(drivername="postgresql")
    with psycopg.connect(plain.render_as_string(hide_password=False), autocommit=True) as conn:
        for name in names:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{name}"')


SERVER_URL = _server_url()
_recreate(SERVER_URL, TEST_DB, EMPTY_DB)
STORAGE_ROOT = Path(tempfile.mkdtemp(prefix="researchos-test-"))

# Settings are read once, so these must be in place before researchos is imported.
os.environ.update(
    DATABASE_URL=_database_url(SERVER_URL, TEST_DB),
    STORAGE_DIR=str(STORAGE_ROOT),
    MAX_UPLOAD_MB=str(MAX_UPLOAD_MB),
    PARSE_RETRY_BASE_SECONDS="0",
    WORKER_POLL_SECONDS="0.1",
    ALLOW_REGISTRATION="true",
    PARSER_DEFAULT="pdfium",
    DOCLING_PYTHON="",
    LOG_LEVEL="INFO",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from researchos.db import get_engine  # noqa: E402
from researchos.main import app  # noqa: E402
from researchos.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, check=True
    )
    yield
    get_engine().dispose()
    shutil.rmtree(STORAGE_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_state():
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    shutil.rmtree(STORAGE_ROOT, ignore_errors=True)


class Account:
    """A signed-in browser: a client with its own cookie jar."""

    def __init__(self, client: TestClient, user: dict, workspace_id: str) -> None:
        self.client = client
        self.user = user
        self.workspace_id = workspace_id

    def new_project(self, name: str = "Project") -> dict:
        response = self.client.post(
            f"/api/v1/workspaces/{self.workspace_id}/projects", json={"name": name}
        )
        assert response.status_code == 201, response.text
        return response.json()

    def upload(self, project_id: str, data: bytes, filename: str = "paper.pdf"):
        return self.client.post(
            f"/api/v1/projects/{project_id}/papers",
            params={"filename": filename},
            content=data,
            headers={"Content-Type": "application/pdf"},
        )


def sign_up() -> Account:
    client = TestClient(app, raise_server_exceptions=False)
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct horse battery", "display_name": "Tester"},
    )
    assert response.status_code == 201, response.text
    workspaces = client.get("/api/v1/workspaces").json()
    return Account(client, response.json(), workspaces[0]["id"])


@pytest.fixture
def owner() -> Account:
    return sign_up()


@pytest.fixture
def outsider() -> Account:
    return sign_up()


def add_member(owner: Account, role: str) -> Account:
    member = sign_up()
    response = owner.client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/members",
        json={"email": member.user["email"], "role": role},
    )
    assert response.status_code == 201, response.text
    member.workspace_id = owner.workspace_id
    return member
