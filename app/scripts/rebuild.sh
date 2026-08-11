#!/bin/bash
# Rebuild + swap the stack's containers from the current checkout.
#
#   ./scripts/rebuild.sh                # all four app services
#   ./scripts/rebuild.sh frontend       # just one (fast)
#   ./scripts/rebuild.sh api frontend   # any subset
#
# Encodes three lessons learned the hard way:
#   1. Both compose files, always — bare `docker compose` misses worker/dispatcher.
#   2. --force-recreate, always — a successful BUILD does not swap the running
#      container; without it you get fresh images under stale containers.
#   3. Trust container AGE, not exit codes — this script verifies the swap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"   # app/

SERVICES=("${@:-}")
if [ ${#SERVICES[@]} -eq 0 ] || [ -z "${SERVICES[0]}" ]; then
  SERVICES=(api worker dispatcher frontend)
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.worker.yml)

echo "==> building + recreating: ${SERVICES[*]}"
"${COMPOSE[@]}" up -d --build --force-recreate "${SERVICES[@]}"

echo "==> verifying the swap actually happened (container age must be seconds)"
sleep 3
FAILED=0
for svc in "${SERVICES[@]}"; do
  AGE=$(docker ps --filter "name=persona-${svc}-1" --format "{{.RunningFor}}")
  case "$AGE" in
    *second*|*Less*) echo "    ${svc}: ${AGE}  OK" ;;
    "")              echo "    ${svc}: NOT RUNNING"; FAILED=1 ;;
    *)               echo "    ${svc}: ${AGE}  — STALE, swap did not happen"; FAILED=1 ;;
  esac
done

if [ "$FAILED" -eq 1 ]; then
  echo "==> FAILED: at least one container was not swapped." >&2
  echo "    Common cause: a leftover compose/build process holding the project —" >&2
  echo "    check 'ps aux | grep docker' and re-run." >&2
  exit 1
fi

echo "==> smoke checks"
sleep 2
for i in $(seq 1 20); do
  if curl -sf -o /dev/null -m 3 "http://localhost:5001/api/v1/social-posts?status=queued"; then
    break
  fi
  sleep 2
done
curl -s -m 5 -o /dev/null -w "    api      → HTTP %{http_code}\n" "http://localhost:5001/api/v1/social-posts?status=queued"
curl -s -m 5 -o /dev/null -w "    frontend → HTTP %{http_code}\n" "http://localhost:3000/"

echo "==> done"
