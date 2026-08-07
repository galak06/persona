#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
cd "$APP_DIR"

# Load .env so spawned processes (API, frontend) see DATABASE_URL/BRAND_DIR/etc.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Start Phoenix (Tracing)
docker compose -f docker/phoenix/docker-compose.yml up -d

# Start API in background (project venv, not bare `python`)
.venv/bin/python -m api.approval_api &

# Start Frontend
cd frontend && npm run dev
