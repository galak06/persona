"""Profile-centric config builder.

Reads engine profiles at social-automation/profiles/*.json and emits derived
artifacts the runtime reads.

Slice A: data/rate_limits.json (consumed by lib/rate_limiter.py).
Slice B: data/schedule.json   (aggregated cross-profile flow DAG).

Usage:
    python -m tools.profiles_build                # write/update BOTH artifacts
    python -m tools.profiles_build --check        # verify both + DAG; exit 1 on drift
    python -m tools.profiles_build --validate-dag # DAG-only validation (fast pre-commit)

Profile asymmetry (intentional):
    `_*.json` profiles (like `_engine.json`) are SKIPPED by `build_rate_limits`
    because rate limits are platform-specific, but INCLUDED by `build_flows`
    because cross-platform flows live there.

Flat-key naming:
    facebook:  comment, like, group_visit, group_post, page_post, group_join
    instagram: comment, like, follow, feed_post
    wordpress: reply

Legacy "ig_*" prefix dropped. Weekly limits preserved in profile JSON only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

Profile = dict[str, Any]
Artifact = dict[str, Any]
Flow = dict[str, Any]

# Fixed strings (not timestamps) so --check stays idempotent across runs.
_GENERATED_BY: str = "tools.profiles_build (slice A: rate_limits)"
_GENERATED_BY_SCHEDULE: str = "tools.profiles_build (slice B: schedule)"

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


_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PROFILE_DIR = _ROOT / "profiles"
_DEFAULT_RATE_LIMITS_PATH = _ROOT / "data" / "rate_limits.json"
_DEFAULT_SCHEDULE_PATH = _ROOT / "data" / "schedule.json"


def load_profiles(profile_dir: Path) -> dict[str, Profile]:
    """Read all *.json profiles. `_*.json` files keyed with leading underscore."""
    profiles: dict[str, Profile] = {}
    for path in sorted(profile_dir.glob("*.json")):
        with path.open() as fh:
            data: Profile = json.load(fh)
        platform = data.get("platform")
        if not isinstance(platform, str) or not platform:
            raise ValueError(f"Profile {path} missing string 'platform' field")
        key = f"_{platform}" if path.name.startswith("_") else platform
        if key in profiles:
            raise ValueError(f"Duplicate platform '{key}' in {path}")
        profiles[key] = data
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
    """Flatten numeric daily limits to `<platform>:<action>` -> int. Skips `_*` keys."""
    limits: dict[str, int] = {}
    for platform, profile in profiles.items():
        if platform.startswith("_"):
            continue
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
    """Collect `delay_between_<action>` strings to `<platform>:<action>` -> str. Skips `_*`."""
    delays: dict[str, str] = {}
    for platform, profile in profiles.items():
        if platform.startswith("_"):
            continue
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


def build_flows(profiles: dict[str, Profile]) -> list[Flow]:
    """Aggregate `flows[]` arrays across ALL profiles (incl. `_engine.json`).

    Returns a stably-order-sorted list of shallow-copied flow dicts. Missing
    `order` is treated as 9999 (sinks last).
    """
    all_flows: list[Flow] = []
    for platform_key, profile in profiles.items():
        flows = profile.get("flows", [])
        if not isinstance(flows, list):
            raise ValueError(f"flows for {platform_key} must be a list")
        for flow in flows:
            if not isinstance(flow, dict):
                raise ValueError(f"flow entry in {platform_key} must be a dict")
            all_flows.append(dict(flow))
    all_flows.sort(key=lambda f: f.get("order", 9999))
    return all_flows


def validate_dag(flows: list[Flow]) -> tuple[bool, str]:
    """Validate `flows` is a DAG: no dup ids, no missing deps, no cycles.

    Returns (True, "") on success; (False, "<reason>") otherwise.
    """
    ids = [f["id"] for f in flows]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        return False, f"Duplicate flow id(s): {dupes}"

    id_set = set(ids)
    for flow in flows:
        for dep in flow.get("depends_on", []):
            if dep not in id_set:
                return False, f"Flow '{flow['id']}' depends_on missing flow '{dep}'"

    in_degree: dict[str, int] = {fid: 0 for fid in ids}
    graph: dict[str, list[str]] = defaultdict(list)
    for flow in flows:
        for dep in flow.get("depends_on", []):
            graph[dep].append(flow["id"])
            in_degree[flow["id"]] += 1

    queue: deque[str] = deque([fid for fid in ids if in_degree[fid] == 0])
    visited = 0
    while queue:
        cur = queue.popleft()
        visited += 1
        for neighbor in graph[cur]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(ids):
        unvisited = sorted([fid for fid, deg in in_degree.items() if deg > 0])
        return False, f"Cycle detected involving: {unvisited}"

    return True, ""


def compose_rate_limits_artifact(profiles: dict[str, Profile]) -> Artifact:
    """Slice A artifact. Skips `_*` profiles."""
    return {
        "_generated": _GENERATED_BY,
        "limits": build_rate_limits(profiles),
        "delays": build_delay_ranges(profiles),
    }


def compose_schedule_artifact(profiles: dict[str, Profile]) -> Artifact:
    """Slice B artifact. INCLUDES `_engine.json` for cross-platform flows."""
    return {
        "_generated": _GENERATED_BY_SCHEDULE,
        "tasks": build_flows(profiles),
    }


# Back-compat alias for any external callers of the slice A name.
compose_artifact = compose_rate_limits_artifact


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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build profile-derived runtime artifacts.")
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero if any artifact would differ from current.")
    parser.add_argument("--validate-dag", action="store_true",
                        help="Only validate the flow DAG; don't read/write artifacts.")
    parser.add_argument("--profile-dir", type=Path, default=_DEFAULT_PROFILE_DIR)
    parser.add_argument("--rate-limits-out", type=Path, default=_DEFAULT_RATE_LIMITS_PATH)
    parser.add_argument("--schedule-out", type=Path, default=_DEFAULT_SCHEDULE_PATH)
    return parser.parse_args(argv)


def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    profiles = load_profiles(args.profile_dir)
    rl_artifact = compose_rate_limits_artifact(profiles)
    sched_artifact = compose_schedule_artifact(profiles)

    dag_ok, reason = validate_dag(sched_artifact["tasks"])

    if args.validate_dag:
        if not dag_ok:
            sys.stderr.write(f"profiles_build --validate-dag: {reason}\n")
            return 1
        sys.stdout.write(
            f"profiles_build --validate-dag: OK ({len(sched_artifact['tasks'])} flow(s))\n"
        )
        return 0

    if not dag_ok:
        verb = "--check" if args.check else "profiles_build"
        sys.stderr.write(f"{verb}: DAG invalid -- {reason}\n")
        return 1

    if args.check:
        rl_ok = check_artifact(args.rate_limits_out, rl_artifact)
        sched_ok = check_artifact(args.schedule_out, sched_artifact)
        if rl_ok and sched_ok:
            return 0
        if not rl_ok:
            sys.stderr.write(f"profiles_build --check: {args.rate_limits_out} is out of date.\n")
        if not sched_ok:
            sys.stderr.write(f"profiles_build --check: {args.schedule_out} is out of date.\n")
        return 1

    write_artifact(args.rate_limits_out, rl_artifact)
    sys.stdout.write(f"profiles_build: wrote {args.rate_limits_out}\n")
    write_artifact(args.schedule_out, sched_artifact)
    sys.stdout.write(f"profiles_build: wrote {args.schedule_out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
