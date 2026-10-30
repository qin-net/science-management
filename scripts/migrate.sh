#!/usr/bin/env bash
# Applies database migrations. Uses the embedded PostgreSQL when DATABASE_URL is empty.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

ensure_database
uv run --directory "$ROOT/backend" alembic upgrade head
