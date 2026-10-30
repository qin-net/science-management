#!/usr/bin/env python3
"""Embedded PostgreSQL for local development (D-001). Development only: production uses a
real server through DATABASE_URL.

  dev_db.py up    start if needed and print the SQLAlchemy URL
  dev_db.py stop  stop the server, keeping its data
"""

import subprocess
import sys
from pathlib import Path

import pgserver
from pgserver import postgres_server

ROOT = Path(__file__).resolve().parents[1]
PGDATA = ROOT / "data" / "pg"


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "up"
    if action == "up":
        PGDATA.mkdir(parents=True, exist_ok=True)
        # cleanup_mode=None leaves the server running after this script exits.
        server = pgserver.get_server(PGDATA, cleanup_mode=None)
        print(server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1))
        return 0
    if action == "stop":
        if not (PGDATA / "postmaster.pid").exists():
            print("embedded database is not running")
            return 0
        pg_ctl = Path(postgres_server.POSTGRES_BIN_PATH) / "pg_ctl"
        subprocess.run([str(pg_ctl), "-D", str(PGDATA), "-m", "fast", "stop"], check=True)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
