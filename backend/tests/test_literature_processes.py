"""F10 across processes: the pacing state is shared through PostgreSQL, not process memory."""

import json
import subprocess
import sys
import time
from pathlib import Path

from tests.fake_upstream import default_arxiv, default_crossref

BACKEND_DIR = Path(__file__).resolve().parents[1]

CHILD = """
import json, sys, time
from researchos.config import get_settings
from researchos.literature import service

start_at, query = float(sys.argv[1]), sys.argv[2]
time.sleep(max(0.0, start_at - time.time()))
outcomes = service.run_search(
    get_settings(), ["arxiv", "crossref"],
    {"query": query, "limit": 5, "year_from": None, "year_to": None},
)
print(json.dumps({o.source: o.status for o in outcomes}))
"""


def test_f10_two_real_processes_do_not_overlap_or_beat_the_interval(upstream, monkeypatch):
    def slow(handler):
        def run(seen):
            time.sleep(0.15)
            return handler(seen)

        return run

    upstream.set("arxiv", slow(default_arxiv))
    upstream.set("crossref", slow(default_crossref))
    monkeypatch.setenv("SEARCH_MAX_WAIT_SECONDS", "30")
    monkeypatch.setenv("SEARCH_SOURCE_TIMEOUT_SECONDS", "5")
    start_at = time.time() + 2.0
    children = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD, str(start_at), f"process query {index}"],
            cwd=BACKEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    outputs = [child.communicate(timeout=60) for child in children]
    for child, (out, err) in zip(children, outputs, strict=True):
        assert child.returncode == 0, err
        assert json.loads(out.strip().splitlines()[-1]) == {"arxiv": "ok", "crossref": "ok"}
    for source, gap in (("arxiv", 0.3), ("crossref", 1.0)):
        calls = sorted(upstream.seen[source], key=lambda s: s.started)
        assert len(calls) == 2, source
        assert calls[1].started >= calls[0].finished, f"{source} requests overlapped"
        assert calls[1].started - calls[0].finished >= gap - 0.05, f"{source} too close"
