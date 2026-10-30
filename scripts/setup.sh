#!/usr/bin/env bash
# First-time setup: installs dependencies and prepares .env. Safe to run again.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_tool uv "Install it from https://docs.astral.sh/uv/getting-started/installation/"
require_tool npm "Install Node.js 20.9 or newer from https://nodejs.org/"

uv sync --directory "$ROOT/backend"
npm --prefix "$ROOT/web" install

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "created .env from .env.example"
fi

bash "$ROOT/scripts/migrate.sh"
echo "setup complete. Start everything with: bash scripts/dev.sh"
