"""Acceptance C4-C9 in specs/001. Failures come from real processes that hang, die or lie;
the worker code under test is the production path with a different parser command."""

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from researchos import storage
from researchos.config import get_settings
from researchos.db import session_factory
from researchos.models import Job, PaperBlock, PaperVersion, WorkerHeartbeat
from researchos.parsers.registry import parser_env
from researchos.worker import Worker
from tests.conftest import BACKEND_DIR
from tests.pdf_samples import text_pdf

SCRIPTS = {
    "hang": """
        import os, sys, time
        open(sys.argv[1] + ".pid", "w").write(str(os.getpid()))
        time.sleep(600)
    """,
    "crash": """
        import os, signal
        os.kill(os.getpid(), signal.SIGSEGV)
    """,
    "empty": """
        import json, sys
        json.dump(
            {"result": {"parser": "x", "parser_version": "1", "page_count": 1, "blocks": []}},
            open(sys.argv[2], "w"),
        )
    """,
    "whitespace": """
        import json, sys
        block = {"page_no": 1, "kind": "x", "text": "  ", "bbox": None, "granularity": "page"}
        json.dump(
            {"result": {"parser": "x", "parser_version": "1", "page_count": 1, "blocks": [block]}},
            open(sys.argv[2], "w"),
        )
    """,
    "bad_bbox": """
        import json, sys
        block = {"page_no": 1, "kind": "x", "text": "t", "bbox": [0.9, 0, 0.1, 2],
                 "granularity": "block"}
        json.dump(
            {"result": {"parser": "x", "parser_version": "1", "page_count": 1, "blocks": [block]}},
            open(sys.argv[2], "w"),
        )
    """,
    "garbage": """
        import sys
        open(sys.argv[2], "w").write("{not json")
    """,
}


@pytest.fixture
def parser_script(tmp_path):
    def build(name: str) -> dict[str, list[str]]:
        path = tmp_path / f"{name}.py"
        path.write_text(textwrap.dedent(SCRIPTS[name]))
        return {"pdfium": [sys.executable, str(path)]}

    return build


def queued_paper(owner) -> tuple[dict, dict]:
    project = owner.new_project()
    response = owner.upload(project["id"], text_pdf("Job test"))
    assert response.status_code == 202, response.text
    return project, response.json()


def load_job(job_id: str) -> Job:
    with session_factory()() as db:
        return db.get(Job, uuid.UUID(job_id))


def count_blocks(job_id: str) -> int:
    with session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(PaperBlock)
            .where(PaperBlock.parse_job_id == uuid.UUID(job_id))
        )


def expire_lease(job_id: str) -> None:
    with session_factory()() as db:
        db.execute(
            update(Job)
            .where(Job.id == uuid.UUID(job_id))
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()


def wait_for(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def process_is_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_c4_killed_worker_is_replaced_without_duplicate_blocks(owner, parser_script, tmp_path):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    commands = parser_script("hang")
    launcher = tmp_path / "doomed_worker.py"
    launcher.write_text(
        "import sys\n"
        "from researchos.worker import Worker\n"
        f"Worker(commands={{'pdfium': {commands['pdfium']!r}}}).run_forever()\n"
    )
    doomed = subprocess.Popen(
        [sys.executable, str(launcher)],
        cwd=BACKEND_DIR,
        env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
        stdout=subprocess.DEVNULL,
    )
    try:
        wait_for(lambda: load_job(job_id).status == "running")
        os.kill(doomed.pid, signal.SIGKILL)
        doomed.wait()
        stuck = load_job(job_id)
        assert stuck.status == "running" and stuck.attempts == 1

        # Until the lease runs out nobody may take the job over.
        assert Worker().claim() is None

        expire_lease(job_id)
        assert Worker().run_once() is True
    finally:
        doomed.kill()
    recovered = load_job(job_id)
    assert recovered.status == "succeeded" and recovered.attempts == 2
    assert count_blocks(job_id) == 2


def test_c4_job_that_keeps_killing_workers_ends_as_worker_lost(owner):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    for _ in range(3):
        assert Worker().claim() is not None
        expire_lease(job_id)
    assert Worker().claim() is None
    failed = load_job(job_id)
    assert failed.status == "failed" and failed.error_code == "worker_lost"
    assert failed.attempts == 3


def test_c5_late_result_from_a_replaced_worker_is_discarded(owner):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    slow, fast = Worker(), Worker()

    stale_claim = slow.claim()
    result = slow._parse(stale_claim)
    expire_lease(job_id)
    fresh_claim = fast.claim()
    assert fresh_claim.attempt == stale_claim.attempt + 1

    slow._record_success(stale_claim, result)
    assert count_blocks(job_id) == 0
    job = load_job(job_id)
    assert job.status == "running" and job.lease_owner == fast.worker_id

    fast.execute(fresh_claim)
    assert load_job(job_id).status == "succeeded"
    assert count_blocks(job_id) == 2


def test_c5_same_worker_cannot_finish_an_older_attempt(owner):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    worker = Worker()
    first = worker.claim()
    result = worker._parse(first)
    expire_lease(job_id)
    worker.claim()
    worker._record_success(first, result)
    assert count_blocks(job_id) == 0


def test_c5_reused_attempt_number_does_not_let_the_old_worker_in(owner):
    # A clean shutdown gives the attempt back, so the number alone cannot fence this.
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    departed, successor = Worker(), Worker()

    released = departed.claim()
    result = departed._parse(released)
    departed._release(released)
    current = successor.claim()
    assert current.attempt == released.attempt

    departed._record_success(released, result)
    assert count_blocks(job_id) == 0
    job = load_job(job_id)
    assert job.status == "running" and job.lease_owner == successor.worker_id


def test_c6_timeouts_retry_up_to_the_limit_and_kill_the_process(owner, parser_script):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    worker = Worker(commands=parser_script("hang"))
    worker.settings = worker.settings.model_copy(update={"parse_timeout_seconds": 1})

    assert worker.run_once() is True
    retrying = load_job(job_id)
    assert retrying.status == "queued" and retrying.error_code == "parse_timeout"

    worker.run_once()
    worker.run_once()
    failed = load_job(job_id)
    assert failed.status == "failed"
    assert failed.error_code == "parse_timeout"
    assert failed.attempts == failed.max_attempts == 3
    assert worker.run_once() is False, "a job at its limit must not run again"


def test_c6_timeout_kills_child_processes_too(owner, parser_script, tmp_path):
    _, paper = queued_paper(owner)
    hang = parser_script("hang")
    wrapper = tmp_path / "wrapper.py"
    pid_file = tmp_path / "child"
    wrapper.write_text(
        "import subprocess, sys\n"
        f"subprocess.run([sys.executable, {hang['pdfium'][1]!r}, {str(pid_file)!r}])\n"
    )
    worker = Worker(commands={"pdfium": [sys.executable, str(wrapper)]})
    worker.settings = worker.settings.model_copy(
        update={"parse_timeout_seconds": 2, "parse_max_attempts": 1}
    )
    worker.run_once()
    pid = int((tmp_path / "child.pid").read_text())
    assert process_is_gone(pid)
    assert load_job(paper["parse_job"]["id"]).error_code == "parse_timeout"


def test_c6_crashing_parser_retries_up_to_the_limit(owner, parser_script):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    worker = Worker(commands=parser_script("crash"))
    for _ in range(3):
        worker.run_once()
    failed = load_job(job_id)
    assert failed.status == "failed" and failed.error_code == "parser_crashed"
    assert failed.attempts == 3


@pytest.mark.parametrize(
    ("script", "code"),
    [
        ("empty", "parser_empty_output"),
        ("whitespace", "parser_empty_output"),
        ("bad_bbox", "parser_bad_output"),
    ],
)
def test_empty_or_malformed_output_is_never_success(owner, parser_script, script, code):
    project, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    Worker(commands=parser_script(script)).run_once()
    failed = load_job(job_id)
    assert failed.status == "failed" and failed.error_code == code
    assert count_blocks(job_id) == 0


def test_unparseable_output_counts_as_a_crash(owner, parser_script):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    worker = Worker(commands=parser_script("garbage"))
    worker.run_once()
    job = load_job(job_id)
    assert job.status == "queued" and job.error_code == "parser_crashed"


def test_c7_cancel_kills_the_running_parser(owner, parser_script):
    project, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    worker = Worker(commands=parser_script("hang"))
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    try:
        wait_for(lambda: bool(_pid_files()))
        pid = int(_pid_files()[0].read_text())

        response = owner.client.post(f"/api/v1/projects/{project['id']}/jobs/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["cancel_requested"] is True
        assert response.json()["status"] == "running"
    finally:
        thread.join(timeout=20)
    assert not thread.is_alive()
    job = load_job(job_id)
    assert job.status == "cancelled"
    assert count_blocks(job_id) == 0
    assert process_is_gone(pid)
    events = owner.client.get(f"/api/v1/projects/{project['id']}/events").json()
    assert "paper.parse_cancelled" in {event["event_type"] for event in events}


def _pid_files():
    # The hang script writes its pid next to the PDF it was given.
    return list(get_settings().storage_dir.rglob("*.pid"))


def test_c7_cancelling_a_queued_job_is_immediate_and_final(owner):
    project, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    path = f"/api/v1/projects/{project['id']}/jobs/{job_id}/cancel"
    response = owner.client.post(path)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert Worker().run_once() is False
    again = owner.client.post(path)
    assert again.status_code == 409 and again.json()["error"]["code"] == "job_finished"


def test_c8_second_parse_request_is_rejected_while_one_is_active(owner):
    project, paper = queued_paper(owner)
    version = paper["current_version"]["id"]
    url = f"/api/v1/projects/{project['id']}/papers/{paper['id']}/versions/{version}/parse"
    response = owner.client.post(url)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_active"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_c9_failed_reparse_keeps_the_old_blocks_and_success_switches(owner, parser_script):
    project, paper = queued_paper(owner)
    base = f"/api/v1/projects/{project['id']}/papers/{paper['id']}"
    version = paper["current_version"]["id"]
    assert Worker().run_once() is True
    first_job = paper["parse_job"]["id"]
    blocks_before = owner.client.get(f"{base}/versions/{version}/blocks").json()
    assert blocks_before["total"] == 2

    second = owner.client.post(f"{base}/versions/{version}/parse")
    assert second.status_code == 202
    Worker(commands=parser_script("empty")).run_once()
    assert load_job(second.json()["id"]).status == "failed"
    assert owner.client.get(f"{base}/versions/{version}/blocks").json() == blocks_before

    third = owner.client.post(f"{base}/versions/{version}/parse")
    assert third.status_code == 202
    assert Worker().run_once() is True
    with session_factory()() as db:
        row = db.get(PaperVersion, uuid.UUID(version))
        assert str(row.current_parse_job_id) == third.json()["id"]
        assert str(row.current_parse_job_id) != first_job
    after = owner.client.get(f"{base}/versions/{version}/blocks").json()
    assert after["total"] == 2
    assert {b["id"] for b in after["items"]}.isdisjoint(b["id"] for b in blocks_before["items"])
    assert [b["text"] for b in after["items"]] == [b["text"] for b in blocks_before["items"]]


def test_missing_stored_file_fails_without_retry(owner):
    project, paper = queued_paper(owner)

    with session_factory()() as db:
        row = db.get(PaperVersion, uuid.UUID(paper["current_version"]["id"]))
        storage.delete(row.storage_key)
    Worker().run_once()
    job = load_job(paper["parse_job"]["id"])
    assert job.status == "failed" and job.error_code == "file_missing"
    assert job.attempts == 1


def test_unavailable_parser_is_refused_at_request_time(owner):
    project, paper = queued_paper(owner)
    Worker().run_once()
    version = paper["current_version"]["id"]
    response = owner.client.post(
        f"/api/v1/projects/{project['id']}/papers/{paper['id']}/versions/{version}/parse",
        params={"parser": "docling"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "parser_unavailable"


def test_shutdown_returns_the_job_without_charging_an_attempt(owner, parser_script):
    _, paper = queued_paper(owner)
    job_id = paper["parse_job"]["id"]
    worker = Worker(commands=parser_script("hang"))
    claim = worker.claim()
    worker.stopping = True
    worker.execute(claim)
    job = load_job(job_id)
    assert job.status == "queued" and job.attempts == 0 and job.lease_owner is None


def test_parser_gets_no_database_credentials():
    env = parser_env()
    assert "DATABASE_URL" not in env
    assert set(env) <= {"PATH", "LANG", "HOME", "HF_HOME", "PYTHONPATH"}


def test_two_workers_never_claim_the_same_job(owner):
    queued_paper(owner)
    results = []
    workers = [Worker() for _ in range(4)]
    threads = [threading.Thread(target=lambda w=w: results.append(w.claim())) for w in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([claim for claim in results if claim is not None]) == 1


def test_heartbeat_is_removed_on_clean_exit():
    worker = Worker()
    worker.beat()
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(WorkerHeartbeat)) == 1
    worker.sign_off()
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(WorkerHeartbeat)) == 0
