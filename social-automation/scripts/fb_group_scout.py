"""Facebook Group Scout — searches FB for dog groups to join, sourced from
niche keywords + content-competitor names (data/competitors.json). Reuses
facebook_session.json from fb_login.py.

Usage:
    fb_group_scout.py                    # normal run (weekly cadence)
    fb_group_scout.py --force            # override weekly re-run guard
    fb_group_scout.py --dry-run          # list, no join requests
    fb_group_scout.py --approve "1 10"   # non-interactive approval (or 'all'/'none')
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from group_discovery.approval import (
    get_user_approval,
    parse_approve_arg,
    print_candidate,
    send_join_requests,
)
from group_discovery.competitor_signals import (
    active_queries as competitor_queries,
)
from group_discovery.competitor_signals import (
    annotate_with_mentions,
    load_competitors,
)
from group_discovery.fb_search import pace_between_queries, search_groups
from group_discovery.scoring import parse_member_count, score_group
from group_discovery.state import (
    SESSION_FILE,
    add_to_pending,
    join_requests_this_week,
    join_requests_today,
    load_known_groups,
    load_last_run,
    load_pending,
    log_error,
    remove_from_pending,
    save_last_run,
    save_pending,
)
from notifier import skill_finished, skill_skipped, skill_started

JOIN_LIMIT_PER_WEEK = 6
JOIN_LIMIT_PER_DAY = 2  # forces 6/week to spread across ≥ 3 days
MIN_SCORE = 40

SEARCH_QUERIES = [
    "homemade dog food",
    "raw dog food",
    "dog nutrition",
    "dog food recipes",
    "dog diet advice",
    "running with dogs",
    "canicross",
    "GPS dog tracker",
    "dog hiking",
    "dog owners community",
    "healthy dogs",
    "dog product reviews",
]


def compute_budget(bypass_daily: bool = False) -> tuple[int, int, int]:
    """Return (effective, weekly_remaining, daily_remaining). bypass_daily skips daily cap; weekly always enforced."""
    w = max(0, JOIN_LIMIT_PER_WEEK - join_requests_this_week())
    d = max(0, JOIN_LIMIT_PER_DAY - join_requests_today())
    return (w if bypass_daily else min(w, d)), w, d


def check_rerun_guard(last_run: dict) -> bool:
    """True if scout should skip (ran successfully in last 7d, no --force)."""
    last_at = (last_run.get("fb_group_scout", {}).get("last_run_at") or "")[:10]
    if not last_at:
        return False
    days = (date.today() - date.fromisoformat(last_at)).days
    if days < 7 and last_run["fb_group_scout"].get("status") == "success":
        print(f"SKIP: Already ran successfully {days} day(s) ago ({last_at}).")
        if "--force" not in sys.argv:
            print("Use --force to override.")
            skill_skipped("fb-group-scout", f"Already ran {days} day(s) ago")
            return True
        print("--force detected, re-running.\n")
    return False


def collect_candidates(page, queries: list[tuple[str, str]], known: set[str]) -> dict[str, dict]:
    """Run every (label, query) pair, return url → card dict (highest score kept)."""
    all_candidates: dict[str, dict] = {}
    for label, query in queries:
        print(f'Searching [{label}]: "{query}"')
        try:
            raw_cards = search_groups(page, query)
            print(f"  Raw results: {len(raw_cards)}")
            for card in raw_cards:
                url = card["url"].lower()
                if url in known or card["name"].lower() in known:
                    continue
                card["member_count"] = parse_member_count(card["member_text"])
                card["found_via_query"] = query
                card["found_via_channel"] = label
                # Score without competitor boost; annotated+rescored after loop
                card["score"] = score_group(card)
                mc = card["member_count"]
                if mc > 0 and (mc < 1_000 or mc > 150_000):
                    continue
                if url not in all_candidates or card["score"] > all_candidates[url]["score"]:
                    all_candidates[url] = card
        except Exception as e:
            print(f"  ERROR: {e}")
            log_error(f"SEARCH_FAILED: [{label}] query='{query}' — {e}")
        pace_between_queries()
    return all_candidates


def apply_competitor_boost(candidates: list[dict]) -> None:
    """Annotate candidates with competitor mentions and re-score with boost."""
    competitors = load_competitors()
    if not competitors:
        return
    annotate_with_mentions(candidates, competitors)
    for g in candidates:
        g["score"] = score_group(g, competitor_mentions=g.get("competitor_mentions", 0))


def run_scout(
    dry_run: bool = False,
    preselected: str | None = None,
    bypass_daily: bool = False,
) -> None:
    from playwright.sync_api import sync_playwright

    print("=== Facebook Group Scout (CLI) ===\n")

    last_run = load_last_run()
    if check_rerun_guard(last_run):
        return

    budget, weekly, daily = compute_budget(bypass_daily=bypass_daily)
    print(
        f"Budget — weekly remaining: {weekly}/{JOIN_LIMIT_PER_WEEK}, "
        f"daily remaining: {daily}/{JOIN_LIMIT_PER_DAY}, effective: {budget}"
    )
    if budget <= 0 and not dry_run:
        reason = (
            "Weekly limit reached"
            if weekly <= 0
            else "Daily limit reached — try again tomorrow"
        )
        print(f"ABORT: {reason}.")
        skill_skipped("fb-group-scout", reason)
        return

    skill_started(
        "fb-group-scout",
        f"Searching for new dog groups — budget: {budget} "
        f"(weekly {weekly}/{JOIN_LIMIT_PER_WEEK}, daily {daily}/{JOIN_LIMIT_PER_DAY})",
    )

    known_groups = load_known_groups()
    print(f"Known groups to skip: {len(known_groups)}")

    pending = load_pending()
    pending = [g for g in pending if g["url"].lower() not in known_groups]
    save_pending(pending)
    if pending:
        print(f"\n📋 Pending queue: {len(pending)} group(s) from previous runs")

    if not SESSION_FILE.exists():
        print("ERROR: No saved Facebook session found. Run: python scripts/fb_login.py")
        return

    queries: list[tuple[str, str]] = [("keyword", q) for q in SEARCH_QUERIES]
    queries += [("competitor", q) for q in competitor_queries()]
    print(
        f"\nQuery plan: {len(SEARCH_QUERIES)} keyword + "
        f"{len(queries) - len(SEARCH_QUERIES)} competitor"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        print("\nChecking Facebook session...")
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        time.sleep(3)
        if "login" in page.url.lower():
            print("ABORT: Facebook session expired. Re-run fb_login.py")
            log_error("SESSION_EXPIRED")
            browser.close()
            return
        print("Facebook session OK.\n")

        all_candidates = collect_candidates(page, queries, known_groups)
        candidates = list(all_candidates.values())
        apply_competitor_boost(candidates)
        candidates = [c for c in candidates if c["score"] >= MIN_SCORE]
        candidates.sort(key=lambda g: g["score"], reverse=True)
        candidates = candidates[:15]

        print(f"\nTotal qualifying candidates: {len(candidates)}")

        if not candidates:
            print("No new qualifying groups found. Done.")
            browser.close()
            last_run["fb_group_scout"] = {
                "last_run_at": datetime.now(UTC).isoformat(),
                "groups_found": 0,
                "groups_approved": 0,
                "join_requests_sent": 0,
                "status": "success",
            }
            save_last_run(last_run)
            return

        if dry_run:
            print("\n[DRY RUN — saving to pending queue, no join requests sent]\n")
            for i, g in enumerate(candidates, 1):
                print_candidate(i, g)
            added = add_to_pending(candidates, known_groups)
            print(f"\n✅ Saved {added} new group(s) to pending queue")
            browser.close()
            return

        pending_to_join = [g for g in pending if g["url"].lower() not in known_groups]
        if pending_to_join:
            seen_urls = {g["url"].lower() for g in pending_to_join}
            fresh = [g for g in candidates if g["url"].lower() not in seen_urls]
            combined = pending_to_join + fresh
        else:
            combined = candidates

        approved = get_user_approval(
            combined, budget, JOIN_LIMIT_PER_WEEK, JOIN_LIMIT_PER_DAY, preselected=preselected
        )
        if not approved:
            print("\nNo groups approved.")
            add_to_pending(candidates, known_groups)
            browser.close()
            return

        join_requests_sent = send_join_requests(page, approved, known_groups)

        context.storage_state(path=str(SESSION_FILE))
        browser.close()

    remove_from_pending([g["url"] for g in approved])
    unapproved = [g for g in candidates if g not in approved]
    added_pending = add_to_pending(unapproved, known_groups)
    if added_pending:
        print(f"Saved {added_pending} unapproved candidate(s) to pending queue for next run.")

    last_run["fb_group_scout"] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "groups_found": len(candidates),
        "groups_approved": len(approved),
        "join_requests_sent": join_requests_sent,
        "status": "success",
    }
    save_last_run(last_run)

    pending_remaining = len(load_pending())
    _, weekly_after, daily_after = compute_budget()
    print(
        f"\n=== Scout Complete: {len(queries)} queries → {len(all_candidates)} raw → "
        f"{len(candidates)} surfaced → {join_requests_sent} joined "
        f"(weekly {weekly_after}/{JOIN_LIMIT_PER_WEEK}, daily {daily_after}/{JOIN_LIMIT_PER_DAY}, "
        f"pending={pending_remaining}) ==="
    )
    skill_finished(
        "fb-group-scout",
        f"🔍 {len(queries)} queries → {len(candidates)} candidates\n"
        f"✅ Joined: {join_requests_sent} (weekly {weekly_after}/{JOIN_LIMIT_PER_WEEK} remaining)",
    )


if __name__ == "__main__":
    run_scout(
        dry_run="--dry-run" in sys.argv,
        preselected=parse_approve_arg(),
        bypass_daily="--bypass-daily-cap" in sys.argv,
    )
