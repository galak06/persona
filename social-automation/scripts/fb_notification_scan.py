"""FB notification scanner — find groups that approved a join request.

Opens facebook.com/notifications via the saved session, parses the notification
list for "approved your request to join", "welcome to", or "you joined" events,
and upserts each matching group into `data/groups_tracker.json`.

The tracker is the source of truth for `fb-group-publisher` — which groups are
we in, what are their rules, and when did we last post there.

Usage:
    python scripts/fb_notification_scan.py            # scan + update tracker
    python scripts/fb_notification_scan.py --dry-run  # show what would be added
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

from lib.logger import log_step
from notifier import skill_error, skill_finished, skill_started


SESSION_FILE = settings.paths.facebook_session
TRACKER_FILE = settings.paths.groups_tracker

_APPROVAL_KEYWORDS = (
    "approved your request to join",
    "approved your request",
    "welcome to",
    "you joined",
    "you are now a member",
    "accepted your request",
)


def _load_tracker() -> list[dict]:
    if TRACKER_FILE.exists():
        return json.loads(TRACKER_FILE.read_text())
    return []


def _save_tracker(data: list[dict]) -> None:
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRACKER_FILE.write_text(json.dumps(data, indent=2))


def _scan_notifications(page) -> list[dict]:
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
    return raw or []


def _upsert(tracker: list[dict], entry: dict) -> tuple[bool, str]:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan FB notifications for group approvals")
    parser.add_argument("--dry-run", action="store_true", help="scan only, don't update tracker")
    args = parser.parse_args()

    skill_started("fb-notification-scan", "scanning FB for group approvals")

    if not SESSION_FILE.exists():
        skill_error("fb-notification-scan", "FB session not found — run fb_login.py")
        return

    from playwright.sync_api import sync_playwright

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    tracker = _load_tracker()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=ua,
        )
        page = ctx.new_page()
        try:
            approvals = _scan_notifications(page)
        finally:
            ctx.storage_state(path=str(SESSION_FILE))
            ctx.close()
            browser.close()

    log_step(f"found {len(approvals)} approval notifications")
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    added = updated = 0
    for a in approvals:
        entry = {
            "group_name": a["group_name"],
            "group_url": a["group_url"],
            "status": "joined",
            "joined_at": now,
            "rules": "unknown",
            "last_post_at": None,
            "source_notification": a["notification_text"],
        }
        is_added, action = _upsert(tracker, entry)
        if action == "added":
            added += 1
            print(f"  + {a['group_name']} — {a['group_url']}", flush=True)
        elif action == "updated":
            updated += 1
            print(f"  ~ {a['group_name']} (status → joined)", flush=True)

    if not args.dry_run and (added or updated):
        _save_tracker(tracker)
        print(f"\nTracker updated: +{added} added, {updated} updated → {TRACKER_FILE}", flush=True)
    elif args.dry_run:
        print("\n(dry-run — tracker not written)", flush=True)
    else:
        print("\nNothing new to add.", flush=True)

    skill_finished(
        "fb-notification-scan",
        f"{added} new, {updated} updated, {len(tracker)} total",
    )


if __name__ == "__main__":
    main()
