"""Mutation check: each change below reintroduces a bug the suite claims to catch.
Every mutated run must fail; a surviving mutant means the test proves nothing. Restores the
file in all cases. Requires a clean tree of backend/researchos, because it edits files in
place. Run from the repo root: backend/.venv/bin/python scripts/mutation_check.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PKG = BACKEND / "researchos"

MUTANTS = [
    ("C2 rotation ignored", "parsers/base.py", "if rotation == 90:", "if rotation == 91:", "tests/test_parsing.py"),
    ("C2 crop ignored", "parsers/pdfium_parser.py", "page_box = page.get_bbox()", "page_box = (0, 0, 400, 600)", "tests/test_parsing.py"),
    ("C5 fencing on owner removed", "worker.py", "                Job.lease_owner == self.worker_id,\n", "", "tests/test_jobs.py"),
    ("C5 fencing on attempt removed", "worker.py", "                Job.attempts == claim.attempt,\n", "", "tests/test_jobs.py"),
    ("C6 no attempt limit on retry", "worker.py", "elif retryable and job.attempts < job.max_attempts:", "elif retryable:", "tests/test_jobs.py"),
    ("C6 kill only the leader", "worker.py", "os.killpg(proc.pid, signal.SIGKILL)", "proc.kill()", "tests/test_jobs.py"),
    ("C3 empty output accepted", "worker.py", "if not any(block.text.strip() for block in result.blocks):", "if False:", "tests/test_jobs.py"),
    ("C7 cancel ignored", "worker.py", "        if cancelled:\n            raise CancelRequested\n", "", "tests/test_jobs.py"),
    ("C9 switch on failure path", "worker.py", "version.current_parse_job_id = job.id", "pass", "tests/test_jobs.py"),
    ("B6 metadata overwrites user", "worker.py", 'if value and provenance.get(field) != "user":', "if value:", "tests/test_import.py"),
    ("A4 outsider treated as viewer", "security.py", '    if role is None:\n        # Same answer for "absent" and "not yours": workspace existence does not leak.\n        raise ApiError(404, "not_found", "workspace not found")\n', '    if role is None:\n        return "viewer"\n', "tests/test_auth_permissions.py"),
    ("A5 role check removed", "security.py", "if ROLE_RANK[role] < ROLE_RANK[minimum]:", "if False:", "tests/test_auth_permissions.py"),
    ("A7 version not scoped to project", "api/papers.py", "            PaperVersion.project_id == project_id,\n        )\n    )\n    if version is None:", "        )\n    )\n    if version is None:", "tests/test_auth_permissions.py"),
    ("A9 CSRF guard off", "security.py", 'if request.method in {"GET", "HEAD", "OPTIONS"}:', "if True:", "tests/test_auth_permissions.py"),
    ("B2 size limit off", "storage.py", "if self.size > self.max_bytes:", "if False:", "tests/test_import.py"),
    ("B-commit cleanup off", "api/papers.py", "        db.rollback()\n        storage.delete(key)\n        raise\n", "        db.rollback()\n        raise\n", "tests/test_import.py"),
    ("E6 request id missing on 500", "errors.py", '            headers={"X-Request-ID": request_id},\n', "", "tests/test_foundation.py"),
]


def main() -> int:
    survivors = []
    for name, relative, old, new, tests in MUTANTS:
        path = PKG / relative
        source = path.read_text(encoding="utf-8")
        if old not in source:
            print(f"[stale]    {name}: pattern not found")
            survivors.append(name)
            continue
        path.write_text(source.replace(old, new, 1), encoding="utf-8")
        try:
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", tests],
                cwd=BACKEND,
                capture_output=True,
                text=True,
                timeout=240,
            )
        finally:
            path.write_text(source, encoding="utf-8")
        killed = run.returncode == 1
        summary = next((l for l in run.stdout.splitlines() if l.startswith("FAILED")), "")
        print(f"[{'killed' if killed else 'SURVIVED'}] {name} {summary[:90]}")
        if not killed:
            survivors.append(name)
    print(f"{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
