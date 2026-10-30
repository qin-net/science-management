#!/usr/bin/env python3
"""Browser acceptance D1-D3 in specs/001, against a real stack.

Starts PostgreSQL, the API, a worker and a production web build on their own ports, database
and storage, runs Playwright, and stops everything. Nothing is mocked and development data is
untouched. Needs the Chromium build: npm --prefix web exec -- playwright install chromium

Run from the repo root: backend/.venv/bin/python scripts/e2e.py [playwright arguments]
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WEB = ROOT / "web"
PGDATA = ROOT / "data" / "pg-e2e"
STORAGE = ROOT / "data" / "files-e2e"
FIXTURES = WEB / "e2e" / ".fixtures"
API_PORT = 8100
WEB_PORT = 3100
START_TIMEOUT_SECONDS = 60

sys.path.insert(0, str(BACKEND))
from tests import pdf_samples  # noqa: E402


def write_fixtures() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    samples = {
        "multipage.pdf": pdf_samples.text_pdf(pages=3),
        "rotate-0.pdf": pdf_samples.transformed_pdf(),
        "rotate-90.pdf": pdf_samples.transformed_pdf(rotate=90),
        "rotate-180.pdf": pdf_samples.transformed_pdf(rotate=180),
        "rotate-270.pdf": pdf_samples.transformed_pdf(rotate=270),
        "crop.pdf": pdf_samples.transformed_pdf(crop=(30, 300, 330, 590)),
        "crop-rotate-90.pdf": pdf_samples.transformed_pdf(rotate=90, crop=(30, 300, 330, 590)),
        "scanned.pdf": pdf_samples.scanned_pdf(),
        "encrypted.pdf": pdf_samples.encrypted_pdf(),
        "corrupt.pdf": pdf_samples.corrupt_pdf(),
        "not-a-pdf.txt": b"plain text, not a PDF\n",
    }
    for name, data in samples.items():
        (FIXTURES / name).write_bytes(data)


def wait_for(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{process.args} exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready")


def main(argv: list[str]) -> int:
    write_fixtures()
    PGDATA.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(PGDATA)
    env = {
        **os.environ,
        "DATABASE_URL": server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1),
        "STORAGE_DIR": str(STORAGE),
        "ALLOWED_ORIGINS": f"http://localhost:{WEB_PORT},http://127.0.0.1:{WEB_PORT}",
        "ALLOW_REGISTRATION": "true",
        "COOKIE_SECURE": "false",
        "PARSER_DEFAULT": "pdfium",
        "DOCLING_PYTHON": "",
        "PARSE_RETRY_BASE_SECONDS": "0",
        "API_ORIGIN": f"http://127.0.0.1:{API_PORT}",
    }
    npm = shutil.which("npm") or "npm"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True
    )
    # Rewrites are compiled into the build, so API_ORIGIN has to be set here.
    subprocess.run([npm, "run", "build"], cwd=WEB, env=env, check=True)

    processes: list[subprocess.Popen] = []
    try:
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "researchos.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(API_PORT),
            ],
            cwd=BACKEND,
            env=env,
        )
        processes.append(api)
        processes.append(
            subprocess.Popen([sys.executable, "-m", "researchos.worker"], cwd=BACKEND, env=env)
        )
        wait_for(f"http://127.0.0.1:{API_PORT}/health/ready", api)
        web = subprocess.Popen(
            [npm, "exec", "--", "next", "start", "--port", str(WEB_PORT)], cwd=WEB, env=env
        )
        processes.append(web)
        wait_for(f"http://127.0.0.1:{WEB_PORT}/login", web)

        test_env = {
            **env,
            "E2E_BASE_URL": f"http://127.0.0.1:{WEB_PORT}",
            "E2E_FIXTURES": str(FIXTURES),
            "E2E_REAL_PAPER": str(pdf_samples.REAL_PAPER) if pdf_samples.REAL_PAPER.exists() else "",
        }
        return subprocess.run(
            [npm, "exec", "--", "playwright", "test", *argv], cwd=WEB, env=test_env
        ).returncode
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
