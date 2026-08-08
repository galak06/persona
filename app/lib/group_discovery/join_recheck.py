"""Reconciliation logic for FB groups stuck in `status=join_requested`.

Split out of `scripts/fb_pending_joins_check.py` (file-size rule) — this
module owns the per-group classify/staleness/rejoin decisions; the script
owns CLI, the visit loop, and bookkeeping.

## Staleness anchor: `PENDING_SINCE_KEY`, not `last_checked_at`

There is no `join_requested_at` column on `fb_groups`. The two columns that
exist for this purpose in principle — `created_at`/`updated_at` — are
declared in `db/schema.sql` but are dead in practice: no INSERT or UPDATE
anywhere in the codebase (`GroupsRepository.upsert_group`,
`GroupsRepository._update_column`) ever writes them, so they are always
NULL, and `GroupsRepository._row_to_dict` doesn't even expose them (they're
excluded from `GROUP_COLUMNS`, the only keys it walks) — not "queryable"
through the shared dict interface every script, including this one, uses.

`last_checked_at` IS populated and exposed, but this script's own contract
(mirroring `scripts/fb_pending_posts_check.py`) bumps it on EVERY visit
regardless of outcome. That script gets away with the same unconditional
bump because it has a SEPARATE anchor field (`last_post_at`, set once by
the publisher, never touched by the checker) decoupled from its own
last_checked_at bookkeeping. Joins have no such external anchor, so reusing
last_checked_at for staleness math would reset the clock to ~0 every run —
staleness would never fire.

So: `PENDING_SINCE_KEY` (`"join_pending_since"`) is a new dict key, set ONCE
the first time this script observes a join_requested row with the key
absent, and left untouched afterward while the row stays pending. It is
deliberately NOT in `GROUP_COLUMNS` — unmodeled keys already round-trip
through the repository's `extra` JSONB column automatically (see
`lib/groups_db/models.py`'s own docstring), so this needed no schema or
repository change at all.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from lib.group_discovery.fb_search import try_join
from lib.group_discovery.status_classifier import classify_join_outcome
from lib.groups.notes import append_group_note
from lib.groups_db.models import GroupStatus
from lib.logger import log_step

if TYPE_CHECKING:
    from playwright.sync_api import Page

PENDING_SINCE_KEY = "join_pending_since"


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def pending_since(entry: dict[str, Any]) -> tuple[datetime, bool]:
    """Return (anchor_datetime, is_first_touch) — see module docstring."""
    raw = entry.get(PENDING_SINCE_KEY) or ""
    if not raw:
        return datetime.now(UTC), True
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")), False
    except ValueError:
        return datetime.now(UTC), True


def scrape_page_text(page: Page) -> str:
    time.sleep(4)
    try:
        text = page.evaluate("() => document.body.innerText") or ""
    except Exception as e:
        log_step(f"    scrape error: {e}")
        return ""
    return str(text)[:6000]


def process_entry(page: Page, entry: dict[str, Any], stale_days: int) -> str:
    """Navigate, scrape, classify. Mutates `entry` in place. Returns one of:
    joined / rejected / stale_pending / still_pending / unknown /
    classify_failed."""
    page.goto(entry["group_url"], wait_until="domcontentloaded", timeout=30000)
    page_text = scrape_page_text(page)

    now = now_iso()
    result = classify_join_outcome(page_text)

    if result is None:
        entry["last_checked_at"] = now
        append_group_note(entry, "Join-outcome classification unavailable this check.")
        return "classify_failed"

    status = result["status"]
    reason = result.get("reason", "")
    entry["last_checked_at"] = now

    if status == "joined":
        entry["status"] = GroupStatus.JOINED
        entry["joined_at"] = now
        append_group_note(entry, f"Join request confirmed accepted — {reason}")
        return "joined"

    if status == "rejected":
        entry["status"] = GroupStatus.REJECTED
        append_group_note(entry, f"Join request appears declined — {reason}")
        return "rejected"

    anchor, first_touch = pending_since(entry)
    if first_touch:
        entry[PENDING_SINCE_KEY] = now
    age_days = (datetime.now(UTC) - anchor).days

    if status == "still_pending":
        if not first_touch and age_days >= stale_days:
            entry["status"] = GroupStatus.STALE_PENDING
            append_group_note(
                entry, f"Still pending after {age_days}d — likely declined by admins."
            )
            return "stale_pending"
        return "still_pending"

    # status == "unknown" — successful classification, model unsure
    log_step(f"    ambiguous classification — {reason}")
    return "unknown"


def rejoin_entry(page: Page, entry: dict[str, Any]) -> str:
    """Manual --force-rejoin only. Mutates `entry` in place. Returns
    clicked / already_joined / already_pending / not_found / error."""
    try:
        result = try_join(page, entry["group_url"])
    except Exception as e:
        log_step(f"    rejoin error: {e}")
        append_group_note(entry, f"Manual --force-rejoin attempt failed: {e}")
        return "error"

    now = now_iso()
    if result.startswith("clicked"):
        entry["status"] = GroupStatus.JOIN_REQUESTED
        entry[PENDING_SINCE_KEY] = now  # restart the staleness clock
        entry["last_checked_at"] = now
        append_group_note(entry, f"Manual --force-rejoin: fresh join request sent ({result}).")
        return "clicked"
    if result == "already_joined":
        entry["status"] = GroupStatus.JOINED
        entry["joined_at"] = now
        append_group_note(entry, "Manual --force-rejoin: already a member.")
        return "already_joined"
    if result == "already_pending":
        entry["status"] = GroupStatus.JOIN_REQUESTED
        entry.setdefault(PENDING_SINCE_KEY, now)
        entry["last_checked_at"] = now
        append_group_note(entry, "Manual --force-rejoin: request already pending.")
        return "already_pending"
    append_group_note(entry, "Manual --force-rejoin: join button not found — check manually.")
    return "not_found"
