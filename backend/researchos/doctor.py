"""Startup diagnostics: `python -m researchos.doctor`. Exits non-zero if something required
is wrong, and says how to fix it. Connection strings are never printed."""

import sys

from pydantic import ValidationError

from researchos.api.health import run_checks
from researchos.config import get_settings
from researchos.parsers.registry import docling_status

HINTS = {
    "database": "set DATABASE_URL in .env, or leave it empty and run: bash scripts/doctor.sh",
    "migrations": "run: bash scripts/migrate.sh",
    "storage": "make STORAGE_DIR writable",
    "worker": "run: bash scripts/dev.sh, or: cd backend && uv run python -m researchos.worker",
}


def main() -> int:
    try:
        get_settings()
    except ValidationError as exc:
        print("[failed] configuration")
        for error in exc.errors(include_input=False, include_url=False):
            where = ".".join(str(part) for part in error["loc"]).upper()
            print(f"  {where}: {error['msg']}")
        return 1

    failed = False
    for name, check in run_checks().items():
        ok = check["status"] == "ok"
        detail = f" ({check['detail']})" if check.get("detail") else ""
        print(f"[{'ok' if ok else 'failed'}] {name}{detail}")
        if not ok:
            failed = True
            print(f"  fix: {HINTS[name]}")
    print(f"[info] parser pdfium: available; parser docling: {docling_status()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
