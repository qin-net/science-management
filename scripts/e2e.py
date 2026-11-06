#!/usr/bin/env python3
"""Browser acceptance D1-D3 in specs/001, against a real stack.

Starts PostgreSQL, the API, a worker and a production web build on their own ports, database
and storage, runs Playwright, and stops everything. Nothing is mocked and development data is
untouched. Needs the Chromium build: npm --prefix web exec -- playwright install chromium

Run from the repo root: backend/.venv/bin/python scripts/e2e.py [playwright arguments]
"""

import json
import os
import shutil
import threading
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pgserver
import psycopg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WEB = ROOT / "web"
PGDATA = ROOT / "data" / "pg-e2e"
STORAGE = ROOT / "data" / "files-e2e"
FIXTURES = WEB / "e2e" / ".fixtures"
API_PORT = 8100
WEB_PORT = 3100
START_TIMEOUT_SECONDS = 60

CONTROL_PORT = 8101

sys.path.insert(0, str(BACKEND))
from tests import pdf_samples  # noqa: E402
from tests.fake_model import FakeModel, ModelReply, answer, message_reply  # noqa: E402
from tests.fake_upstream import ARXIV_EMPTY, FakeUpstream, Reply  # noqa: E402

JSON = {"Content-Type": "application/json"}
EMPTY = {
    "arxiv": Reply(body=ARXIV_EMPTY),
    "openalex": Reply(body='{"meta": {"count": 0}, "results": []}', headers=JSON),
    "crossref": Reply(body='{"message": {"total-results": 0, "items": []}}', headers=JSON),
}
# Seconds a "slow" source stays silent: longer than SEARCH_SOURCE_TIMEOUT_SECONDS (2) so it times
# out, and the one retry makes the source take about four seconds in all.
SLOW_SECONDS = 5.0
# A "delayed" source answers normally, but late enough for a test to see the search in progress.
DELAYED_SECONDS = 1.5


def apply_mode(upstream: FakeUpstream, source: str, mode: str) -> None:
    """Make one fake source behave a certain way. Used by the browser tests."""
    defaults = FakeUpstream()
    default = defaults.handlers[source]
    if mode == "ok":
        upstream.handlers[source] = default
    elif mode == "empty":
        upstream.set(source, EMPTY[source])
    elif mode == "rate_limited":
        upstream.set(source, Reply(429, "slow down", {"Retry-After": "30"}))
    elif mode == "unavailable":
        upstream.set(source, Reply(503, "down"))
    elif mode == "slow":
        upstream.set(source, Reply(delay=SLOW_SECONDS))
    elif mode == "delayed":
        upstream.handlers[source] = lambda seen: Reply(
            **{**vars(default(seen)), "delay": DELAYED_SECONDS}
        )
    elif mode == "bad_json":
        upstream.set(source, Reply(body="{not json", headers=JSON))
    elif mode == "markup":
        upstream.handlers[source] = lambda seen: _markup(default(seen), source)
    else:
        raise ValueError(f"unknown mode {mode}")


def _markup(reply: Reply, source: str) -> Reply:
    """Put script-looking text into titles and abstracts of the recorded reply."""
    body = reply.body.decode() if isinstance(reply.body, bytes) else reply.body
    hostile = "<img src=x onerror=window.__pwned=1>PWNED-TITLE"
    if source == "arxiv":
        head, entry, rest = body.partition("<entry>")
        rest = rest.replace("<title>", f"<title>{hostile.replace('<', '&lt;')} ", 1)
        body = head + entry + rest
    elif source == "openalex":
        data = json.loads(body)
        for work in data["results"]:
            work["title"] = hostile + (work.get("title") or "")
        body = json.dumps(data)
    else:
        data = json.loads(body)
        for item in data["message"]["items"]:
            item["title"] = [hostile + ((item.get("title") or [""])[0])]
        body = json.dumps(data)
    return Reply(reply.status, body, reply.headers)


HOSTILE = "<img src=x onerror=window.__pwned=1>"


def apply_model_mode(model: FakeModel, mode: str) -> None:
    """Make the fake language model behave a certain way for the browser tests."""
    if mode == "ok":
        model.default = message_reply(
            text=answer(
                True,
                [
                    {"text": "Answer with evidence.", "block_numbers": [1, 999]},
                    {"text": "Answer without a source.", "block_numbers": []},
                ],
            )
        )
    elif mode == "hostile":
        model.default = message_reply(
            text=answer(True, [{"text": f"{HOSTILE}claim", "block_numbers": [1]}])
        )
    elif mode == "unanswerable":
        model.default = message_reply(text=answer(False, []))
    elif mode == "down":
        model.default = ModelReply(500, {"type": "error"})
    elif mode == "busy":
        model.default = ModelReply(529, {"type": "error"})
    elif mode in ("translate", "translate_hostile"):
        # Every request in the browser tests translates one block, so block 1 is the only one.
        words = f"{HOSTILE}译文" if mode == "translate_hostile" else "标记文本（译文）"
        model.default = message_reply(text=json.dumps({"translations": [{"n": 1, "text": words}]}))
    else:
        raise ValueError(f"unknown model mode {mode}")


HOLD_FUNCTION = """
CREATE OR REPLACE FUNCTION e2e_hold_job() RETURNS trigger AS $$
BEGIN
    NEW.next_run_at := now() + interval '1 hour';
    RETURN NEW;
END $$ LANGUAGE plpgsql
"""
DROP_HOLD = "DROP TRIGGER IF EXISTS e2e_hold ON jobs"


def start_control(upstream: FakeUpstream, model: FakeModel, database_url: str) -> ThreadingHTTPServer:
    """A tiny HTTP control surface so Playwright can steer the fake sources between tests.

    GET /reset                      forget handlers, requests seen, cache, cooldowns, call counts
    GET /set?source=X&mode=Y        change one source (see apply_mode)
    GET /model?mode=Y               change what the fake language model answers (apply_model_mode)
    GET /model-seen                 how many requests the fake language model received
    GET /seen?source=X              how many requests that source received since the last reset
    GET /hold-jobs                  new parse jobs wait an hour before a worker may take them
    GET /release-jobs               undo /hold-jobs and let waiting jobs run
    GET /alter-anchors              make every annotation remember a different text (see below)
    """
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    class Control(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            status, payload = 200, {"ok": True}
            try:
                if parts.path == "/reset":
                    fresh = FakeUpstream()
                    upstream.handlers = fresh.handlers
                    for name in upstream.seen:
                        upstream.seen[name] = []
                    with psycopg.connect(dsn, autocommit=True) as conn:
                        conn.execute("DELETE FROM search_cache")
                        conn.execute("DELETE FROM source_state")
                        conn.execute("DELETE FROM search_calls")
                        conn.execute(DROP_HOLD)
                elif parts.path == "/set":
                    apply_mode(upstream, query["source"], query["mode"])
                elif parts.path == "/hold-jobs":
                    # A real parse of a small PDF ends in about 0.1 s, too fast for a browser
                    # test to click "cancel". Pushing next_run_at forward keeps the job queued
                    # (the worker only claims jobs that are due) without touching worker code.
                    with psycopg.connect(dsn, autocommit=True) as conn:
                        conn.execute(HOLD_FUNCTION)
                        conn.execute("DROP TRIGGER IF EXISTS e2e_hold ON jobs")
                        conn.execute(
                            "CREATE TRIGGER e2e_hold BEFORE INSERT ON jobs "
                            "FOR EACH ROW EXECUTE FUNCTION e2e_hold_job()"
                        )
                elif parts.path == "/release-jobs":
                    with psycopg.connect(dsn, autocommit=True) as conn:
                        conn.execute(DROP_HOLD)
                        conn.execute("UPDATE jobs SET next_run_at = now() WHERE status = 'queued'")
                elif parts.path == "/alter-anchors":
                    # The pdfium parser is deterministic, so re-parsing the same file cannot
                    # change a page. This gives the stored quote a hash no block has, which is
                    # exactly the state a changed page leaves behind (specs/003, L3).
                    with psycopg.connect(dsn, autocommit=True) as conn:
                        conn.execute(
                            "UPDATE notes SET anchor = jsonb_set(anchor, '{content_hash}', "
                            "to_jsonb('sha256:' || repeat('ab', 32))) WHERE anchor IS NOT NULL"
                        )
                elif parts.path == "/model":
                    model.reset()
                    apply_model_mode(model, query["mode"])
                elif parts.path == "/model-seen":
                    payload = {"count": len(model.calls)}
                elif parts.path == "/seen":
                    payload = {"count": upstream.count(query["source"])}
                else:
                    status, payload = 404, {"ok": False}
            except Exception as exc:  # report to the test instead of hanging it
                status, payload = 500, {"ok": False, "error": repr(exc)}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), Control)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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
        "hostile.pdf": pdf_samples.text_pdf(HOSTILE),
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
    upstream = FakeUpstream().start()
    model = FakeModel().start()
    database_url = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "STORAGE_DIR": str(STORAGE),
        "ALLOWED_ORIGINS": f"http://localhost:{WEB_PORT},http://127.0.0.1:{WEB_PORT}",
        "ALLOW_REGISTRATION": "true",
        "COOKIE_SECURE": "false",
        "PARSER_DEFAULT": "pdfium",
        "DOCLING_PYTHON": "",
        "PARSE_RETRY_BASE_SECONDS": "0",
        "API_ORIGIN": f"http://127.0.0.1:{API_PORT}",
        # Search talks to a local stand-in for the three sources; nothing leaves the machine.
        "ARXIV_BASE_URL": f"{upstream.url}/arxiv",
        "OPENALEX_BASE_URL": f"{upstream.url}/openalex",
        "CROSSREF_BASE_URL": f"{upstream.url}/crossref",
        "OPENALEX_API_KEY": "",
        "SEARCH_SOURCE_TIMEOUT_SECONDS": "2",
        "SEARCH_MAX_WAIT_SECONDS": "3",
        "SEARCH_CACHE_TTL_SECONDS": "600",
        "SEARCH_USER_PER_MINUTE": "1000",
        "ARXIV_MIN_INTERVAL_SECONDS": "0.3",
        # The language model is a local stand-in too; no question leaves the machine.
        "ANTHROPIC_API_KEY": "e2e-key-not-a-secret",
        "ANTHROPIC_BASE_URL": model.url,
        # Pinned so a developer's own model settings in .env cannot change the run.
        "MODEL_NAME": "claude-sonnet-5-5",
        "MODEL_MAX_OUTPUT_TOKENS": "4096",
        "MODEL_CONTEXT_CHARS": "120000",
        "MODEL_USER_PER_MINUTE": "1000",
        "MODEL_USER_PER_DAY": "1000",
        "MODEL_TIMEOUT_SECONDS": "10",
    }
    control = start_control(upstream, model, database_url)
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
            "E2E_CONTROL": f"http://127.0.0.1:{CONTROL_PORT}",
            "E2E_REAL_PAPER": str(pdf_samples.REAL_PAPER) if pdf_samples.REAL_PAPER.exists() else "",
        }
        return subprocess.run(
            [npm, "exec", "--", "playwright", "test", *argv], cwd=WEB, env=test_env
        ).returncode
    finally:
        control.shutdown()
        upstream.stop()
        model.stop()
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
