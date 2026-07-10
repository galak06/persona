"""
Rolling 30-day history of comments we've drafted/posted.

Two questions this answers:
  1. Was this exact text used recently? (prevents template-recycling spam — the
     bug that posted "Balancing calcium..." 13× across groups in April-May 2026.)
  2. Has this post already been commented on? (wraps deduplication.is_duplicate
     with the same TTL semantics for caller convenience.)

Backed by .claude/state/recent_drafts.jsonl (one event per line, append-only).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from deduplication import is_duplicate

Platform = Literal["facebook", "instagram", "wordpress"]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = _PROJECT_ROOT / ".claude" / "state" / "recent_drafts.jsonl"
TTL_DAYS = 30


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation/emoji noise.
    Two drafts that differ only in casing or spacing collide on the same hash.
    """
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Hash prefix length. Engagement log truncates content to 200 chars, so we
# match on the first 80 normalized characters — long enough to be distinctive,
# short enough that templates collide with their (possibly truncated) log copy.
PREFIX_LEN = 80


def _hash(text: str) -> str:
    prefix = _normalize(text)[:PREFIX_LEN]
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]


def _load_recent_hashes() -> set[str]:
    """Return hashes of drafts within TTL window. Lazily prunes the file."""
    if not HISTORY_FILE.exists():
        return set()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)).isoformat()
    keep_lines: list[str] = []
    hashes: set[str] = set()
    with HISTORY_FILE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ts", "") >= cutoff:
                keep_lines.append(line)
                h = row.get("hash")
                if h:
                    hashes.add(h)
    # Rewrite file dropping expired entries (best-effort)
    if keep_lines:
        try:
            HISTORY_FILE.write_text("\n".join(keep_lines) + "\n")
        except OSError:
            pass
    return hashes


def was_text_recently_used(text: str) -> bool:
    """True if a draft with the same normalized text was recorded in last 30 days."""
    if not text:
        return False
    return _hash(text) in _load_recent_hashes()


def record_draft(text: str, *, platform: Platform = "facebook",
                 post_id: str = "", target: str = "") -> None:
    """Append a draft event so future drafts can detect repeats."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hash": _hash(text),
        "platform": platform,
        "post_id": post_id,
        "target": target,
        "text_preview": (text or "")[:80],
    }
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(event) + "\n")


def was_post_commented(platform: Platform, post_id: str) -> bool:
    """Has the bot already engaged with this post on this platform?

    Wraps deduplication.is_duplicate so callers don't need two imports.
    """
    if not post_id:
        return False
    return is_duplicate(platform, post_id)


def filter_unused(texts: list[str]) -> list[str]:
    """From a list of candidate template strings, drop any used in last 30 days."""
    used = _load_recent_hashes()
    return [t for t in texts if _hash(t) not in used]


def cli_stats() -> None:
    """Print quick summary — useful for ops checks."""
    hashes = _load_recent_hashes()
    print(f"recent_drafts.jsonl: {len(hashes)} unique texts in last {TTL_DAYS} days")
    if HISTORY_FILE.exists():
        # Per-day counts
        from collections import Counter
        counts: Counter[str] = Counter()
        with HISTORY_FILE.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                day = row.get("ts", "")[:10]
                counts[day] += 1
        for day, n in sorted(counts.items())[-14:]:
            print(f"  {day}  {n}")


if __name__ == "__main__":
    cli_stats()
