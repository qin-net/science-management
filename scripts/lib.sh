#!/usr/bin/env bash
# Runs a backend command with DATABASE_URL set, starting the embedded PostgreSQL when neither
# the environment nor .env provides one (D-001). Sourced by the other scripts, never run alone.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: $1 is required. $2" >&2
    exit 1
  fi
}

env_file_database_url() {
  [ -f "$ROOT/.env" ] || return 0
  # Last assignment wins, as in the settings loader. The value is never printed.
  local line
  line="$(grep -E '^DATABASE_URL=' "$ROOT/.env" | tail -n 1 || true)"
  line="${line#DATABASE_URL=}"
  line="${line%\"}"
  line="${line#\"}"
  printf '%s' "$line"
}

ensure_database() {
  if [ -n "${DATABASE_URL:-}" ] || [ -n "$(env_file_database_url)" ]; then
    return 0
  fi
  echo "DATABASE_URL is empty: using the embedded PostgreSQL in data/pg" >&2
  DATABASE_URL="$(uv run --directory "$ROOT/backend" python "$ROOT/scripts/dev_db.py" up 2>/dev/null)"
  if [ -z "$DATABASE_URL" ]; then
    echo "error: the embedded PostgreSQL did not start. Run it directly to see why:" >&2
    echo "  uv run --directory backend python ../scripts/dev_db.py up" >&2
    exit 1
  fi
  export DATABASE_URL
}
