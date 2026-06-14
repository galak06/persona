# pyright: reportMissingImports=false
# Pre-existing print()-based step logging throughout this script; structured
# log migration is deferred to a dedicated refactor (sys.path-based imports
# also force the pyright suppression — bootstrap rewires sys.path at runtime).
"""Facebook Group Scout — find dog groups to join from niche keywords +
content-competitor names (data/competitors.json). Reuses the brand FB session.

CLI: --force / --dry-run / --approve "1 10"|'all'|'none' / --bypass-daily-cap /
--health-check (exit 0 if session present, 1 otherwise).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script

settings, log = init_script(__name__)

from group_discovery.approval import (
    get_user_approval,
    print_candidate,
    send_join_requests,
)
from group_discovery.competitor_signals import active_queries as competitor_queries
from group_discovery.competitor_signals import annotate_with_mentions, load_competitors
from group_discovery.fb_search import pace_between_queries, search_groups
from group_discovery.scoring import parse_member_count, score_group
from group_discovery.state import (
    add_to_pending,
    join_requests_today,
    load_known_groups,
    load_last_run,
    load_pending,
    log_error,
    remove_from_pending,
    save_last_run,
    save_pending,
)
from lib.fb.session import FbSession, build_fb_session
from lib.runtime.singleton import SingletonLock
from notifier import skill_finished, skill_skipped, skill_started

if TYPE_CHECKING:
    from playwright.sync_api import Page

JOIN_LIMIT_PER_DAY = 10  # ~2.5x under FB's ~25/day ceiling
MIN_SCORE = 40

SEARCH_QUERIES = [
    "homemade dog food", "raw dog food", "dog nutrition", "dog food recipes",
    "dog diet advice", "running with dogs", "canicross", "GPS dog tracker",
    "dog hiking", "dog owners community", "healthy dogs", "dog product reviews",
]


def compute_budget(bypass_daily: bool = False) -> tuple[int, int]:
    """Return (effective, daily_remaining). bypass_daily ignores the daily cap."""
    d = max(0, JOIN_LIMIT_PER_DAY - join_requests_today())
    return (JOIN_LIMIT_PER_DAY if bypass_daily else d), d


def check_rerun_guard(last_run: dict[str, dict[str, Any]]) -> bool:
    """True if scout should skip (ran successfully today, no --force)."""
    last_at = str(last_run.get("fb_group_scout", {}).get("last_run_at") or "")[:10]
    if not last_at:
        return False
    days = (date.today() - date.fromisoformat(last_at)).days
    if days < 1 and last_run["fb_group_scout"].get("status") == "success":
        print(f"SKIP: Already ran successfully today ({last_at}).")
        if "--force" not in sys.argv:
            print("Use --force to override.")
            skill_skipped("fb-group-scout", f"Already ran today ({last_at})")
            return True
        print("--force detected, re-running.\n")
    return False


def collect_candidates(
    page: Page, queries: list[tuple[str, str]], known: set[str]
) -> dict[str, dict[str, Any]]:
    """Run every (label, query) pair, return url → card dict (highest score kept)."""
    all_candidates: dict[str, dict[str, Any]] = {}
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


def _success_record(
    *, found: int, approved: int = 0, sent: int = 0, mode: str | None = None
) -> dict[str, Any]:
    """Build the last_run.fb_group_scout payload (uniform across exit paths)."""
    rec: dict[str, Any] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "groups_found": found,
        "groups_approved": approved,
        "join_requests_sent": sent,
        "status": "success",
    }
    if mode:
        rec["mode"] = mode
    return rec


def apply_competitor_boost(candidates: list[dict[str, Any]]) -> None:
    """Annotate candidates with competitor mentions and re-score with boost."""
    competitors = load_competitors()
    if not competitors:
        return
    annotate_with_mentions(candidates, competitors)
    for g in candidates:
        g["score"] = score_group(g, competitor_mentions=g.get("competitor_mentions", 0))


def main(
    session: FbSession, *, dry_run: bool = False,
    preselected: str | None = None, bypass_daily: bool = False,
) -> None:
    print("=== Facebook Group Scout (CLI) ===\n")

    last_run = load_last_run()
    if check_rerun_guard(last_run):
        return

    budget, daily = compute_budget(bypass_daily=bypass_daily)
    caps = f"daily {daily}/{JOIN_LIMIT_PER_DAY}"
    print(f"Budget — {caps}, effective: {budget}")

    # --- Join pre-approved groups first ---
    known_groups = load_known_groups()
    pre_approved = [g for g in load_pending() if g.get("status") == "approved"]
    pre_approved = [g for g in pre_approved if g["url"].lower() not in known_groups]
    if pre_approved:
        print(f"Pre-approved queue: {len(pre_approved)} group(s) waiting to join.")
        if budget <= 0 and not dry_run:
            print("ABORT: Daily limit already reached — pre-approved groups will join tomorrow.")
            skill_skipped("fb-group-scout", "Daily limit reached — pre-approved groups queued for tomorrow")
            return
        to_join = pre_approved[:budget]
        print(f"Joining {len(to_join)} pre-approved group(s) (cap: {budget}).")
        skill_started("fb-group-scout", f"Joining {len(to_join)} pre-approved group(s) — budget: {budget} ({caps})")
        if not session.is_authenticated():
            print("ERROR: No saved Facebook session found. Run: python scripts/fb_login.py")
            return
        with session.page() as page:
            print("\nChecking Facebook session...")
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            time.sleep(3)
            if "login" in page.url.lower():
                print("ABORT: Facebook session expired. Re-run fb_login.py")
                log_error("SESSION_EXPIRED")
                return
            print("Facebook session OK.\n")
            if dry_run:
                print("[dry-run] Would join pre-approved groups (skipping actual requests):")
                for g in to_join:
                    print(f"  - {g['name']} [{g.get('privacy','?').upper()}]")
                join_requests_sent = 0
            else:
                join_requests_sent = send_join_requests(page, to_join, known_groups)
        if not dry_run:
            remove_from_pending([g["url"] for g in to_join])
        last_run["fb_group_scout"] = _success_record(
            found=0, approved=len(to_join), sent=join_requests_sent, mode="pre-approved"
        )
        save_last_run(last_run)
        _, daily_after = compute_budget()
        print(
            f"\n=== Pre-approved join complete: {join_requests_sent} joined "
            f"({daily_after}/{JOIN_LIMIT_PER_DAY} daily remaining) ==="
        )
        skill_finished("fb-group-scout", (
            f"Pre-approved: joined {join_requests_sent} group(s)\n"
            f"Daily remaining: {daily_after}/{JOIN_LIMIT_PER_DAY}"
        ))
        return  # skip search phase this run

    if budget <= 0 and not dry_run:
        print("ABORT: Daily limit reached — try again tomorrow.")
        skill_skipped("fb-group-scout", "Daily limit reached — try again tomorrow")
        return

    skill_started("fb-group-scout", f"Searching for new dog groups — budget: {budget} ({caps})")

    print(f"Known groups to skip: {len(known_groups)}")

    pending = load_pending()
    pending = [g for g in pending if g["url"].lower() not in known_groups]
    save_pending(pending)
    if pending:
        print(f"\n📋 Pending queue: {len(pending)} group(s) from previous runs")

    if not session.is_authenticated():
        print("ERROR: No saved Facebook session found. Run: python scripts/fb_login.py")
        return

    queries: list[tuple[str, str]] = [("keyword", q) for q in SEARCH_QUERIES]
    queries += [("competitor", q) for q in competitor_queries()]
    print(f"\nQuery plan: {len(SEARCH_QUERIES)} keyword + {len(queries) - len(SEARCH_QUERIES)} competitor")

    with session.page() as page:
        print("\nChecking Facebook session...")
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        time.sleep(3)
        if "login" in page.url.lower():
            print("ABORT: Facebook session expired. Re-run fb_login.py")
            log_error("SESSION_EXPIRED")
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
            last_run["fb_group_scout"] = _success_record(found=0)
            save_last_run(last_run)
            return

        if dry_run:
            print("\n[DRY RUN — saving to pending queue, no join requests sent]\n")
            for i, g in enumerate(candidates, 1):
                print_candidate(i, g)
            added = add_to_pending(candidates, known_groups)
            print(f"\n✅ Saved {added} new group(s) to pending queue")
            last_run["fb_group_scout"] = _success_record(found=len(candidates), mode="dry-run")
            save_last_run(last_run)
            return

        pending_to_join = [g for g in pending if g["url"].lower() not in known_groups]
        if pending_to_join:
            seen_urls = {g["url"].lower() for g in pending_to_join}
            fresh = [g for g in candidates if g["url"].lower() not in seen_urls]
            combined = pending_to_join + fresh
        else:
            combined = candidates

        approved = get_user_approval(combined, budget, preselected or "all")
        if not approved:
            print("\nNo groups approved.")
            add_to_pending(candidates, known_groups)
            return

        join_requests_sent = send_join_requests(page, approved, known_groups)

    remove_from_pending([g["url"] for g in approved])
    unapproved = [g for g in candidates if g not in approved]
    added_pending = add_to_pending(unapproved, known_groups)
    if added_pending:
        print(f"Saved {added_pending} unapproved candidate(s) to pending queue for next run.")

    last_run["fb_group_scout"] = _success_record(
        found=len(candidates), approved=len(approved), sent=join_requests_sent
    )
    save_last_run(last_run)

    pending_remaining = len(load_pending())
    _, daily_after = compute_budget()
    caps_after = f"daily {daily_after}/{JOIN_LIMIT_PER_DAY}"
    print(
        f"\n=== Scout Complete: {len(queries)} queries → {len(all_candidates)} raw → "
        f"{len(candidates)} surfaced → {join_requests_sent} joined "
        f"({caps_after}, pending={pending_remaining}) ==="
    )
    skill_finished("fb-group-scout", (
        f"🔍 {len(queries)} queries → {len(candidates)} candidates\n"
        f"✅ Joined: {join_requests_sent} ({daily_after}/{JOIN_LIMIT_PER_DAY} daily remaining)"
    ))


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Facebook Group Scout — find dog groups to join.")
    parser.add_argument("--force", action="store_true", help="Override weekly re-run guard.")
    parser.add_argument("--dry-run", action="store_true", help="List candidates; no join requests.")
    parser.add_argument("--approve", type=str, default=None, help="'all'/'none'/'1 3 5'.")
    parser.add_argument("--bypass-daily-cap", action="store_true", help="Skip daily cap only.")
    parser.add_argument("--health-check", action="store_true", help="Probe session, exit 0/1.")
    return parser


if __name__ == "__main__":
    args = _build_argparser().parse_args()
    fb_session = build_fb_session()

    if args.health_check:
        if fb_session.is_authenticated():
            print(f"FB session OK (storage: {fb_session.storage_path})")
            sys.exit(0)
        print(f"SESSION_EXPIRED: {fb_session.storage_path} missing or empty", file=sys.stderr)
        sys.exit(1)

    with SingletonLock("fb_group_scout"):
        main(
            fb_session,
            dry_run=args.dry_run,
            # Approval gate removed: with no explicit --approve, auto-approve all
            # discovered groups up to the daily/weekly cap (the remaining limit).
            preselected=args.approve if args.approve is not None else "all",
            bypass_daily=args.bypass_daily_cap,
        )
