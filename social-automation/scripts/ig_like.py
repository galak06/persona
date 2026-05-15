"""Instagram Hashtag Liker — standalone likes-only runner.

Extracted from scripts/ig_scan.py after the comment-drafting flow was retired
on 2026-05-15. Walks today's hashtags from data/instagram_accounts.csv, scores
each post via score_relevance + ig_score_adjustments, then likes qualifying
posts (up to the daily cap from lib/rate_limiter.py). No queue writes, no
comment drafting, no Telegram approvals.

Usage:
    ig_like.py                  # scout + like within daily cap
    ig_like.py --dry-run        # walk hashtags, evaluate, NO like clicks
    ig_like.py --health-check   # verify session + sources, exit
    ig_like.py --force          # bypass "already ran today" re-run guard
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

from comment_generator import score_relevance
from deduplication import is_duplicate, mark_engaged
from local_env import load_local_env
from lib.logger import log_progress, log_step
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import can_act, print_status, record_action, wait_random_delay

SESSION_FILE = settings.paths.instagram_session
LAST_RUN_FILE = settings.paths.last_run
ERROR_LOG = (settings.paths.logs_dir / "errors.log")
CONFIG_FILE = (settings.paths.brand_dir / "config.json")
HASHTAG_FILE = PROJECT_ROOT / "data/instagram_accounts.csv"
SKILL_NAME = "ig-like"

# Competitor + own-account guards (carried over from ig_scan.py)
COMPETITOR_ACCOUNTS = {
    "tractive", "tractivepets", "ficollar", "fidogs",
    "whistlepet", "whistle", "linkakc",
}
OWN_ACCOUNT = "dogfoodandfun"

# JS payloads — copied verbatim from ig_scan.py so behaviour stays identical.
EXTRACT_HASHTAG_POSTS_JS = """
() => {
    const links = Array.from(document.querySelectorAll('a[href*="/p/"]'));
    const posts = [];
    const seen = new Set();
    for (const a of links) {
        const href = a.getAttribute('href') || '';
        const match = href.match(/\\/p\\/([^\\/]+)/);
        if (!match) continue;
        const postId = match[1];
        if (seen.has(postId)) continue;
        seen.add(postId);
        posts.push({url: 'https://www.instagram.com' + href, post_id: postId});
    }
    return posts.slice(0, 15);
}
"""

EXTRACT_POST_DETAILS_JS = """
() => {
    const result = {caption: '', like_text: '', comment_text: '', author: ''};
    const h1 = document.querySelector('h1');
    if (h1) result.caption = h1.innerText || '';
    if (!result.caption) {
        const spans = document.querySelectorAll('span[dir="auto"]');
        for (const span of spans) {
            const t = span.innerText || '';
            if (t.length > 30) { result.caption = t; break; }
        }
    }
    const authorLink = document.querySelector('header a[href]:not([href="/"])');
    if (authorLink) {
        const href = authorLink.getAttribute('href') || '';
        result.author = href.replace(/\\//g, '').trim();
    }
    const allSpans = document.querySelectorAll('span');
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/\\d.*like/i) || t.match(/like.*\\d/i)) { result.like_text = t; break; }
    }
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/view.*\\d+.*comment/i) || t.match(/\\d+.*comment/i)) {
            result.comment_text = t; break;
        }
    }
    return result;
}
"""

CLICK_LIKE_JS = """
() => {
    const svgs = document.querySelectorAll('svg[aria-label="Like"]');
    for (const svg of svgs) {
        const btn = svg.closest('[role="button"]') || svg.closest('button') || svg.parentElement;
        if (btn) { btn.click(); return 'liked'; }
    }
    const btns = document.querySelectorAll('[aria-label="Like"][role="button"], button[aria-label="Like"]');
    if (btns.length > 0) { btns[0].click(); return 'liked'; }
    const unlikeSvgs = document.querySelectorAll('svg[aria-label="Unlike"]');
    if (unlikeSvgs.length > 0) return 'already_liked';
    return 'not_found';
}
"""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Evaluate posts but do not click Like.")
    p.add_argument("--health-check", action="store_true", help="Verify session + config, exit.")
    p.add_argument("--force", action="store_true", help="Bypass already-ran-today guard.")
    return p.parse_args()


def _log_json(event: str, **fields: Any) -> None:
    """Emit a structured JSON log line to stdout (launchd captures stdout)."""
    rec = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
    print(json.dumps(rec, default=str), flush=True)


def _log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ERROR_LOG.open("a") as f:
        f.write(f"[{datetime.now(UTC).isoformat()}] {msg}\n")


def _should_scan_today(freq: str, today: date) -> bool:
    if freq == "daily":
        return True
    if freq == "every_2_days":
        return today.toordinal() % 2 == 0
    if freq == "weekly":
        return today.weekday() == 0
    return False


def _load_hashtags() -> list[dict[str, str]]:
    today = date.today()
    rows: list[dict[str, str]] = []
    with HASHTAG_FILE.open() as f:
        for row in csv.DictReader(f):
            if _should_scan_today((row.get("scan_frequency") or "").strip(), today):
                rows.append(row)
    return rows


def _load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def _load_last_run() -> dict[str, Any]:
    if LAST_RUN_FILE.exists():
        with LAST_RUN_FILE.open() as f:
            return json.load(f)  # type: ignore[no-any-return]
    return {}


def _save_last_run(data: dict[str, Any]) -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LAST_RUN_FILE.open("w") as f:
        json.dump(data, f, indent=2)


def _parse_like_count(text: str) -> int:
    if not text:
        return 0
    t = text.lower().replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s*k", t)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"(\d+\.?\d*)\s*m", t)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else 0


def _ig_score(base_score: float, like_count: int) -> float:
    s = base_score
    if like_count < 500:
        s += 0.15
    if like_count > 5000:
        s -= 0.20
    return round(s, 2)


def _dismiss_overlays(page: Any) -> None:
    for sel in [
        "button:has-text('Not Now')",
        "button:has-text('Cancel')",
        "button:has-text('Decline')",
        "button:has-text('Accept')",
        "[aria-label='Close']",
    ]:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                time.sleep(1)
                return
        except Exception:
            pass


def _health_check() -> int:
    problems: list[str] = []
    if not SESSION_FILE.exists():
        problems.append(f"IG session missing: {SESSION_FILE} (run scripts/ig_login.py)")
    if not CONFIG_FILE.exists():
        problems.append(f"config missing: {CONFIG_FILE}")
    if not HASHTAG_FILE.exists():
        problems.append(f"hashtag CSV missing: {HASHTAG_FILE}")
    hashtags = _load_hashtags() if HASHTAG_FILE.exists() else []
    _log_json(
        "ig_like_health",
        session=SESSION_FILE.exists(),
        hashtags_today=len(hashtags),
        can_like=can_act("instagram", "like"),
        problems=problems,
    )
    if problems:
        for p in problems:
            print(f"HEALTH FAIL: {p}", flush=True)
        return 1
    print("HEALTH OK", flush=True)
    return 0


def _evaluate_post(page: Any, post_info: dict[str, str], threshold: float) -> dict[str, Any]:
    """Open a post page, extract details, return scoring verdict (no side-effects)."""
    post_url = post_info["url"]
    page.goto(post_url, wait_until="domcontentloaded")
    time.sleep(3)
    _dismiss_overlays(page)
    try:
        d = page.evaluate(EXTRACT_POST_DETAILS_JS)
    except Exception:
        d = {"caption": "", "like_text": "", "comment_text": "", "author": ""}
    caption = (d.get("caption") or "")[:800]
    author = (d.get("author") or "").strip().strip("/").lower()
    if not author and caption:
        m = re.match(r"^([a-zA-Z0-9_.]+)\s", caption)
        if m:
            author = m.group(1).lower()
    like_count = _parse_like_count(d.get("like_text") or "")
    base = score_relevance(caption, {"comment_count": 0, "hours_old": 12})
    score = _ig_score(base, like_count)
    verdict = "ok"
    if author == OWN_ACCOUNT or caption.lower().startswith(OWN_ACCOUNT):
        verdict = "own_account"
    elif author in COMPETITOR_ACCOUNTS:
        verdict = "competitor"
    elif score < threshold:
        verdict = "below_threshold"
    return {"caption": caption, "author": author, "like_count": like_count, "score": score, "verdict": verdict}


def run(args: argparse.Namespace) -> int:
    load_local_env()

    if args.health_check:
        return _health_check()

    last_run = _load_last_run()
    today_iso = date.today().isoformat()
    prev = last_run.get(SKILL_NAME, {})
    if not args.force and (prev.get("last_run_at") or "")[:10] == today_iso and prev.get("status") == "success":
        _log_json("ig_like_skipped", reason="already_ran_today", prev=prev)
        skill_skipped(SKILL_NAME, f"already ran today — liked {prev.get('posts_liked', 0)}")
        return 0

    if not args.dry_run and not SESSION_FILE.exists():
        _log_json("ig_like_aborted", reason="session_missing")
        print("ERROR: no IG session. Run scripts/ig_login.py first.", flush=True)
        return 1

    if not can_act("instagram", "like"):
        _log_json("ig_like_skipped", reason="daily_cap_reached")
        skill_skipped(SKILL_NAME, "daily IG like cap reached")
        print_status()
        return 0

    hashtags = _load_hashtags()
    _log_json("ig_like_started", dry_run=args.dry_run, hashtags=len(hashtags))
    if not hashtags:
        skill_finished(SKILL_NAME, "no hashtags scheduled today", success=True)
        return 0

    config = _load_config()
    threshold = float(config["content_analysis"]["relevance_threshold"])

    if args.dry_run:
        return _dry_run_summary(hashtags, threshold)

    skill_started(SKILL_NAME, f"liking IG posts across {len(hashtags)} hashtags")
    print_status()

    liked = scanned = evaluated = 0
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        log_step("Launching browser")
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        time.sleep(4)
        if "login" in page.url.lower():
            _log_json("ig_like_aborted", reason="session_expired")
            _log_error("SESSION_EXPIRED")
            browser.close()
            return 1
        _dismiss_overlays(page)

        for idx, row in enumerate(hashtags, 1):
            if not can_act("instagram", "like"):
                break
            hashtag = (row.get("hashtag") or "").strip().lstrip("#")
            log_progress(idx, len(hashtags), f"#{hashtag}")
            try:
                page.goto(f"https://www.instagram.com/explore/tags/{hashtag}/", wait_until="domcontentloaded")
                time.sleep(4)
                _dismiss_overlays(page)
                for _ in range(2):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                scanned += 1
                posts = page.evaluate(EXTRACT_HASHTAG_POSTS_JS)
                for post in posts:
                    if not can_act("instagram", "like"):
                        break
                    evaluated += 1
                    post_id = post["post_id"]
                    if is_duplicate("instagram", post_id):
                        _log_json("ig_like", media_id=post_id, hashtag=hashtag, result="skipped_dedup")
                        continue
                    verdict = _evaluate_post(page, post, threshold)
                    if verdict["verdict"] != "ok":
                        _log_json("ig_like", media_id=post_id, hashtag=hashtag,
                                  score=verdict["score"], result=f"skipped_{verdict['verdict']}")
                        continue
                    try:
                        result = page.evaluate(CLICK_LIKE_JS)
                    except Exception as e:
                        result = f"error:{e}"
                    if result == "liked":
                        record_action("instagram", "like")
                        mark_engaged("instagram", post_id, "like", hashtag)
                        liked += 1
                        _log_json("ig_like", media_id=post_id, hashtag=hashtag,
                                  score=verdict["score"], result="liked")
                    else:
                        _log_json("ig_like", media_id=post_id, hashtag=hashtag,
                                  score=verdict["score"], result=str(result))
                    wait_random_delay("instagram", "like")
            except Exception as e:
                _log_error(f"HASHTAG_ERR #{hashtag}: {e}")
                _log_json("ig_like_hashtag_error", hashtag=hashtag, error=str(e))
            time.sleep(5)
        ctx.storage_state(path=str(SESSION_FILE))
        browser.close()

    last_run[SKILL_NAME] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "hashtags_scanned": scanned,
        "posts_evaluated": evaluated,
        "posts_liked": liked,
        "status": "success",
    }
    _save_last_run(last_run)
    _log_json("ig_like_finished", scanned=scanned, evaluated=evaluated, liked=liked)
    skill_finished(SKILL_NAME, f"liked {liked} posts across {scanned} hashtags")
    print_status()
    return 0


def _dry_run_summary(hashtags: list[dict[str, str]], threshold: float) -> int:
    _log_json("ig_like_dry_run", hashtags=[h.get("hashtag") for h in hashtags],
              threshold=threshold, daily_cap=8)
    print(f"DRY-RUN: would scan {len(hashtags)} hashtags @ threshold={threshold}", flush=True)
    for h in hashtags[:10]:
        print(f"  - {h.get('hashtag')} (tier {h.get('tier', '?')})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run(_parse_args()))
