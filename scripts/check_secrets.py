#!/usr/bin/env python3
"""Fails if tracked or about-to-be-tracked files contain secrets or local data.

Looks at what git would commit: tracked files plus untracked files that are not ignored.
Reports file and line only, so a real secret is not copied into CI logs.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)\.env$"), "environment file"),
    (re.compile(r"(^|/)\.env\.(?!example$)[^/]+$"), "environment file"),
    (re.compile(r"\.(pem|key|p12|pfx)$"), "private key file"),
    (re.compile(r"^data/"), "local data directory"),
    (re.compile(r"^samples/pdfs/"), "sample PDF, not redistributable by default"),
]
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{20,}"), "API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), "Hugging Face token"),
    # Templates are fine: PASSWORD, {placeholder}, $VARIABLE and <value> hold no secret.
    (re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:(?!PASSWORD@|[{$<])[^@\s]+@"), "database password"),
]
SKIP_CONTENT = re.compile(r"(\.lock|package-lock\.json|\.pdf|\.png|\.jpg|\.ico)$")


def candidates(root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [name for name in listed.stdout.decode().split("\0") if name]


def scan(root: Path, names: list[str]) -> list[str]:
    problems = []
    for name in names:
        for pattern, label in FORBIDDEN_PATHS:
            if pattern.search(name):
                problems.append(f"{name}: {label} must not be committed")
        path = root / name
        if SKIP_CONTENT.search(name) or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, label in SECRET_PATTERNS:
                if pattern.search(line):
                    problems.append(f"{name}:{number}: looks like a {label}")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve() if argv else ROOT
    names = candidates(root)
    problems = scan(root, names)
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"secrets: {len(names)} files, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
