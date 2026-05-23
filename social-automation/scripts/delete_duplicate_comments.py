"""
Delete duplicate Nalla's Dad comments on FB groups + IG posts.

Reads .claude/state/duplicate_cleanup_manifest_2026-05-05.json, navigates each
post URL via stored Playwright session, and deletes all but one of our matching
comments per post. Use --dry-run to preview matches without clicking.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.local_env import get_runtime_headless

MANIFEST = PROJECT_ROOT / ".claude" / "state" / "duplicate_cleanup_manifest_2026-05-05.json"
FB_SESSION = PROJECT_ROOT / ".claude" / "state" / "facebook_session.json"
IG_SESSION = PROJECT_ROOT / ".claude" / "state" / "instagram_session.json"
LOG_FILE = PROJECT_ROOT / "logs" / "duplicate_cleanup_2026-05-05.jsonl"
DOM_JS = (Path(__file__).resolve().parent / "_dedup_dom.js").read_text()

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
INTERACTIVE_MODE = False  # set by --interactive flag


def log_event(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(event) + "\n")


def load_targets() -> list[dict]:
    """One entry per (platform, post_url) with full comment text + delete count."""
    with MANIFEST.open() as f:
        manifest = json.load(f)

    # Key by (platform, post_url, content_preview) — same URL with different
    # comment texts must be processed separately.
    by_key: dict[tuple[str, str, str], dict] = {}
    for cluster in manifest["duplicate_clusters"]:
        plat = cluster["platform"]
        prefix = cluster["content_preview"]
        for d in cluster["delete"]:
            url = d.get("post_url") or ""
            if not url or url.startswith("("):
                continue
            key = (plat, url, prefix)
            if key not in by_key:
                by_key[key] = {
                    "platform": plat,
                    "post_url": url,
                    "content_preview": prefix,
                    "delete_count": 0,
                }
            by_key[key]["delete_count"] += 1

    # Recover full text from engagement log (manifest preview is 60 chars)
    text_by_prefix: dict[str, str] = {}
    with (PROJECT_ROOT / "logs" / "engagement_log.jsonl").open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = row.get("content") or ""
            if content:
                text_by_prefix[content[:60]] = content

    targets = []
    skipped_no_post_url = []
    for entry in by_key.values():
        full = text_by_prefix.get(entry["content_preview"])
        if not full:
            print(f"WARN: full text not found for prefix '{entry['content_preview']}'", flush=True)
            continue
        entry["full_text"] = full
        # Require a specific post URL — group-root URLs can't be navigated
        # directly to find a single post.
        url = entry["post_url"]
        is_specific = (
            "/p/" in url  # IG
            or "/posts/" in url  # FB post-specific
            or "/permalink/" in url
            or "/photo" in url
        )
        if not is_specific:
            skipped_no_post_url.append(entry)
            continue
        targets.append(entry)
    if skipped_no_post_url:
        print(f"\nSKIPPED {len(skipped_no_post_url)} target(s) — group-root URL only, manual cleanup needed:", flush=True)
        for s in skipped_no_post_url:
            print(f"  [{s['platform']}] {s['post_url']}  delete={s['delete_count']}", flush=True)
            print(f"      text=\"{s['full_text'][:80]}…\"", flush=True)
    return targets


def delete_first_match(page, plat: str, text: str) -> str:
    """Open menu → click 'Delete' → confirm if dialog appears. Verifies by recount."""
    finder = "findFb" if plat == "facebook" else "findIg"
    open_fn = "fbOpenMenuFirst" if plat == "facebook" else "igOpenMenuFirst"
    before = page.evaluate(f"(t) => window.{finder}(t).count", text)
    result = page.evaluate(f"(t) => window.{open_fn}(t)", text)
    if result != "menu_clicked":
        return result
    time.sleep(1.5)
    delete_clicked = page.evaluate("() => window.clickDeleteOption()")
    if delete_clicked != "delete_clicked":
        page.keyboard.press("Escape")
        return delete_clicked
    time.sleep(1.5)
    page.evaluate("() => window.confirmDeleteDialog()")  # ok if no dialog
    time.sleep(3)
    after = page.evaluate(f"(t) => window.{finder}(t).count", text)
    if after < before:
        return "deleted"
    return f"no_change(before={before},after={after})"


def process_target(page, target: dict, dry_run: bool) -> dict:
    plat = target["platform"]
    url = target["post_url"]
    text = target["full_text"]
    expected = target["delete_count"]

    print(f"\n→ {plat.upper()}  {url}", flush=True)
    print(f"  text: \"{text[:70]}…\"", flush=True)
    print(f"  expected matches: {expected + 1} (delete {expected}, keep 1)", flush=True)

    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(6)
    for pct in (0.4, 0.7, 1.0, 1.0):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
        time.sleep(2)
    page.evaluate(DOM_JS)  # inject finder/clicker helpers

    if plat == "instagram":
        loaded = page.evaluate("(t) => window.loadAllIgComments(t)", text)
        print(f"  after expand: {loaded} matching rows in DOM", flush=True)
    elif plat == "facebook":
        articles = page.evaluate("() => window.loadAllFbComments()")
        print(f"  after expand: {articles} comment articles in DOM", flush=True)

    if INTERACTIVE_MODE:
        proceed_file = PROJECT_ROOT / ".claude" / "state" / "_proceed_signal"
        skip_file = PROJECT_ROOT / ".claude" / "state" / "_skip_signal"
        for f in (proceed_file, skip_file):
            if f.exists():
                f.unlink()
        print(f"  ⏸  WAITING. Scroll/expand the browser to load duplicates.", flush=True)
        print(f"     Touch {proceed_file} to proceed, or {skip_file} to skip this URL.", flush=True)
        waited = 0
        while True:
            if proceed_file.exists():
                proceed_file.unlink()
                print(f"  ▶  Proceeding (waited {waited}s)...", flush=True)
                break
            if skip_file.exists():
                skip_file.unlink()
                print(f"  ⏭  Skipping URL (waited {waited}s)", flush=True)
                return {"platform": plat, "post_url": url, "status": "user_skipped"}
            time.sleep(2)
            waited += 2
            if waited > 1800:  # 30 min hard cap per URL
                print(f"  ⏱  Timeout (30 min) — skipping", flush=True)
                return {"platform": plat, "post_url": url, "status": "timeout"}

    finder = "findFb" if plat == "facebook" else "findIg"
    found = page.evaluate(f"(t) => window.{finder}(t)", text)
    print(f"  found: {found['count']} matching comments", flush=True)

    record = {
        "platform": plat,
        "post_url": url,
        "expected_to_delete": expected,
        "matches_found": found["count"],
        "dry_run": dry_run,
        "deletions": [],
    }

    if dry_run:
        record["status"] = "dry_run_only"
        log_event(record)
        return record
    if found["count"] <= 1:
        record["status"] = "skipped_no_extras"
        log_event(record)
        return record

    deletes_to_do = min(expected, found["count"] - 1)
    for i in range(deletes_to_do):
        outcome = delete_first_match(page, plat, text)
        record["deletions"].append({"i": i + 1, "outcome": outcome})
        print(f"    delete {i+1}/{deletes_to_do}: {outcome}", flush=True)
        if outcome != "deleted":
            break
        time.sleep(random.uniform(5, 15))

    record["status"] = "completed"
    log_event(record)
    return record


def run_platform(p, targets: list[dict], session_file: Path, login_url: str, dry_run: bool) -> None:
    if not targets:
        return
    ctx = p.new_context(storage_state=str(session_file), viewport={"width": 1280, "height": 900}, user_agent=UA)
    page = ctx.new_page()
    page.goto(login_url, wait_until="domcontentloaded")
    time.sleep(3)
    if "login" in page.url.lower() or "accounts/login" in page.url.lower():
        plat = targets[0]["platform"]
        print(f"ABORT: {plat} session expired.", flush=True)
        log_event({"action": "abort", "reason": f"{plat}_session_expired"})
        ctx.close()
        sys.exit(2)
    for t in targets:
        try:
            process_target(page, t, dry_run)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            log_event({"action": "error", "url": t["post_url"], "err": str(e)[:300]})
        if not dry_run:
            time.sleep(random.uniform(8, 20))
    ctx.storage_state(path=str(session_file))
    ctx.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--platform", choices=["facebook", "instagram", "both"], default="both")
    ap.add_argument("--limit-urls", type=int, default=0)
    ap.add_argument("--interactive", action="store_true", help="Pause on each URL for user to manually load comments before deletion")
    args = ap.parse_args()
    global INTERACTIVE_MODE
    INTERACTIVE_MODE = args.interactive

    targets = load_targets()
    if args.platform != "both":
        targets = [t for t in targets if t["platform"] == args.platform]
    if args.limit_urls:
        targets = targets[: args.limit_urls]

    print(f"\n=== {'DRY-RUN' if args.dry_run else 'LIVE'} — {len(targets)} URLs ===", flush=True)
    for t in targets:
        print(f"  [{t['platform']}] {t['post_url']}  delete={t['delete_count']}", flush=True)
    if not targets:
        return

    log_event({"action": "run_start", "dry_run": args.dry_run, "url_count": len(targets)})

    from playwright.sync_api import sync_playwright

    fb = [t for t in targets if t["platform"] == "facebook"]
    ig = [t for t in targets if t["platform"] == "instagram"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=get_runtime_headless())
        run_platform(browser, fb, FB_SESSION, "https://www.facebook.com", args.dry_run)
        run_platform(browser, ig, IG_SESSION, "https://www.instagram.com", args.dry_run)
        browser.close()

    print("\n=== Done ===", flush=True)
    log_event({"action": "run_end", "dry_run": args.dry_run})


if __name__ == "__main__":
    main()
