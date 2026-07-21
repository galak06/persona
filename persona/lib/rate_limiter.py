"""
Rate limiter for Persona social media automation.
Enforces daily hard limits per platform + action type.
Persists state to .claude/state/rate_limit_tracker.json

DAILY_LIMITS and DELAY_RANGES are loaded at module-import time from the
generated artifact at ``data/rate_limits.json``. That artifact is built by
``python -m tools.profiles_build`` from the profile JSONs in ``profiles/``
plus any brand overlay (``<brand_dir>/brand.json``).

If the artifact is missing, importing this module raises RuntimeError —
that's intentional. The deploy/CI flow must run the build first.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import date
from pathlib import Path
from typing import Literal

from lib.config import settings

Platform = Literal["facebook", "instagram", "wordpress"]
# Action names match the flat keys emitted by tools.profiles_build.
# ``own_reply`` ships via the brand-overlay rate_limits in <brand_dir>/brand.json
# (mapped from ``own_replies_per_day`` + ``delay_between_own_replies``).
ActionType = Literal[
    "comment",
    "like",
    "group_visit",
    "group_post",
    "group_join",
    "page_post",
    "feed_post",
    "follow",
    "reply",
    "own_reply",
]

# Resolve against the actual project root regardless of cwd or caller location
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RATE_LIMITS_PATH = _PROJECT_ROOT / "data" / "rate_limits.json"

# Matches the leading "NN-MMs" in delay strings like "30-120s random". The
# trailing " random" qualifier is informational only and ignored at runtime.
_DELAY_PATTERN: re.Pattern[str] = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*s", re.IGNORECASE)


def _parse_delay(spec: str) -> tuple[int, int]:
    """Parse a delay spec like "30-120s random" into (lo, hi) seconds."""
    match = _DELAY_PATTERN.match(spec)
    if match is None:
        raise ValueError(f"Unrecognized delay format: {spec!r} (expected '<lo>-<hi>s ...')")
    lo, hi = int(match.group(1)), int(match.group(2))
    if lo > hi:
        raise ValueError(f"Delay range out of order: {spec!r} ({lo} > {hi})")
    return lo, hi


def _load_artifact() -> tuple[dict[str, int], dict[str, tuple[int, int]]]:
    """Load the generated rate_limits artifact.

    Returns:
        (limits, delays) — limits map ``<platform>:<action>`` -> int daily cap;
        delays map ``<platform>:<action>`` -> (lo_seconds, hi_seconds) tuple.
    """
    if not _RATE_LIMITS_PATH.exists():
        raise RuntimeError(
            f"Missing {_RATE_LIMITS_PATH} — run 'python -m tools.profiles_build' to generate it."
        )
    with _RATE_LIMITS_PATH.open() as fh:
        data = json.load(fh)

    raw_limits = data.get("limits", {})
    raw_delays = data.get("delays", {})
    if not isinstance(raw_limits, dict) or not isinstance(raw_delays, dict):
        raise RuntimeError(f"Malformed artifact at {_RATE_LIMITS_PATH}: limits/delays must be objects")

    limits: dict[str, int] = dict(raw_limits)
    delays: dict[str, tuple[int, int]] = {key: _parse_delay(spec) for key, spec in raw_delays.items()}
    return limits, delays


DAILY_LIMITS, DELAY_RANGES = _load_artifact()

# STATE_FILE now resolves to the brand-dir-aware state path
# (e.g. persona/state/rate_limit_tracker.json), via the BrandPaths
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


def daily_limit(platform: Platform, action: ActionType) -> int:
    """The daily cap this module actually enforces for `platform:action`.

    Callers that display a quota must read it from here rather than from
    `EngagementPolicy`, which is built from `config.json` and can drift from
    the generated `data/rate_limits.json` artifact enforced by `can_act`.
    """
    key = _action_key(platform, action)
    limit = DAILY_LIMITS.get(key)
    if limit is None:
        raise ValueError(f"Unknown action key: {key}")
    return limit


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
