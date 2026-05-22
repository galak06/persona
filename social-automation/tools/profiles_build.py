"""Profile-centric config builder.

Reads engine profiles at social-automation/profiles/*.json and emits derived
artifacts the runtime reads. Slice A: emits data/rate_limits.json consumed
by lib/rate_limiter.py.

Usage:
    python -m tools.profiles_build           # write/update artifacts
    python -m tools.profiles_build --check   # exit non-zero if artifacts are out-of-date

Flat-key naming:
    facebook:  comment, like, group_visit, group_post, page_post, group_join
    instagram: comment, like, follow, feed_post
    wordpress: reply

Legacy "ig_*" prefix dropped (instagram:ig_comment -> instagram:comment).
Weekly limits (group_join_requests_per_week) are NOT in the flat dict in
slice A -- preserved in the profile JSON for a future slice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Profile values are heterogeneous (strings, ints, nested dicts) so Any is
# the honest type here.
Profile = dict[str, Any]
Artifact = dict[str, Any]

# Identifies who wrote this artifact. NOT a timestamp -- wall-clock would
# break --check idempotency (every run would diverge).
_GENERATED_BY: str = "tools.profiles_build (slice A: rate_limits)"

# Map a profile rate-limit field name to a flat <platform>:<action> suffix.
# None means "skip" (handled elsewhere or not relevant to slice A).
_DAILY_FIELD_TO_ACTION: dict[str, str | None] = {
    "comments_per_day": "comment",
    "likes_per_day": "like",
    "group_visits_per_day": "group_visit",
    "group_posts_per_day": "group_post",
    "page_posts_per_day": "page_post",
    "feed_posts_per_day": "feed_post",
    "follows_per_day": "follow",
    "group_join_requests_per_day": "group_join",
    "replies_per_day": "reply",
    # Weekly cadence -- preserved in profile JSON, not the daily flat dict.
    "group_join_requests_per_week": None,
}

# Explicit map (rather than naively trimming "s") because English is irregular:
# "replies" -> "reply" can't be derived by stripping a trailing letter.
_DELAY_FIELD_TO_ACTION: dict[str, str] = {
    "delay_between_comments": "comment",
    "delay_between_likes": "like",
    "delay_between_group_visits": "group_visit",
    "delay_between_follows": "follow",
    "delay_between_replies": "reply",
}


def _default_profile_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "profiles"


def _default_artifact_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "rate_limits.json"


def load_profiles(profile_dir: Path) -> dict[str, Profile]:
    """Read all *.json profiles in `profile_dir`, keyed by their `platform`."""
    profiles: dict[str, Profile] = {}
    for path in sorted(profile_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        with path.open() as fh:
            data: Profile = json.load(fh)
        platform = data.get("platform")
        if not isinstance(platform, str) or not platform:
            raise ValueError(f"Profile {path} missing string 'platform' field")
        if platform in profiles:
            raise ValueError(f"Duplicate platform '{platform}' in {path}")
        profiles[platform] = data
    return profiles


def _action_key(platform: str, profile_key: str) -> str | None:
    """Translate a profile field to a flat `<platform>:<action>` key, or None."""
    if profile_key.startswith("delay_between_"):
        return None
    action = _DAILY_FIELD_TO_ACTION.get(profile_key)
    if action is None:
        return None
    return f"{platform}:{action}"


def build_rate_limits(profiles: dict[str, Profile]) -> dict[str, int]:
    """Flatten numeric daily limits to `<platform>:<action>` -> int."""
    limits: dict[str, int] = {}
    for platform, profile in profiles.items():
        rate_limits = profile.get("rate_limits", {})
        if not isinstance(rate_limits, dict):
            raise ValueError(f"rate_limits for {platform} must be an object")
        for field, value in rate_limits.items():
            key = _action_key(platform, field)
            if key is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{platform}.{field} must be an int, got {type(value).__name__}")
            limits[key] = value
    return limits


def build_delay_ranges(profiles: dict[str, Profile]) -> dict[str, str]:
    """Collect `delay_between_<action>` strings to `<platform>:<action>` -> str."""
    delays: dict[str, str] = {}
    for platform, profile in profiles.items():
        rate_limits = profile.get("rate_limits", {})
        if not isinstance(rate_limits, dict):
            continue
        for field, value in rate_limits.items():
            if not field.startswith("delay_between_"):
                continue
            action = _DELAY_FIELD_TO_ACTION.get(field)
            if action is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{platform}.{field} must be a string, got {type(value).__name__}")
            delays[f"{platform}:{action}"] = value
    return delays


def compose_artifact(profiles: dict[str, Profile]) -> Artifact:
    """Return the full artifact dict that gets written to disk."""
    return {
        "_generated": _GENERATED_BY,
        "limits": build_rate_limits(profiles),
        "delays": build_delay_ranges(profiles),
    }


def write_artifact(path: Path, data: Artifact) -> None:
    """Atomically write JSON (sorted, 2-space indent, trailing newline)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(payload)
    os.replace(tmp, path)


def check_artifact(path: Path, expected: Artifact) -> bool:
    """Return True iff `path` exists and parses to a dict equal to `expected`."""
    if not path.exists():
        return False
    try:
        with path.open() as fh:
            current: Artifact = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    return bool(current == expected)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build profile-derived runtime artifacts.")
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero if any artifact would differ from current.")
    parser.add_argument("--profile-dir", type=Path, default=_default_profile_dir(),
                        help="Directory containing platform profile JSON files.")
    parser.add_argument("--rate-limits-out", type=Path, default=_default_artifact_path(),
                        help="Output path for the rate_limits.json artifact.")
    args = parser.parse_args(argv)

    profiles = load_profiles(args.profile_dir)
    expected = compose_artifact(profiles)

    if args.check:
        if check_artifact(args.rate_limits_out, expected):
            return 0
        sys.stderr.write(
            f"profiles_build --check: {args.rate_limits_out} is out of date. "
            "Run `python -m tools.profiles_build` to regenerate.\n"
        )
        return 1

    write_artifact(args.rate_limits_out, expected)
    sys.stdout.write(f"profiles_build: wrote {args.rate_limits_out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
