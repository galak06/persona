"""
WordPress Comment Scanner — pulls pending visitor comments from
dogfoodandfun.com, auto-trashes obvious spam, and queues the rest for a
Nalla's-Dad reply.

Mirrors fb_scan.py / ig_scan.py: scan → queue. The drafting step happens in
comment-composer (which calls Claude). The approve + post step happens in
comment_approver.py → comment_poster.py.

Auto-trash heuristic (conservative): we only trash comments that are obviously
link-stuffed / keyword-matched spam. Everything else goes to the queue, where
a human approves it in Telegram before anything is published or replied to.

Env vars required (read from .claude/settings.local.json env dict):
    WP_URL              — e.g. https://dogfoodandfun.com
    WP_USER             — application-password user
    WP_APP_PASSWORD     — application password (spaces preserved)

Usage:
    python scripts/wp_scan.py           # scan + queue
    python scripts/wp_scan.py --dry-run # don't trash / queue, just print
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from logger import enable_unbuffered, log_progress, log_step

enable_unbuffered()

from comment_generator import score_relevance
from deduplication import is_duplicate, mark_engaged
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import print_status

QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"
LAST_RUN_FILE = PROJECT_ROOT / ".claude/state/last_run.json"
ERROR_LOG = PROJECT_ROOT / "logs/errors.log"

# Comments beyond this age are stale — the visitor has moved on, a reply is
# awkward. Moderation still needs to happen, but we won't auto-draft a reply.
MAX_COMMENT_AGE_DAYS = 30

# Heuristic spam signals. Intentionally conservative so we never auto-trash a
# genuine question — borderline cases go through Telegram approval instead.
_SPAM_KEYWORDS = (
    "viagra", "cialis", "casino", "poker", "bitcoin", "crypto wallet",
    "forex", "payday loan", "seo services", "buy followers",
    "cheap essay", "write my essay", "escort",
)
_LINK_RE = re.compile(r"https?://", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    with ERROR_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def wp_client() -> httpx.Client:
    base = os.environ["WP_URL"].rstrip("/")
    user = os.environ["WP_USER"]
    pw = os.environ["WP_APP_PASSWORD"]
    return httpx.Client(
        base_url=base,
        auth=(user, pw),
        timeout=30.0,
        headers={"User-Agent": "dogfoodandfun-wp-scanner/0.1"},
    )


def strip_html(html: str) -> str:
    """Return plain text. WP returns comment content as HTML-wrapped paragraphs."""
    text = _HTML_TAG_RE.sub(" ", html or "")
    # collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


def is_self_pingback(comment: dict, wp_base: str) -> bool:
    """A pingback/trackback whose source URL is our own domain.

    WordPress auto-generates one of these every time one of our own posts
    links to another of our own posts. They carry no SEO value (we already
    have the internal link) and clutter the moderation queue — worth an
    automatic trash so the queue stays meaningful.
    """
    if comment.get("type") not in ("pingback", "trackback"):
        return False
    author_url = (comment.get("author_url") or "").lower().rstrip("/")
    base = wp_base.lower().rstrip("/")
    # Strip scheme for a tolerant compare (http/https/www variants).
    def _host(u: str) -> str:
        return re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
    return _host(author_url) == _host(base)


def is_obvious_spam(body: str, author_url: str = "") -> tuple[bool, str]:
    """Cheap heuristic. Returns (is_spam, reason). Designed for zero false positives
    on legitimate dog-owner questions — we'd rather queue a borderline comment for
    Telegram approval than auto-trash a real reader."""
    lower = body.lower()

    # 3+ links in the body = almost always spam on a dog-food blog. Genuine
    # commenters linking to one source is normal; three is a spammer habit.
    link_count = len(_LINK_RE.findall(body))
    if link_count >= 3:
        return True, f"{link_count} links in body"

    # Spam-keyword hit anywhere.
    for kw in _SPAM_KEYWORDS:
        if kw in lower:
            return True, f"spam keyword: {kw!r}"

    # Ultra-short comments with only emoji / single word — almost never worth a
    # reply. Not auto-trashed (the commenter may still be a real reader); left
    # for Telegram review. Return False here.

    # Author URL on a link farm / casino TLD.
    if author_url:
        low_url = author_url.lower()
        for tld in (".xyz", ".top", ".loan", ".click", ".win"):
            if low_url.endswith(tld):
                return True, f"suspicious author URL TLD: {tld}"

    return False, ""


def fetch_pending_comments(client: httpx.Client) -> list[dict]:
    """GET /wp-json/wp/v2/comments?status=hold — all moderation-queue items.

    Fetches each of the three comment types explicitly. The WP REST endpoint
    silently defaults to `type=comment` and ignores pingbacks/trackbacks
    unless you ask for them by name — a stock install can pile up dozens of
    pending pingbacks that never appear in the default query.

    `context=edit` returns raw content (un-rendered) + author_email + author_ip,
    which we need for moderation decisions. Requires moderate_comments capability
    on the authenticated user.
    """
    out: list[dict] = []
    for comment_type in ("comment", "pingback", "trackback"):
        page = 1
        while True:
            r = client.get(
                "/wp-json/wp/v2/comments",
                params={
                    "status": "hold",
                    "type": comment_type,
                    "per_page": 100,
                    "page": page,
                    "context": "edit",
                    "orderby": "date",
                    "order": "desc",
                },
            )
            if r.status_code == 400 and page > 1:
                break  # past last page
            if r.status_code >= 400:
                raise RuntimeError(
                    f"WP comments fetch failed ({comment_type}): "
                    f"{r.status_code} {r.text[:200]}"
                )
            batch = r.json()
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return out


def fetch_post_title(client: httpx.Client, post_id: int) -> tuple[str, str]:
    """Return (title, permalink) for a post — used as context for the approver."""
    r = client.get(f"/wp-json/wp/v2/posts/{post_id}", params={"context": "edit"})
    if r.status_code >= 400:
        return ("", "")
    data = r.json()
    return (
        strip_html(data.get("title", {}).get("rendered", "")),
        data.get("link", ""),
    )


def trash_comment(client: httpx.Client, comment_id: int) -> bool:
    """Move a comment to trash (not permanent delete — recoverable in WP admin).

    WP REST quirk: DELETE /comments/{id} moves to trash; DELETE with ?force=true
    permanently deletes. We use non-force so a false-positive spam call is
    recoverable from the WP admin UI.
    """
    r = client.delete(f"/wp-json/wp/v2/comments/{comment_id}")
    return r.status_code < 400


def run(dry_run: bool = False) -> None:
    print(f"=== WordPress Comment Scan ===\n", flush=True)
    print_status()

    # Re-run guard — match fb_scan / ig_scan style.
    last_run = load_json(LAST_RUN_FILE, {})
    today = date.today().isoformat()
    wp_last = last_run.get("wp_scan", {})
    wp_last_date = (wp_last.get("last_run_at") or "")[:10]
    if wp_last_date == today and wp_last.get("status") == "success" and "--force" not in sys.argv:
        msg = f"Already ran today — queued {wp_last.get('queued', 0)}"
        print(f"SKIP: wp_scan already ran today ({wp_last_date}).", flush=True)
        skill_skipped("wp-comment-handler", msg)
        return

    skill_started("wp-comment-handler", "Scanning pending WordPress comments")

    queue = load_json(QUEUE_FILE, [])
    trashed = 0
    queued = 0
    skipped_dup = 0
    skipped_stale = 0
    external_pings = 0  # surfaced for manual review — not queued, not trashed
    wp_base = os.environ["WP_URL"]

    try:
        with wp_client() as client:
            comments = fetch_pending_comments(client)
            # Break out counts per type so the operator can spot runaway
            # pingback accumulation at a glance.
            by_type: dict[str, int] = {}
            for c in comments:
                by_type[c.get("type", "comment")] = by_type.get(c.get("type", "comment"), 0) + 1
            breakdown = ", ".join(f"{v} {k}" for k, v in sorted(by_type.items())) or "none"
            print(f"Pending moderation items: {len(comments)} ({breakdown})\n", flush=True)

            # Cache post lookups — multiple comments on the same post is common.
            post_cache: dict[int, tuple[str, str]] = {}

            for idx, c in enumerate(comments, 1):
                comment_id = str(c["id"])
                ctype = c.get("type", "comment")
                author = c.get("author_name", "anonymous")
                author_url = c.get("author_url", "")
                body = strip_html(c.get("content", {}).get("rendered", ""))
                post_id = int(c.get("post", 0))
                date_iso = c.get("date_gmt", "")

                log_progress(idx, len(comments), f"{ctype} #{comment_id} by {author[:40]}")

                if is_duplicate("wordpress", comment_id):
                    print(f"    skip: already processed", flush=True)
                    skipped_dup += 1
                    continue

                # Self-pingbacks: our own posts linking to each other. Trash
                # immediately — the internal link already exists, these just
                # pollute the queue.
                if is_self_pingback(c, wp_base):
                    print(f"    TRASH: self-pingback from {author_url[:60]}", flush=True)
                    if not dry_run:
                        ok = trash_comment(client, int(comment_id))
                        if ok:
                            mark_engaged(
                                "wordpress", comment_id, "trash",
                                "self-pingback", status="spam",
                            )
                            trashed += 1
                        else:
                            log_error(f"WP_TRASH_FAILED: comment_id={comment_id}")
                    continue

                # External pingbacks/trackbacks: potential backlink signal
                # worth a human look, but we don't auto-reply to a pingback.
                # Surface it here and leave it pending in WP admin.
                if ctype in ("pingback", "trackback"):
                    print(
                        f"    EXTERNAL {ctype}: {author_url[:80]} "
                        f"— left pending for manual review",
                        flush=True,
                    )
                    external_pings += 1
                    continue

                # Stale check — don't reply to month-old comments.
                try:
                    age_days = (date.today() - datetime.fromisoformat(date_iso).date()).days
                    if age_days > MAX_COMMENT_AGE_DAYS:
                        print(f"    skip: {age_days}d old (>{MAX_COMMENT_AGE_DAYS})", flush=True)
                        skipped_stale += 1
                        continue
                except Exception:
                    pass

                is_spam, reason = is_obvious_spam(body, author_url)
                if is_spam:
                    print(f"    TRASH: {reason}", flush=True)
                    if not dry_run:
                        ok = trash_comment(client, int(comment_id))
                        if ok:
                            mark_engaged("wordpress", comment_id, "trash", "spam", status="spam")
                            trashed += 1
                        else:
                            log_error(f"WP_TRASH_FAILED: comment_id={comment_id}")
                    continue

                # Look up parent post for context (title in the Telegram approval).
                if post_id not in post_cache:
                    post_cache[post_id] = fetch_post_title(client, post_id)
                post_title, post_url = post_cache[post_id]

                # Score for context — doesn't gate. Moderation always reviews.
                score = score_relevance(body)

                candidate = {
                    "platform": "wordpress",
                    "post_url": post_url,
                    "post_id": comment_id,         # dedup key = comment id
                    "parent_post_id": post_id,      # WP post the comment lives on
                    "parent_post_title": post_title,
                    "post_text": body[:600],
                    "author": author,
                    "author_email": c.get("author_email", ""),
                    "category": "general",
                    "relevance_score": score,
                    "queued_at": datetime.now(UTC).isoformat(),
                    "status": "pending",
                    # Always require approval for WP replies until the flow is
                    # battle-tested. Matches the IG policy.
                    "requires_approval": True,
                }

                if dry_run:
                    print(f"    DRY: would queue (score={score}, post={post_title!r})", flush=True)
                    continue

                queue.append(candidate)
                queued += 1
                print(f"    QUEUED (score={score}, post={post_title[:50]!r})", flush=True)
    except KeyError as e:
        msg = f"Missing WP env var: {e}. Add to .claude/settings.local.json."
        print(f"ABORT: {msg}", flush=True)
        log_error(f"WP_SCAN_CONFIG_ERROR: {msg}")
        skill_finished("wp-comment-handler", msg)
        return
    except Exception as e:
        log_error(f"WP_SCAN_FAILED: {e}")
        skill_finished("wp-comment-handler", f"Error: {e}")
        raise

    if not dry_run:
        save_json(QUEUE_FILE, queue)
        last_run["wp_scan"] = {
            "last_run_at": datetime.now(UTC).isoformat(),
            "queued": queued,
            "trashed": trashed,
            "status": "success",
        }
        save_json(LAST_RUN_FILE, last_run)

    summary = (
        f"Queued: {queued} | Trashed: {trashed} | "
        f"External pings: {external_pings} | "
        f"Dedup: {skipped_dup} | Stale: {skipped_stale}"
    )
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished("wp-comment-handler", summary)


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
