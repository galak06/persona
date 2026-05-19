"""
Rate limiter for DogFoodAndFun social media automation.
Enforces daily hard limits per platform + action type.
Persists state to .claude/state/rate_limit_tracker.json
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import date
from pathlib import Path
from typing import Literal

from lib.config import settings

Platform = Literal["facebook", "instagram", "wordpress"]
ActionType = Literal[
    "comment",
    "like",
    "group_visit",
    "group_post",
    "ig_comment",
    "own_reply",
]

# wordpress:comment counts only *outbound* replies we post to moderated
# visitor comments on dogfoodandfun.com. Approving/trashing a visitor comment
# (site-owner moderation) is not rate-limited — those are our own admin
# actions on our own site, not engagement toward a third party.
DAILY_LIMITS: dict[str, int] = {
    "facebook:comment": 5,
    "facebook:group_visit": 6,  # reduced from 10 — spread through day
    "facebook:group_post": 10,  # share blog link to group — bumped from 3 (still well under FB's ~25/d ceiling)
    "instagram:like": 8,  # increased from 5 — likes are low-risk
    "instagram:ig_comment": 7,
    # Replies to comments on OUR OWN IG media. Separate bucket from ig_comment
    # (which guards outbound comments on third-party posts — the real spam
    # risk). Conversation on your own post is expected engagement; we cap at
    # 15/day to stay well under IG's per-user API ceilings while letting every
    # recipe post have a real thread.
    "instagram:own_reply": 15,
    "wordpress:comment": 20,
}

DELAY_RANGES: dict[str, tuple[int, int]] = {
    "facebook:comment": (30, 120),
    "facebook:group_visit": (45, 180),
    "facebook:group_post": (60, 180),
    "instagram:like": (10, 45),
    "instagram:ig_comment": (120, 180),
    "instagram:own_reply": (30, 90),
    "wordpress:comment": (15, 45),
}

# Resolve against the actual project root regardless of cwd or caller location
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# STATE_FILE now resolves to the brand-dir-aware state path
# (e.g. dogfoodandfun/state/rate_limit_tracker.json), via the BrandPaths
# resolver in lib.config. The legacy social-automation/.claude/state path
# never existed under the multi-brand layout and caused FileNotFoundError
# on every _save_state() call.
STATE_FILE = settings.paths.rate_limit_tracker


def _load_state() -> dict:
    # Tolerate empty/corrupt files: a torn read (caught mid-rewrite by an older
    # non-atomic writer, or an interrupted launchd run) must not crash a script
    # that has already posted to the API. Worst case we lose today's counts —
    # better than aborting after a successful reply.
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    # Atomic write: serialize fully into a temp file in the same directory, then
    # os.replace() onto the target. POSIX guarantees rename is atomic, so a
    # concurrent reader (e.g. the comment-approver launchd job overlapping with
    # ig-own-comments' Telegram-approval wait) sees either the old contents or
    # the new contents — never a half-written or empty file.
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + f".tmp.{os.getpid()}")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


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
