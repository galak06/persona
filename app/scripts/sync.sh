#!/bin/bash
# Bring the running stack up to date with a git ref, and PROVE it.
#
#   ./scripts/sync.sh                     # sync to origin/main
#   ./scripts/sync.sh --ref origin/dev    # some other ref
#   ./scripts/sync.sh --no-pull           # rebuild from the checkout as-is
#   ./scripts/sync.sh --check             # report only, change nothing
#   ./scripts/sync.sh api worker          # limit to some services
#
# Why this exists on top of rebuild.sh:
#
#   rebuild.sh answers "did the containers get swapped?" It does that well.
#   It cannot answer "is the running code current?", because it builds from
#   whatever the checkout happens to be. A container built from a checkout
#   that is ten commits behind passes every check rebuild.sh makes.
#
#   That is not hypothetical: on 2026-09-01 all three app containers were
#   rebuilt successfully and kept running code with a known credential leak
#   in it, because the checkout was behind main.
#
#   So this script owns the git step and the proof; rebuild.sh still owns the
#   build and the swap. The proof works by baking the commit into the image
#   (PERSONA_GIT_SHA) and reading it back out of the running container.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$(cd "$APP_DIR" && git rev-parse --show-toplevel)"
cd "$APP_DIR"

REF="origin/main"
PULL=1
CHECK_ONLY=0
SERVICES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)      REF="$2"; shift 2 ;;
    --no-pull)  PULL=0; shift ;;
    --check)    CHECK_ONLY=1; PULL=0; shift ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    -*)         echo "unknown flag: $1" >&2; exit 2 ;;
    *)          SERVICES+=("$1"); shift ;;
  esac
done
[ ${#SERVICES[@]} -eq 0 ] && SERVICES=(api worker dispatcher frontend)

# ── what is running right now ────────────────────────────────────────────────

container_sha() {
  # Empty string if the container is absent or predates the stamp.
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "persona-$1-1" 2>/dev/null \
    | sed -n 's/^PERSONA_GIT_SHA=//p' | head -1
}

report() {
  local checkout_sha="$1" drift=0
  echo "==> running containers vs checkout ($(git -C "$REPO_DIR" rev-parse --short HEAD))"
  for svc in "${SERVICES[@]}"; do
    local sha; sha="$(container_sha "$svc")"
    if [ -z "$sha" ]; then
      printf '    %-11s %s\n' "$svc" "no stamp — built before sync.sh, treat as stale"
      drift=1
    elif [ "$sha" = "unknown" ]; then
      printf '    %-11s %s\n' "$svc" "unstamped build — treat as stale"
      drift=1
    elif [ "$sha" = "$checkout_sha" ]; then
      printf '    %-11s %s  current\n' "$svc" "${sha:0:7}"
    else
      printf '    %-11s %s  STALE (checkout is %s)\n' "$svc" "${sha:0:7}" "${checkout_sha:0:7}"
      drift=1
    fi
  done
  return $drift
}

# ── git ──────────────────────────────────────────────────────────────────────

if [ "$PULL" -eq 1 ]; then
  echo "==> fetching"
  git -C "$REPO_DIR" fetch --quiet origin

  DIRTY="$(git -C "$REPO_DIR" status --porcelain | wc -l | tr -d ' ')"
  if [ "$DIRTY" -gt 0 ]; then
    echo "==> WARNING: $DIRTY uncommitted file(s) in the checkout." >&2
    echo "    A build bakes these in. Review before deploying:" >&2
    git -C "$REPO_DIR" status --porcelain | sed 's/^/      /' >&2
    printf '    Continue? [y/N] ' >&2
    read -r reply
    case "$reply" in [yY]*) ;; *) echo "    aborted."; exit 1 ;; esac
  fi

  BEHIND="$(git -C "$REPO_DIR" rev-list --count "HEAD..$REF" 2>/dev/null || echo 0)"
  if [ "$BEHIND" -gt 0 ]; then
    echo "==> merging $REF ($BEHIND commit(s) behind)"
    if ! git -C "$REPO_DIR" merge --no-edit "$REF"; then
      echo "==> merge conflict — resolve it, then re-run with --no-pull" >&2
      exit 1
    fi
  else
    echo "==> already up to date with $REF"
  fi
fi

CHECKOUT_SHA="$(git -C "$REPO_DIR" rev-parse HEAD)"

if [ "$CHECK_ONLY" -eq 1 ]; then
  # Exits non-zero when stale so this is usable as a health check / cron guard.
  if report "$CHECKOUT_SHA"; then
    echo "==> stack is current"
    exit 0
  fi
  echo "==> stack is STALE — run without --check to fix"
  exit 1
fi

# ── build + swap (rebuild.sh owns this) ──────────────────────────────────────

echo "==> building at ${CHECKOUT_SHA:0:7}"
PERSONA_GIT_SHA="$CHECKOUT_SHA" "$SCRIPT_DIR/rebuild.sh" "${SERVICES[@]}"

# ── prove it ─────────────────────────────────────────────────────────────────

echo
if report "$CHECKOUT_SHA"; then
  echo "==> stack is current at ${CHECKOUT_SHA:0:7}"
else
  echo "==> FAILED: a container is not running ${CHECKOUT_SHA:0:7} after rebuild." >&2
  echo "    A fresh container running stale code usually means the build used a" >&2
  echo "    cached layer from before the stamp. Re-run with --no-cache:" >&2
  echo "      docker compose -f docker-compose.yml -f docker-compose.worker.yml build --no-cache" >&2
  exit 1
fi
