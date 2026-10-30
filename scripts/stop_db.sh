#!/usr/bin/env bash
# Stops the embedded PostgreSQL. Its data stays in data/pg.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

uv run --directory "$ROOT/backend" python "$ROOT/scripts/dev_db.py" stop
