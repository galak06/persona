"""FB notification scanner — find groups that approved a join request.

Opens facebook.com/notifications via the saved session, parses the notification
list for "approved your request to join", "welcome to", or "you joined" events,
and upserts each matching group into `data/groups_tracker.json`.

The tracker is the source of truth for `fb-group-publisher` — which groups are
we in, what are their rules, and when did we last post there.

Usage:
    python -m scripts.fb_notification_scan                # scan + update tracker
    python -m scripts.fb_notification_scan --dry-run      # show what would be added
    python -m scripts.fb_notification_scan --health-check # verify session + exit
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script

settings, log = init_script(__name__)

from lib.fb.session import FbSession, build_fb_session
from lib.logger import log_step
from lib.runtime.singleton import LockAcquisitionError, SingletonLock
from notifier import skill_error, skill_finished, skill_started

if TYPE_CHECKING:
    from playwright.sync_api import Page

SKILL_NAME = "fb-notification-scan"

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
from lib import groups_db  # FB groups live in groups.db (was groups_tracker.json)

_APPROVAL_KEYWORDS = (
    "approved your request to join",
    "approved your request",
    "welcome to",
    "you joined",
    "you are now a member",
    "accepted your request",
)


def _load_tracker() -> list[dict[str, Any]]:
    return groups_db.load_all()


def _save_tracker(data: list[dict[str, Any]]) -> None:
    groups_db.save_all(data)


def _scan_notifications(page: Page) -> list[dict[str, str]]:
    """Return a list of {group_name, group_url, notification_text} for approvals."""
    page.goto(
        "https://www.facebook.com/notifications",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    time.sleep(4)
    # Scroll to load more notifications
    for pct in (0.3, 0.6, 0.9):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
        time.sleep(1.5)

    raw = page.evaluate(
        """(keywords) => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        const rows = Array.from(document.querySelectorAll('[role="link"], a'))
            .filter(el => el.href && el.textContent);
        const out = [];
        const seen = new Set();
        for (const row of rows) {
            const text = norm(row.textContent).toLowerCase();
            if (!keywords.some(k => text.includes(k))) continue;
            // The link itself may point to the group. Extract href.
            let url = row.href || '';
            // Prefer /groups/ URLs if the row has one nested inside.
            const groupLink = row.querySelector('a[href*="/groups/"]') ||
                              (row.href && row.href.includes('/groups/') ? row : null);
            if (groupLink) url = groupLink.href || url;
            if (!url.includes('/groups/')) continue;
            // Pull the group name: last /groups/<slug>/ segment's link usually
            // has the group name as text. Fallback: parse notification text.
            const match = url.match(/\\/groups\\/([^\\/?#]+)/);
            const slug = match ? match[1] : '';
            const key = slug || url;
            if (seen.has(key)) continue;
            seen.add(key);
            // Try to extract group name from nearby bold/strong or the url slug
            const boldEl = row.querySelector('strong, b, [class*="x1heor9g"]');
            let name = boldEl ? norm(boldEl.textContent) : '';
            if (!name) name = slug.replace(/[-_]/g, ' ');
            out.push({
                group_name: name,
                group_url: 'https://www.facebook.com/groups/' + slug + '/',
                notification_text: norm(row.textContent).slice(0, 200),
            });
        }
        return out;
    }""",
        list(_APPROVAL_KEYWORDS),
    )
    result: list[dict[str, str]] = raw or []
    return result


def _upsert(tracker: list[dict[str, Any]], entry: dict[str, Any]) -> tuple[bool, str]:
    """Insert entry if new, or update status if already present. Returns (added, action)."""
    for existing in tracker:
        if existing.get("group_url") == entry["group_url"]:
            if existing.get("status") != "joined":
                existing["status"] = "joined"
                existing["joined_at"] = entry["joined_at"]
                return False, "updated"
            return False, "unchanged"
    tracker.append(entry)
    return True, "added"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan FB notifications for group approvals")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan only, don't write the tracker",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify FB session is authenticated and exit",
    )
    return parser.parse_args()


def _health_check(session: FbSession) -> int:
    if not session.is_authenticated():
        print(
            f"FB session not authenticated (storage: {session.storage_path}) — "
            "run fb_login.py",
            file=sys.stderr,
        )
        return 1
    print(f"FB session OK (storage: {session.storage_path})", file=sys.stderr)
    return 0


def main(session: FbSession, *, dry_run: bool = False) -> int:
    skill_started(SKILL_NAME, "scanning FB for group approvals")

    if not session.is_authenticated():
        skill_error(SKILL_NAME, "FB session not found — run fb_login.py")
        return 1

    tracker = _load_tracker()

    with session.page() as page:
        approvals = _scan_notifications(page)

    log_step(f"found {len(approvals)} approval notifications")
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    added = updated = 0
    for a in approvals:
        entry: dict[str, Any] = {
            "group_name": a["group_name"],
            "group_url": a["group_url"],
            "status": "joined",
            "joined_at": now,
            "rules": "unknown",
            "last_post_at": None,
            "source_notification": a["notification_text"],
        }
        _, action = _upsert(tracker, entry)
        if action == "added":
            added += 1
            print(f"  + {a['group_name']} — {a['group_url']}", flush=True)
        elif action == "updated":
            updated += 1
            print(f"  ~ {a['group_name']} (status → joined)", flush=True)

    if dry_run:
        print("\n(dry-run — tracker not written)", flush=True)
    elif added or updated:
        _save_tracker(tracker)
        print(
            f"\nTracker updated: +{added} added, {updated} updated → groups.db",
            flush=True,
        )
    else:
        print("\nNothing new to add.", flush=True)

    skill_finished(
        SKILL_NAME,
        f"{added} new, {updated} updated, {len(tracker)} total",
    )
    return 0


if __name__ == "__main__":
    args = _parse_args()
    fb_session = build_fb_session()

    if args.health_check:
        sys.exit(_health_check(fb_session))

    try:
        with SingletonLock(SKILL_NAME):
            sys.exit(main(fb_session, dry_run=args.dry_run))
    except LockAcquisitionError as exc:
        print(f"another instance of {SKILL_NAME!r} is running: {exc}", file=sys.stderr)
        sys.exit(0)
