#!/usr/bin/env python3
"""Fail if a feature ID in the dev doc is missing from specs/REQUIREMENTS.md,
or a row carries an unknown status, or claims 完成 without evidence."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID = r"(?:HOME|LIT|READ|MATRIX|BRAIN|WRITE|LAB|MEET|PROJ|TEAM|EXTRA|AUDIT)-\d+"
STATUSES = {"未做", "进行中", "完成", "实验", "阻塞"}


def main() -> int:
    doc = (ROOT / "ResearchOS开发文档.md").read_text(encoding="utf-8")
    table = (ROOT / "specs" / "REQUIREMENTS.md").read_text(encoding="utf-8")
    wanted = set(re.findall(rf"^\| ({ID}) \|", doc, re.M))
    rows = re.findall(rf"^\| ({ID}) \| .* \| ([^|]*?) \| ([^|]*?) \|$", table, re.M)
    seen = {fid for fid, _, _ in rows}
    problems = [f"missing from mapping table: {fid}" for fid in sorted(wanted - seen)]
    for fid, status, note in rows:
        if status not in STATUSES:
            problems.append(f"{fid}: unknown status {status!r}")
        if status == "完成" and not note.strip():
            problems.append(f"{fid}: marked 完成 without evidence")
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"requirements: {len(wanted)} ids, {len(rows)} rows, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
