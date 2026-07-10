#!/usr/bin/env bash
#
# Persona — one-command self-host bootstrap.
#
# Brings up the shared stack (postgres + redis + api + frontend) from a clean
# checkout: ensures .env exists, builds images, starts the stack, polls each
# service's health, then prints the frontend URL and next steps (onboarding a
# brand, running the one-time login scripts, starting that brand's worker).
#
# Usage: scripts/setup.sh

set -euo pipefail

# Always run from the persona/ project root, regardless of caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

HEALTH_TIMEOUT_SECONDS=120
HEALTH_POLL_INTERVAL_SECONDS=3

log() {
    printf '[setup] %s\n' "$1"
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf '[setup] ERROR: required command not found: %s\n' "$1" >&2
        exit 1
    fi
}

compose() {
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" "$@"
}

# ── 1. Prerequisites ─────────────────────────────────────────────────────────
require_command docker
if ! docker compose version >/dev/null 2>&1; then
    printf '[setup] ERROR: "docker compose" (v2 plugin) is required.\n' >&2
    exit 1
fi

# ── 2. .env ───────────────────────────────────────────────────────────────────
if [ ! -f "${PROJECT_ROOT}/.env" ]; then
    if [ ! -f "${PROJECT_ROOT}/.env.example" ]; then
        printf '[setup] ERROR: .env.example not found — cannot bootstrap .env.\n' >&2
        exit 1
    fi
    cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
    log "Created .env from .env.example."
    log "Fill in your secrets (API keys, Telegram, WordPress, etc.) in .env,"
    log "then re-run scripts/setup.sh to continue."
    exit 0
fi

# ── 3. Build + start the shared stack ────────────────────────────────────────
log "Building images (postgres/redis are prebuilt, api + frontend are built locally)..."
compose build

log "Starting the shared stack (postgres, redis, api, frontend)..."
compose up -d postgres redis api frontend

# ── 4. Poll health ────────────────────────────────────────────────────────────
wait_for_healthy() {
    local service="$1"
    local elapsed=0

    log "Waiting for '${service}' to become healthy (timeout ${HEALTH_TIMEOUT_SECONDS}s)..."
    while [ "${elapsed}" -lt "${HEALTH_TIMEOUT_SECONDS}" ]; do
        local container_id status
        container_id="$(compose ps -q "${service}" 2>/dev/null || true)"

        if [ -n "${container_id}" ]; then
            status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${container_id}" 2>/dev/null || echo "unknown")"

            if [ "${status}" = "healthy" ]; then
                log "'${service}' is healthy."
                return 0
            elif [ "${status}" = "no-healthcheck" ]; then
                # No healthcheck defined for this service — running is enough.
                local state
                state="$(docker inspect --format '{{.State.Status}}' "${container_id}" 2>/dev/null || echo "unknown")"
                if [ "${state}" = "running" ]; then
                    log "'${service}' is running (no healthcheck defined)."
                    return 0
                fi
            elif [ "${status}" = "unhealthy" ]; then
                printf '[setup] ERROR: %s reported unhealthy.\n' "${service}" >&2
                compose logs --tail=50 "${service}" >&2 || true
                return 1
            fi
        fi

        sleep "${HEALTH_POLL_INTERVAL_SECONDS}"
        elapsed=$((elapsed + HEALTH_POLL_INTERVAL_SECONDS))
    done

    printf '[setup] ERROR: timed out waiting for %s to become healthy.\n' "${service}" >&2
    compose logs --tail=50 "${service}" >&2 || true
    return 1
}

for service in postgres redis api; do
    wait_for_healthy "${service}"
done

# ── 5. Done — print next steps ───────────────────────────────────────────────
cat <<'EOF'

[setup] Stack is up.

  Frontend:  http://localhost:3000
  API:       http://localhost:5001/api/v1/health
  Postgres:  localhost:5432 (schema auto-applied from db/schema.sql)
  Redis:     localhost:6379

Next steps:
  1. Onboard a brand via the frontend (or the brands API), which provisions
     brands/<slug>/ on disk and a matching DB row.
  2. Run the one-time interactive login scripts on the HOST for that brand
     (requires local Playwright: pip install playwright && playwright
     install chromium):
       BRAND_DIR=./brands/<slug> python scripts/ig_login.py
       BRAND_DIR=./brands/<slug> python scripts/fb_login.py
  3. Start that brand's worker (own container, own Playwright session):
       BRAND_DIR=./brands/<slug> PERSONA_BRAND=<slug> \
         docker compose -f docker-compose.worker.yml -p <slug> up -d

EOF
