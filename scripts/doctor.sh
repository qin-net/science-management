#!/usr/bin/env bash
# Checks configuration and real dependencies, and prints how to fix what is wrong.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

ensure_database
uv run --directory "$ROOT/backend" python -m researchos.doctor
