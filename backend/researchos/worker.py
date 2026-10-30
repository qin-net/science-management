"""Durable parse worker.

Jobs live in PostgreSQL. A worker claims one with SKIP LOCKED and owns it through a lease;
every write it makes afterwards is fenced on (lease_owner, attempts), so a worker that lost
its lease cannot publish late results. attempts is charged at claim time, which keeps a
crashing file from looping forever even if the worker dies with it.

Run with `python -m researchos.worker`.
"""

import hashlib
import json
import logging
import os
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, delete, insert, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from researchos import events, storage
from researchos.config import get_settings
from researchos.db import session_factory
from researchos.errors import ApiError
from researchos.jobs import PARSE_KIND
from researchos.logs import configure_logging
from researchos.models import Job, Paper, PaperBlock, PaperVersion, WorkerHeartbeat
from researchos.parsers.base import ParseError, ParseResult
from researchos.parsers.registry import parser_commands, parser_env

log = logging.getLogger("researchos.worker")

TICK_SECONDS = 0.1
RENEW_SECONDS = 1.0
MAX_ERROR_MESSAGE = 500


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    attempt: int
    parser: str
    storage_key: str


class LeaseLost(Exception):
    """Another worker owns the job now; stop without touching it."""


class CancelRequested(Exception):
    pass


class Shutdown(Exception):
    pass


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def scrub(value: str) -> str:
    """Parser strings come from untrusted files. PostgreSQL rejects NUL and UTF-8 rejects
    lone surrogates, and neither should be able to fail an otherwise good parse."""
    return value.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def validate_result(result: ParseResult) -> None:
    """An empty or malformed result must never count as a successful parse."""
    if not any(block.text.strip() for block in result.blocks):
        raise ParseError("parser_empty_output", "the parser finished but returned no text")
    for block in result.blocks:
        if not 1 <= block.page_no <= result.page_count:
            raise ParseError("parser_bad_output", f"block on invalid page {block.page_no}")
        if block.bbox is None:
            if block.granularity != "page":
                raise ParseError("parser_bad_output", "block without bbox must be page-level")
            continue
        if len(block.bbox) != 4:
            raise ParseError("parser_bad_output", f"bbox has wrong shape on page {block.page_no}")
        x0, y0, x1, y1 = block.bbox
        if not (0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1):
            raise ParseError("parser_bad_output", f"bbox out of range on page {block.page_no}")


class Worker:
    def __init__(self, commands: dict[str, list[str]] | None = None) -> None:
        self.settings = get_settings()
        self.commands = parser_commands() if commands is None else commands
        unique = uuid.uuid4().hex[:8]
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{unique}"[:128]
        self.sessions = session_factory()
        self.stopping = False
        self._next_beat = 0.0

    # --- heartbeat -------------------------------------------------------------------

    def beat(self) -> None:
        now = time.monotonic()
        if now < self._next_beat:
            return
        self._next_beat = now + self.settings.worker_stale_seconds / 3
        with self.sessions() as db:
            row = db.get(WorkerHeartbeat, self.worker_id)
            if row is None:
                db.add(WorkerHeartbeat(worker_id=self.worker_id, parsers=sorted(self.commands)))
            else:
                row.last_seen_at = utcnow()
            db.commit()

    def sign_off(self) -> None:
        with self.sessions() as db:
            db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == self.worker_id))
            db.commit()

    # --- claiming --------------------------------------------------------------------

    def claim(self) -> Claim | None:
        while True:
            with self.sessions() as db:
                now = utcnow()
                job = db.scalar(
                    select(Job)
                    .where(
                        Job.kind == PARSE_KIND,
                        or_(
                            and_(Job.status == "queued", Job.next_run_at <= now),
                            and_(Job.status == "running", Job.lease_expires_at < now),
                        ),
                    )
                    .order_by(Job.next_run_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if job is None:
                    return None
                recovered = job.status == "running"
                if recovered and job.cancel_requested:
                    self._finish_cancelled(db, job)
                    db.commit()
                    continue
                if recovered and job.attempts >= job.max_attempts:
                    self._finish_failed(
                        db, job, "worker_lost", "a worker stopped while parsing this file"
                    )
                    db.commit()
                    continue
                if job.parser not in self.commands:
                    self._finish_failed(
                        db,
                        job,
                        "parser_unavailable",
                        f"parser {job.parser!r} is not available on this worker",
                    )
                    db.commit()
                    continue
                version = db.get_one(PaperVersion, job.subject_id)
                job.status = "running"
                job.stage = "parsing"
                job.attempts += 1
                job.lease_owner = self.worker_id
                job.lease_expires_at = now + timedelta(seconds=self.settings.worker_lease_seconds)
                job.started_at = job.started_at or now
                job.error_code = None
                job.error_message = None
                claim = Claim(job.id, job.attempts, job.parser, version.storage_key)
                db.commit()
                log.info(
                    "job claimed", extra={"job_id": str(job.id), "worker_id": self.worker_id}
                )
                return claim

    def _owned(self, db: Session, claim: Claim) -> Job | None:
        return db.scalar(
            select(Job)
            .where(
                Job.id == claim.job_id,
                Job.status == "running",
                Job.lease_owner == self.worker_id,
                Job.attempts == claim.attempt,
            )
            .with_for_update()
        )

    def renew(self, claim: Claim) -> None:
        self.beat()
        with self.sessions() as db:
            job = self._owned(db, claim)
            if job is None:
                raise LeaseLost
            job.lease_expires_at = utcnow() + timedelta(
                seconds=self.settings.worker_lease_seconds
            )
            cancelled = job.cancel_requested
            db.commit()
        if cancelled:
            raise CancelRequested

    # --- running ---------------------------------------------------------------------

    def _run_parser(self, claim: Claim, pdf: Path, output: Path) -> int:
        """Run the parser in its own process group so that a kill reaches any helpers."""
        proc = subprocess.Popen(
            [*self.commands[claim.parser], str(pdf), str(output)],
            env=parser_env(),
            stdin=subprocess.DEVNULL,
            # Parser output can quote document text, which must stay out of the logs.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + self.settings.parse_timeout_seconds
        next_renew = time.monotonic() + RENEW_SECONDS
        try:
            while True:
                code = proc.poll()
                if code is not None:
                    return code
                now = time.monotonic()
                if self.stopping:
                    raise Shutdown
                if now >= deadline:
                    raise ParseError(
                        "parse_timeout",
                        f"parsing exceeded {self.settings.parse_timeout_seconds} seconds",
                        retryable=True,
                    )
                if now >= next_renew:
                    self.renew(claim)
                    next_renew = now + RENEW_SECONDS
                time.sleep(TICK_SECONDS)
        finally:
            if proc.poll() is None:
                _kill_group(proc)

    def _parse(self, claim: Claim) -> ParseResult:
        pdf = storage.open_pdf(claim.storage_key)
        with tempfile.TemporaryDirectory(dir=storage.temp_dir()) as scratch:
            output = Path(scratch) / "result.json"
            code = self._run_parser(claim, pdf, output)
            try:
                payload = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ParseError(
                    "parser_crashed",
                    f"parser exited with code {code} and no readable output",
                    retryable=True,
                ) from exc
        if "error" in payload:
            error = payload["error"]
            raise ParseError(error["code"], error["message"], error.get("retryable", False))
        try:
            result = ParseResult.from_dict(payload["result"])
        except (KeyError, TypeError) as exc:
            raise ParseError("parser_bad_output", "parser output has the wrong shape") from exc
        validate_result(result)
        return result

    def execute(self, claim: Claim) -> None:
        try:
            result = self._parse(claim)
        except ParseError as exc:
            self._record_failure(claim, exc.code, exc.message, exc.retryable)
        except ApiError as exc:
            self._record_failure(claim, exc.code, exc.message, False)
        except CancelRequested:
            self._record_cancelled(claim)
        except LeaseLost:
            log.warning(
                "lease lost", extra={"job_id": str(claim.job_id), "worker_id": self.worker_id}
            )
        except Shutdown:
            self._release(claim)
        except OperationalError:
            raise  # the main loop waits for the database; the lease recovers this job
        except Exception as exc:
            # Only the type: exception text can quote document content.
            log.error(
                "unexpected worker error: %s",
                type(exc).__name__,
                extra={"job_id": str(claim.job_id), "worker_id": self.worker_id},
            )
            self._record_failure(claim, "worker_error", "the worker hit an internal error", True)
        else:
            self._record_success(claim, result)

    def run_once(self) -> bool:
        self.beat()
        claim = self.claim()
        if claim is None:
            return False
        self.execute(claim)
        return True

    # --- outcomes --------------------------------------------------------------------

    def _event(self, db: Session, job: Job, event_type: str, payload: dict | None = None) -> None:
        events.record(
            db,
            event_type=event_type,
            object_type="paper_version",
            object_id=job.subject_id,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            actor_id=None,
            payload={"job_id": str(job.id), **(payload or {})},
        )

    def _close(self, job: Job, status: str) -> None:
        job.status = status
        job.stage = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.finished_at = utcnow()

    def _finish_cancelled(self, db: Session, job: Job) -> None:
        self._close(job, "cancelled")
        self._event(db, job, "paper.parse_cancelled")

    def _finish_failed(self, db: Session, job: Job, code: str, message: str) -> None:
        job.error_code = code
        job.error_message = message[:MAX_ERROR_MESSAGE]
        self._close(job, "failed")
        self._event(db, job, "paper.parse_failed", {"error_code": code})

    def _record_cancelled(self, claim: Claim) -> None:
        with self.sessions() as db:
            job = self._owned(db, claim)
            if job is not None:
                self._finish_cancelled(db, job)
                db.commit()

    def _record_failure(self, claim: Claim, code: str, message: str, retryable: bool) -> None:
        with self.sessions() as db:
            job = self._owned(db, claim)
            if job is None:
                return
            if job.cancel_requested:
                self._finish_cancelled(db, job)
            elif retryable and job.attempts < job.max_attempts:
                delay = self.settings.parse_retry_base_seconds * 2 ** (job.attempts - 1)
                job.status = "queued"
                job.stage = None
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_run_at = utcnow() + timedelta(seconds=delay)
                job.error_code = code
                job.error_message = message[:MAX_ERROR_MESSAGE]
            else:
                self._finish_failed(db, job, code, message)
            db.commit()
        log.warning(
            "parse failed",
            extra={"job_id": str(claim.job_id), "worker_id": self.worker_id, "error_code": code},
        )

    def _release(self, claim: Claim) -> None:
        """Hand the job back on a clean shutdown; this attempt was not the file's fault."""
        with self.sessions() as db:
            job = self._owned(db, claim)
            if job is not None:
                job.status = "queued"
                job.stage = None
                job.attempts -= 1
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_run_at = utcnow()
                db.commit()

    def _record_success(self, claim: Claim, result: ParseResult) -> None:
        with self.sessions() as db:
            job = self._owned(db, claim)
            if job is None:
                log.warning(
                    "late result discarded",
                    extra={"job_id": str(claim.job_id), "worker_id": self.worker_id},
                )
                return
            if job.cancel_requested:
                self._finish_cancelled(db, job)
                db.commit()
                return
            version = db.get_one(PaperVersion, job.subject_id, with_for_update=True)
            rows = []
            for seq, block in enumerate(result.blocks):
                text = scrub(block.text)
                digest = hashlib.sha256(text.encode()).hexdigest()
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "workspace_id": job.workspace_id,
                        "project_id": job.project_id,
                        "paper_version_id": version.id,
                        "parse_job_id": job.id,
                        "seq": seq,
                        "page_no": block.page_no,
                        "kind": block.kind,
                        "text": text,
                        "bbox": block.bbox,
                        "granularity": block.granularity,
                        "content_hash": f"sha256:{digest}",
                    }
                )
            db.execute(insert(PaperBlock), rows)
            version.page_count = result.page_count
            version.pdf_metadata = {scrub(k): scrub(v) for k, v in result.pdf_metadata.items()}
            version.current_parse_job_id = job.id
            self._apply_metadata(db, version, result)
            job.result = {
                "parser": result.parser,
                "parser_version": result.parser_version,
                "page_count": result.page_count,
                "block_count": len(rows),
                "warnings": [scrub(warning) for warning in result.warnings],
                "duration_seconds": result.duration_seconds,
            }
            self._close(job, "succeeded")
            self._event(db, job, "paper.parsed", {"block_count": len(rows)})
            db.commit()
        log.info(
            "job succeeded", extra={"job_id": str(claim.job_id), "worker_id": self.worker_id}
        )

    def _apply_metadata(self, db: Session, version: PaperVersion, result: ParseResult) -> None:
        """Fill gaps from the PDF, but never overwrite what a user entered."""
        paper = db.get_one(Paper, version.paper_id, with_for_update=True)
        if paper.current_version_id != version.id:
            return
        provenance = dict(paper.metadata_provenance)
        title = scrub(result.title) if result.title else None
        authors = [scrub(author) for author in result.authors]
        for field, value in (("title", title), ("authors", authors)):
            if value and provenance.get(field) != "user":
                setattr(paper, field, value)
                provenance[field] = "pdf_metadata"
        paper.metadata_provenance = provenance

    # --- main loop -------------------------------------------------------------------

    def run_forever(self) -> None:
        def stop(*_) -> None:
            self.stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        log.info("worker started", extra={"worker_id": self.worker_id})
        try:
            while not self.stopping:
                try:
                    worked = self.run_once()
                except OperationalError as exc:
                    self._next_beat = 0.0
                    log.error(
                        "database unavailable: %s",
                        type(exc.orig).__name__,
                        extra={"worker_id": self.worker_id},
                    )
                    worked = False
                if not worked:
                    time.sleep(self.settings.worker_poll_seconds)
        finally:
            try:
                self.sign_off()
            except OperationalError:
                log.warning("could not remove heartbeat", extra={"worker_id": self.worker_id})


def main() -> None:
    configure_logging(get_settings().log_level)
    Worker().run_forever()


if __name__ == "__main__":
    main()
