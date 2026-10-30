"""Acceptance C10 and the database half of E4: real API and worker processes, a real
PostgreSQL stop and start. Needs the embedded server, whose pg_ctl the test can reach."""

import hashlib
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests.conftest import BACKEND_DIR, EMBEDDED_PGDATA
from tests.pdf_samples import text_pdf

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("TEST_DATABASE_URL")), reason="needs the embedded PostgreSQL"
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start(*args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", *args],
        cwd=BACKEND_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop(*processes: subprocess.Popen) -> None:
    for process in processes:
        process.send_signal(signal.SIGTERM)
    for process in processes:
        process.wait(timeout=20)


def pg_ctl(action: str) -> None:
    from pgserver import postgres_server

    binary = Path(postgres_server.POSTGRES_BIN_PATH) / "pg_ctl"
    subprocess.run(
        [str(binary), "-D", str(EMBEDDED_PGDATA), "-w", "-m", "fast", action],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_until(check, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
        except httpx.TransportError:
            value = None
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


def boot(port: int) -> tuple[subprocess.Popen, subprocess.Popen]:
    api = start("uvicorn", "researchos.main:app", "--port", str(port))
    worker = start("researchos.worker")
    return api, worker


def test_c10_data_survives_restarting_everything():
    port = free_port()
    api, worker = boot(port)
    data = text_pdf("Survives restart")
    try:
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20)
        wait_until(lambda: client.get("/health/ready").status_code == 200)
        client.post(
            "/api/v1/auth/register",
            json={
                "email": f"restart-{uuid.uuid4().hex[:8]}@example.com",
                "password": "correct horse battery",
                "display_name": "Restart",
            },
        ).raise_for_status()
        workspace = client.get("/api/v1/workspaces").json()[0]["id"]
        project = client.post(
            f"/api/v1/workspaces/{workspace}/projects", json={"name": "Durable"}
        ).json()
        paper = client.post(
            f"/api/v1/projects/{project['id']}/papers",
            content=data,
            headers={"Content-Type": "application/pdf"},
        ).json()
        base = f"/api/v1/projects/{project['id']}/papers/{paper['id']}"
        wait_until(lambda: client.get(base).json()["parse_job"]["status"] == "succeeded")
        version = paper["current_version"]["id"]
        before = client.get(f"{base}/versions/{version}/blocks").json()
        assert before["total"] == 2

        # The API stays up while PostgreSQL is down and says so.
        pg_ctl("stop")
        try:
            down = client.get("/health/ready")
            assert down.status_code == 503
            assert down.json()["checks"]["database"]["status"] == "failed"
            assert client.get("/health/live").status_code == 200
            assert client.get(f"{base}").status_code == 503
            assert client.get(f"{base}").json()["error"]["code"] == "database_unavailable"
        finally:
            pg_ctl("restart")

        stop(api, worker)
        api, worker = boot(port)
        wait_until(lambda: client.get("/health/ready").status_code == 200)

        assert client.get(f"{base}/versions/{version}/blocks").json() == before
        restored = client.get(f"{base}/versions/{version}/file")
        assert hashlib.sha256(restored.content).hexdigest() == hashlib.sha256(data).hexdigest()
        events = client.get(f"/api/v1/projects/{project['id']}/events").json()
        assert "paper.parsed" in {event["event_type"] for event in events}
    finally:
        for process in (api, worker):
            if process.poll() is None:
                process.kill()
                process.wait()
