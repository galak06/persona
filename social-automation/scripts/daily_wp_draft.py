"""
Daily WP Draft Nudge — picks the highest-scored approved brief from the
enrichment cache and sends a Telegram nudge so the user runs wp-post-creator.

Runs once per day via launchd. The script does NOT call the Anthropic API or
draft the post itself — drafting still happens through wp-post-creator inside
Claude Code so the human stays in the loop. This script is the cadence trigger
plus a "next idea is ready" reminder.

Behavior:
    - If approved briefs exist: nudge with the top-scored topic.
    - If none exist: nudge to run content-enricher.
    - Idempotent: writes a daily marker so reruns within 24h are no-ops.

Usage:
    python scripts/daily_wp_draft.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

UTC = UTC

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from logger import enable_unbuffered, log_step

enable_unbuffered()

from notifier import send as send_telegram

ENRICHMENT_CACHE = PROJECT_ROOT / ".claude/state/enrichment_cache.json"
DAILY_MARKER = PROJECT_ROOT / ".claude/state/daily_wp_draft_marker.json"


def load_briefs() -> list[dict]:
    if not ENRICHMENT_CACHE.exists():
        return []
    data = json.loads(ENRICHMENT_CACHE.read_text())
    if isinstance(data, dict):
        return list(data.get("briefs", []))
    if isinstance(data, list):
        return data
    return []


def already_ran_today() -> bool:
    if not DAILY_MARKER.exists():
        return False
    try:
        marker = json.loads(DAILY_MARKER.read_text())
        last = datetime.fromisoformat(marker["last_run"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return last.date() == datetime.now(UTC).date()


def write_marker(topic: str | None) -> None:
    DAILY_MARKER.parent.mkdir(parents=True, exist_ok=True)
    DAILY_MARKER.write_text(
        json.dumps(
            {
                "last_run": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "nudged_topic": topic,
            },
            indent=2,
        )
    )


def pick_top_brief(briefs: list[dict]) -> dict | None:
    approved = [b for b in briefs if b.get("status") == "approved"]
    if not approved:
        return None
    return max(approved, key=lambda b: b.get("score", 0))


def build_nudge(brief: dict) -> str:
    topic = brief.get("title") or brief.get("topic") or "(untitled)"
    keyword = brief.get("target_keyword") or brief.get("keywords", [""])[0]
    score = brief.get("score", "?")
    nalla = brief.get("nalla_context", "")
    return (
        "📝 Daily WP Draft Ready\n\n"
        f"Topic: {topic}\n"
        f"Keyword: {keyword} | Score: {score}/12\n\n"
        f"Nalla angle: {nalla[:200]}{'…' if len(nalla) > 200 else ''}\n\n"
        "Run `wp-post-creator` in Claude Code to draft."
    )


def build_empty_nudge() -> str:
    return (
        "📝 Daily WP Draft — queue empty\n\n"
        "No approved briefs in enrichment_cache.json.\n"
        "Run `content-enricher` to prepare the next idea, "
        "or `content-ideator` if the sheet is also low."
    )


def main() -> int:
    log_step("daily_wp_draft", "start")

    if already_ran_today():
        log_step("daily_wp_draft", "skip_already_ran")
        return 0

    briefs = load_briefs()
    top = pick_top_brief(briefs)

    if top is None:
        send_telegram(build_empty_nudge())
        write_marker(None)
        log_step("daily_wp_draft", "nudged (queue empty)")
        return 0

    topic = top.get("title") or top.get("topic")
    send_telegram(build_nudge(top))
    write_marker(topic)
    log_step("daily_wp_draft", f"nudged: {topic}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
