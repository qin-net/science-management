#!/usr/bin/env bash
# Everything a change must pass. Runs every step and reports all failures, not just the first.
set -uo pipefail
source "$(dirname "$0")/lib.sh"

failed=()
step() {
  local name="$1"
  shift
  echo "==> $name"
  if ! "$@"; then
    failed+=("$name")
  fi
}

backend() {
  uv run --directory "$ROOT/backend" "$@"
}

step "secrets" backend python "$ROOT/scripts/check_secrets.py"
step "requirements mapping" backend python "$ROOT/scripts/check_requirements.py"
step "ruff" backend ruff check .
step "mypy" backend mypy
step "migration drift and backend tests" backend pytest -q -p no:cacheprovider
step "web typecheck" npm --prefix "$ROOT/web" run typecheck
step "web build" npm --prefix "$ROOT/web" run build

if [ "${#failed[@]}" -gt 0 ]; then
  echo "FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "all checks passed"
