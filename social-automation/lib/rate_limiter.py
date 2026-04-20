"""
Rate limiter for DogFoodAndFun social media automation.
Enforces daily hard limits per platform + action type.
Persists state to .claude/state/rate_limit_tracker.json
"""

from __future__ import annotations

import json
import random
import time
from datetime import date
from pathlib import Path
from typing import Literal

Platform = Literal["facebook", "instagram"]
ActionType = Literal["comment", "like", "group_visit", "group_post", "ig_comment"]

DAILY_LIMITS: dict[str, int] = {
    "facebook:comment": 5,
    "facebook:group_visit": 6,  # reduced from 10 — spread through day
    "facebook:group_post": 3,   # share blog link to group — hard spam cap
    "instagram:like": 8,  # increased from 5 — likes are low-risk
    "instagram:ig_comment": 2,
}

DELAY_RANGES: dict[str, tuple[int, int]] = {
    "facebook:comment": (30, 120),
    "facebook:group_visit": (45, 180),
    "facebook:group_post": (60, 180),
    "instagram:like": (10, 45),
    "instagram:ig_comment": (120, 180),
}

# Resolve against the actual project root regardless of cwd or caller location
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = _PROJECT_ROOT / ".claude" / "state" / "rate_limit_tracker.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open() as f:
            return json.load(f)
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def _today_key() -> str:
    return date.today().isoformat()


def _action_key(platform: Platform, action: ActionType) -> str:
    return f"{platform}:{action}"


def can_act(platform: Platform, action: ActionType) -> bool:
    """Returns True if the daily limit has not been reached."""
    key = _action_key(platform, action)
    limit = DAILY_LIMITS.get(key)
    if limit is None:
        raise ValueError(f"Unknown action key: {key}")

    state = _load_state()
    today = _today_key()
    count = state.get(today, {}).get(key, 0)
    return count < limit


def record_action(platform: Platform, action: ActionType) -> int:
    """
    Records that an action was taken. Returns the new daily count.
    Raises RuntimeError if the daily limit is already exceeded.
    """
    key = _action_key(platform, action)
    limit = DAILY_LIMITS.get(key)
    if limit is None:
        raise ValueError(f"Unknown action key: {key}")

    state = _load_state()
    today = _today_key()
    if today not in state:
        state[today] = {}

    current = state[today].get(key, 0)
    if current >= limit:
        raise RuntimeError(f"Daily limit reached for {key}: {current}/{limit}. Aborting.")

    state[today][key] = current + 1
    _save_state(state)
    return state[today][key]


def wait_random_delay(platform: Platform, action: ActionType) -> None:
    """Sleeps a randomized delay appropriate for the platform + action."""
    key = _action_key(platform, action)
    lo, hi = DELAY_RANGES.get(key, (10, 30))
    delay = random.uniform(lo, hi)
    print(f"[rate_limiter] Waiting {delay:.1f}s before {key}...")
    time.sleep(delay)


def get_daily_status() -> dict[str, dict[str, int]]:
    """Returns today's action counts vs limits for all tracked keys."""
    state = _load_state()
    today = _today_key()
    today_counts = state.get(today, {})
    return {
        key: {
            "used": today_counts.get(key, 0),
            "limit": limit,
            "remaining": limit - today_counts.get(key, 0),
        }
        for key, limit in DAILY_LIMITS.items()
    }


def print_status() -> None:
    """Pretty-prints today's rate limit status."""
    status = get_daily_status()
    print(f"\n=== Rate Limit Status ({_today_key()}) ===")
    for key, s in status.items():
        bar = "█" * s["used"] + "░" * s["remaining"]
        print(f"  {key:<30} {s['used']}/{s['limit']}  [{bar}]")
    print()


if __name__ == "__main__":
    print_status()
