"""Redis-backed atomic rate limiter — process-safe replacement for rate_limiter.py.

Uses Redis INCR + EXPIRE so concurrent workers share the same daily counter
without file-lock races.  The daily window resets at midnight UTC.

Drop-in replacement for the public surface of rate_limiter.py:
    can_act()        → check without incrementing
    record_action()  → increment; raises RuntimeError if limit exceeded
    wait_random_delay() → unchanged (pure sleep, no state)
    get_daily_status()  → returns same dict shape as before
    print_status()      → pretty-prints to stdout

Environment variables:
    REDIS_URL  — Redis connection URL (default: redis://localhost:6379/0)
"""

from __future__ import annotations

import random
import time
from datetime import date

from lib.rate_limiter import DAILY_LIMITS, DELAY_RANGES, ActionType, Platform
from lib.task_queue import _get_client

_NAMESPACE = "dogfood:rate"


def _key(platform: Platform, action: ActionType, today: str) -> str:
    return f"{_NAMESPACE}:{platform}:{action}:{today}"


def _today() -> str:
    return date.today().isoformat()


def _seconds_until_midnight() -> int:
    import datetime
    now = datetime.datetime.utcnow()
    midnight = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, int((midnight - now).total_seconds()))


def can_act(platform: Platform, action: ActionType) -> bool:
    """Returns True if the daily limit has not been reached."""
    limit_key = f"{platform}:{action}"
    limit = DAILY_LIMITS.get(limit_key)
    if limit is None:
        raise ValueError(f"Unknown action key: {limit_key}")
    r = _get_client()
    current = int(r.get(_key(platform, action, _today())) or 0)
    return current < limit


def record_action(platform: Platform, action: ActionType) -> int:
    """Atomically increment the counter. Raises RuntimeError if limit exceeded."""
    limit_key = f"{platform}:{action}"
    limit = DAILY_LIMITS.get(limit_key)
    if limit is None:
        raise ValueError(f"Unknown action key: {limit_key}")

    r = _get_client()
    rkey = _key(platform, action, _today())

    # Lua script: atomic check-and-increment
    lua = """
local current = tonumber(redis.call('GET', KEYS[1])) or 0
if current >= tonumber(ARGV[1]) then
    return -1
end
local new = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return new
"""
    result = r.eval(lua, 1, rkey, limit, _seconds_until_midnight())
    if result == -1:
        raise RuntimeError(
            f"Daily limit reached for {limit_key}: {limit}/{limit}. Aborting."
        )
    return int(result)


def wait_random_delay(platform: Platform, action: ActionType) -> None:
    """Sleeps a randomized delay appropriate for the platform + action."""
    key = f"{platform}:{action}"
    lo, hi = DELAY_RANGES.get(key, (10, 30))
    delay = random.uniform(lo, hi)
    print(f"[rate_limiter] Waiting {delay:.1f}s before {key}...")
    time.sleep(delay)


def get_daily_status() -> dict[str, dict[str, int]]:
    """Returns today's action counts vs limits for all tracked keys."""
    r = _get_client()
    today = _today()
    result: dict[str, dict[str, int]] = {}
    for limit_key, limit in DAILY_LIMITS.items():
        platform, action = limit_key.split(":", 1)
        rkey = _key(platform, action, today)  # type: ignore[arg-type]
        used = int(r.get(rkey) or 0)
        result[limit_key] = {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }
    return result


def print_status() -> None:
    status = get_daily_status()
    print(f"\n=== Rate Limit Status (Redis) ({_today()}) ===")
    for key, s in status.items():
        bar = "█" * s["used"] + "░" * s["remaining"]
        print(f"  {key:<30} {s['used']}/{s['limit']}  [{bar}]")
    print()
