#!/usr/bin/env bash
# Starts the API, worker and web app together. Ctrl-C stops all three; the embedded database
# keeps running so a restart is quick (stop it with: bash scripts/stop_db.sh).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

bash "$ROOT/scripts/migrate.sh"
ensure_database

pids=()
stop_all() {
  trap - INT TERM EXIT
  if [ "${#pids[@]}" -gt 0 ]; then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap stop_all INT TERM EXIT

(cd "$ROOT/backend" && exec uv run uvicorn researchos.main:app --host 127.0.0.1 --port 8000) &
pids+=($!)
(cd "$ROOT/backend" && exec uv run python -m researchos.worker) &
pids+=($!)
(cd "$ROOT/web" && exec npm run dev) &
pids+=($!)

echo "web: http://localhost:3000   api: http://127.0.0.1:8000   health: /health/ready"
# Exit as soon as one process dies, so a crash is not hidden behind the two still running.
# Polled because macOS ships bash 3.2, which has no `wait -n`.
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "a service stopped; shutting down the others" >&2
      exit 1
    fi
  done
  sleep 1
done
