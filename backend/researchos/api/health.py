"""Liveness and readiness. Readiness runs real checks on every call and never returns
paths, URLs or driver messages."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from researchos import storage
from researchos.config import get_settings
from researchos.db import session_factory
from researchos.errors import ApiError
from researchos.models import WorkerHeartbeat
from researchos.parsers.registry import docling_status

router = APIRouter(prefix="/health", tags=["health"])

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def migration_head() -> str | None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config).get_current_head()


def run_checks() -> dict:
    checks = {
        "database": {"status": "failed", "detail": "not checked"},
        "migrations": {"status": "failed", "detail": "database unavailable"},
        "storage": {"status": "ok" if storage.storage_writable() else "failed"},
        "worker": {"status": "failed", "detail": "database unavailable"},
    }
    try:
        with session_factory()() as db:
            db.execute(text("select 1"))
            checks["database"] = {"status": "ok"}
            head = migration_head()
            try:
                current = db.scalar(text("select version_num from alembic_version"))
            except SQLAlchemyError:
                db.rollback()
                current = None
            if current != head:
                checks["migrations"] = {
                    "status": "failed",
                    "detail": "schema is behind; run scripts/migrate.sh",
                }
                checks["worker"] = {"status": "failed", "detail": "schema is behind"}
                return checks
            checks["migrations"] = {"status": "ok"}
            stale_before = datetime.now(UTC) - timedelta(
                seconds=get_settings().worker_stale_seconds
            )
            online = db.scalar(
                select(func.count())
                .select_from(WorkerHeartbeat)
                .where(WorkerHeartbeat.last_seen_at >= stale_before)
            )
            checks["worker"] = (
                {"status": "ok", "detail": f"{online} online"}
                if online
                else {"status": "failed", "detail": "no worker heartbeat"}
            )
    except ApiError as exc:
        checks["database"] = {"status": "failed", "detail": exc.message}
    except SQLAlchemyError as exc:
        kind = type(exc).__name__
        checks["database"] = {"status": "failed", "detail": f"database unreachable ({kind})"}
    return checks


@router.get("/live")
def live() -> dict:
    return {"status": "live"}


@router.get("/ready")
def ready() -> JSONResponse:
    checks = run_checks()
    is_ready = all(check["status"] == "ok" for check in checks.values())
    return JSONResponse(
        {
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
            "parsers": {"pdfium": "available", "docling": docling_status()},
        },
        status_code=200 if is_ready else 503,
    )
