"""Acceptance E2-E6 in specs/000: diagnostics, health, errors and log hygiene."""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from researchos.logs import JsonFormatter
from researchos.main import app, create_app
from researchos.worker import Worker
from tests.conftest import BACKEND_DIR, EMPTY_DB, SERVER_URL, _database_url

PASSWORD_IN_URL = "s3cret-value"


def doctor(database_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "researchos.doctor"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_e2_doctor_reports_missing_database_url():
    result = doctor("")
    assert result.returncode == 1
    assert "[failed] database (DATABASE_URL is not set" in result.stdout
    assert "fix:" in result.stdout


def test_e2_doctor_rejects_a_malformed_url_without_printing_it():
    result = doctor(f"mysql://root:{PASSWORD_IN_URL}@db/x")
    assert result.returncode == 1
    assert "DATABASE_URL" in result.stdout
    assert PASSWORD_IN_URL not in result.stdout + result.stderr


def test_e2_doctor_reports_an_unreachable_database_without_the_password():
    result = doctor(f"postgresql+psycopg://root:{PASSWORD_IN_URL}@127.0.0.1:1/x")
    assert result.returncode == 1
    assert "[failed] database (database unreachable" in result.stdout
    assert PASSWORD_IN_URL not in result.stdout + result.stderr


def test_e3_doctor_flags_a_schema_that_has_not_been_migrated():
    result = doctor(_database_url(SERVER_URL, EMPTY_DB))
    assert result.returncode == 1
    assert "[ok] database" in result.stdout
    assert "[failed] migrations (schema is behind; run scripts/migrate.sh)" in result.stdout


def test_models_and_migrations_do_not_drift():
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_e4_ready_fails_without_a_worker_and_passes_with_one():
    client = TestClient(app)
    assert client.get("/health/live").json() == {"status": "live"}

    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["worker"]["status"] == "failed"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["migrations"]["status"] == "ok"

    Worker().beat()
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["parsers"] == {"pdfium": "available", "docling": "not_configured"}


READY_SCRIPT = """
import json
from fastapi.testclient import TestClient
from researchos.main import app

client = TestClient(app)
response = client.get("/health/ready")
print(json.dumps({"status": response.status_code, "body": response.json(), "text": response.text}))
"""


def test_e3_ready_returns_503_and_names_the_fix_when_not_migrated():
    result = subprocess.run(
        [sys.executable, "-c", READY_SCRIPT],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": _database_url(SERVER_URL, EMPTY_DB)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])

    assert seen["status"] == 503 and seen["body"]["status"] == "not_ready"
    checks = seen["body"]["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["migrations"] == {
        "status": "failed",
        "detail": "schema is behind; run scripts/migrate.sh",
    }
    assert checks["worker"]["status"] == "failed"
    assert EMPTY_DB not in seen["text"] and "postgresql" not in seen["text"]


STOP_DATABASE_SCRIPT = """
import json, subprocess, sys
from fastapi.testclient import TestClient
from researchos.main import app
from researchos.worker import Worker

pg_ctl, pgdata = sys.argv[1], sys.argv[2]
client = TestClient(app)
Worker().beat()
before = client.get("/health/ready")
subprocess.run([pg_ctl, "-D", pgdata, "-m", "fast", "stop"], check=True, capture_output=True)
after = client.get("/health/ready")
print(json.dumps({"before": [before.status_code, before.json()],
                  "after": [after.status_code, after.json(), after.text]}))
"""


def test_e4_ready_reports_a_database_that_stops_after_startup():
    """A dedicated server is migrated, seen healthy, then really stopped by pg_ctl. The shared
    test server cannot be used because every other test depends on it."""
    import pgserver
    from pgserver import postgres_server

    pgdata = Path(tempfile.mkdtemp(prefix="researchos-stop-"))
    try:
        server = pgserver.get_server(pgdata, cleanup_mode=None)
        url = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
        env = {**os.environ, "DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR, env=env, check=True, capture_output=True, timeout=60,
        )
        pg_ctl = str(Path(postgres_server.POSTGRES_BIN_PATH) / "pg_ctl")
        result = subprocess.run(
            [sys.executable, "-c", STOP_DATABASE_SCRIPT, pg_ctl, str(pgdata)],
            cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        seen = json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        subprocess.run(
            [str(Path(postgres_server.POSTGRES_BIN_PATH) / "pg_ctl"), "-D", str(pgdata),
             "-m", "immediate", "stop"],
            capture_output=True,
        )
        shutil.rmtree(pgdata, ignore_errors=True)

    status, body = seen["before"]
    assert status == 200 and body["status"] == "ready"

    status, body, text = seen["after"]
    assert status == 503 and body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "failed"
    assert "database unreachable" in body["checks"]["database"]["detail"]
    assert body["checks"]["migrations"]["status"] == "failed"
    assert body["checks"]["worker"]["status"] == "failed"
    assert str(pgdata) not in text and "postgresql" not in text


def test_ready_response_holds_no_paths_or_urls():
    body = TestClient(app).get("/health/ready").text
    assert "postgresql" not in body and "/data/" not in body and "researchos-test" not in body


@pytest.fixture
def crashing_client():
    broken = create_app()

    @broken.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    return TestClient(broken, raise_server_exceptions=False)


def test_e6_unhandled_error_is_generic_and_correlated(crashing_client, caplog):
    with caplog.at_level(logging.ERROR, logger="researchos.api"):
        response = crashing_client.get("/boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "secret internal detail" not in response.text and "Traceback" not in response.text
    assert response.headers["x-request-id"] == error["request_id"]
    assert error["request_id"] in {getattr(r, "request_id", None) for r in caplog.records}


def test_every_response_has_a_request_id_and_clients_cannot_set_it():
    response = TestClient(app).get("/health/live", headers={"X-Request-ID": "forged"})
    assert response.headers["x-request-id"] != "forged"


def test_validation_errors_do_not_echo_the_password():
    response = TestClient(app).post(
        "/api/v1/auth/register",
        json={"email": "bad", "password": "hunter2-hunter2", "display_name": "x"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "hunter2" not in response.text


def test_log_lines_only_carry_allow_listed_fields():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "request", None, None)
    record.request_id = "abc"
    record.cookie = "ros_session=secret"
    record.password = "hunter2"
    entry = json.loads(JsonFormatter().format(record))
    assert entry["request_id"] == "abc"
    assert "cookie" not in entry and "password" not in entry
