"""Enrich groups_tracker.json with full names + rules from each group page.

For every entry in `data/groups_tracker.json`, navigates to the group URL via
the saved FB session and scrapes:

  - full group name (page title — reliable)
  - privacy badge (public / private — from the header metadata)
  - group rules (best-effort from the About / Rules section; "unknown" on miss)
  - member count (bonus signal)

Rules extraction is intentionally best-effort — FB surfaces rules in different
widgets across groups, and some private groups show nothing until you navigate
a specific tab. When we can't find them, we leave `rules: unknown` and the
user can fill manually.

Usage:
    python scripts/fb_group_enrich.py                 # enrich every tracker entry
    python scripts/fb_group_enrich.py --only URL      # enrich a single group
    python scripts/fb_group_enrich.py --dry-run       # show findings, don't write
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

from lib.logger import log_step
from notifier import skill_error, skill_finished, skill_started


SESSION_FILE = settings.paths.facebook_session
TRACKER_FILE = settings.paths.groups_tracker


def _enrich_group(page, url: str) -> dict:
    """Navigate to group URL and return {name, privacy, rules, member_count}."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)
    # Scroll to nudge the rules/about card into view
    for pct in (0.25, 0.5, 0.75):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
        time.sleep(1)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)

    return page.evaluate(
        """() => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        // Name: first h1 is the group title on a group page.
        const h1 = document.querySelector('h1');
        const name = h1 ? norm(h1.textContent) : '';

        // Privacy + members: FB renders them together as "Public group · 89.6K members".
        // Scan every div up to ~500 chars long for this pattern — avoids page-wide
        // false matches while bypassing the left-sidebar chrome that buries the
        // header past position 4000.
        let privacy = 'unknown';
        let member_count = null;
        const divs = Array.from(document.querySelectorAll('div, span'));
        for (const d of divs) {
            const t = norm(d.textContent);
            if (!t || t.length > 300) continue;
            const lo = t.toLowerCase();
            if (privacy === 'unknown') {
                if (lo.includes('private group')) privacy = 'private';
                else if (lo.includes('public group')) privacy = 'public';
            }
            if (!member_count) {
                const m = t.match(/([\\d,.]+\\s?[Kk]?)\\s+members/);
                if (m) member_count = m[1];
            }
            if (privacy !== 'unknown' && member_count) break;
        }

        // Rules: look for the rules card. FB usually labels it "Group rules"
        // with numbered/bulleted items below. Fallback: grab the About text.
        let rules = 'unknown';
        const allEls = Array.from(document.querySelectorAll('h2, h3, div[role="heading"]'));
        const rulesHeader = allEls.find(el => {
            const t = norm(el.textContent).toLowerCase();
            return t === 'group rules' || t === 'rules' || t.startsWith('group rules from');
        });
        if (rulesHeader) {
            // Walk siblings and descendants to collect rule text.
            const parent = rulesHeader.closest('[role="article"], div, section') || rulesHeader.parentElement;
            if (parent) {
                const text = norm(parent.textContent);
                // Trim the header out, keep body
                const body = text.replace(/^group rules(\\s+from[^:]*)?:?\\s*/i, '').slice(0, 1200);
                if (body.length > 20) rules = body;
            }
        }

        return { name, privacy, rules, member_count };
    }"""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich groups_tracker.json with name + rules")
    parser.add_argument("--only", help="enrich a single group URL")
    parser.add_argument("--dry-run", action="store_true", help="print findings without saving")
    args = parser.parse_args()

    skill_started("fb-group-enrich", "enriching groups tracker")

    if not TRACKER_FILE.exists():
        skill_error("fb-group-enrich", "tracker file missing — run fb_notification_scan first")
        return
    if not SESSION_FILE.exists():
        skill_error("fb-group-enrich", "FB session not found — run fb_login.py")
        return

    tracker = json.loads(TRACKER_FILE.read_text())
    targets = [g for g in tracker if not args.only or g.get("group_url") == args.only]
    if not targets:
        print(f"No matching groups for --only={args.only!r}", flush=True)
        return

    from playwright.sync_api import sync_playwright

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    updated = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=ua,
        )
        page = ctx.new_page()
        try:
            for group in targets:
                url = group["group_url"]
                log_step(f"  → {url}")
                try:
                    data = _enrich_group(page, url)
                except Exception as e:
                    print(f"    ERROR: {e}", flush=True)
                    continue
                name = data.get("name") or group.get("group_name", "")
                privacy = data.get("privacy", "unknown")
                member_count = data.get("member_count")
                rules = data.get("rules", "unknown")
                print(
                    f"    name={name!r} privacy={privacy} members={member_count} "
                    f"rules={'<found>' if rules != 'unknown' else 'unknown'}",
                    flush=True,
                )
                if not args.dry_run:
                    if name:
                        group["group_name"] = name
                    group["privacy"] = privacy
                    group["member_count"] = member_count
                    group["rules"] = rules
                    updated += 1
                time.sleep(2)
        finally:
            ctx.storage_state(path=str(SESSION_FILE))
            ctx.close()
            browser.close()

    if not args.dry_run and updated:
        TRACKER_FILE.write_text(json.dumps(tracker, indent=2))
        print(f"\nTracker updated: {updated} entries → {TRACKER_FILE}", flush=True)
    elif args.dry_run:
        print("\n(dry-run — tracker not written)", flush=True)

    skill_finished("fb-group-enrich", f"enriched {updated}/{len(targets)} groups")


if __name__ == "__main__":
    main()
