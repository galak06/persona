"""InstagramHashtagAdapter — outbound engagement on IG hashtag pages.

Implements OutboundAdapter (plus the optional SupportsComment capability).
Owns hashtag iteration, post detail extraction, the inline like + comment
actions, and the IG-specific pre-filters (competitor, own-account, age) +
score adjustment. The Playwright session lifecycle lives in
instagram_session.py; DOM constants in instagram_dom.py; text parsing in
instagram_parsing.py.

Scanner orchestration (loop over sources, score, draft, comment) lives in
lib/engagement/pipeline.py; scripts/ig_scan.py is the thin wrapper.
"""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from lib.engagement.adapters.instagram_dom import (
    CLICK_LIKE_JS,
    COMPETITOR_ACCOUNTS,
    EXTRACT_HASHTAG_POSTS_JS,
    EXTRACT_POST_DETAILS_JS,
    OWN_ACCOUNT,
)
from lib.engagement.adapters.instagram_parsing import (
    parse_author_from_caption,
    parse_comment_count,
    parse_like_count,
    parse_post_age_weeks,
    should_scan_today,
)
from lib.engagement.adapters.instagram_session import InstagramSession
from lib.engagement.post import Post
from lib.engagement.result import CommentResult, LikeResult
from lib.ig.comment_post import post_comment_ig

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _IGSource:
    """A single IG hashtag scheduled to scan today."""

    id: str
    name: str
    url: str
    category: str = "general"


class InstagramHashtagAdapter:
    """OutboundAdapter for Instagram hashtag scanning + inline likes/comments."""

    platform: str = "instagram"

    def __init__(self, config: dict[str, object]) -> None:
        """Construct from a config dict containing paths + tuning knobs.

        Expected keys:
            session_file: Path to the Playwright storage_state JSON
            hashtag_file: Path to instagram_accounts.csv

        Playwright headless mode is sourced from the brand overlay via
        `get_runtime_headless()` (see lib/local_env.py), not from `config`.
        """
        self._config = config
        self._hashtag_file: Path = Path(str(config["hashtag_file"]))
        self._session = InstagramSession(Path(str(config["session_file"])))

    def session(self) -> AbstractContextManager[None]:
        """Open + tear down the authenticated Playwright session."""
        return self._session.open()

    def list_sources(self) -> list[_IGSource]:
        """Read instagram_accounts.csv and filter to rows scheduled for today."""
        today = date.today()
        sources: list[_IGSource] = []
        with self._hashtag_file.open() as f:
            for row in csv.DictReader(f):
                freq = (row.get("scan_frequency") or "").strip()
                if not should_scan_today(freq, today):
                    continue
                tag = (row.get("hashtag") or "").strip().lstrip("#")
                if not tag:
                    continue
                sources.append(
                    _IGSource(
                        id=tag,
                        name=tag,
                        url=f"https://www.instagram.com/explore/tags/{tag}/",
                        category=(row.get("category") or "general").strip(),
                    )
                )
        return sources

    def iterate_posts(self, source: _IGSource) -> Iterator[Post]:
        """Yield Post objects for one hashtag.

        Lifted from ig_scan.py:430-529. Navigates to the hashtag page, scrolls
        twice, extracts up to 15 post links, then opens each post and extracts
        caption/author/like/comment text.
        """
        page = self._session.require_page()
        try:
            page.goto(source.url, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            # Instagram hashtag pages sometimes stall on DOMContentLoaded due to
            # redirect chains; fall back to waiting for the network to settle.
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
        time.sleep(4)

        body_text = page.inner_text("body")[:500].lower()
        if "sorry" in body_text and "page isn't available" in body_text:
            _log.warning("instagram_hashtag_blocked", extra={"hashtag": source.name})
            return

        self._session.dismiss_overlays()
        for _ in range(2):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

        post_links: list[dict[str, str]] = page.evaluate(EXTRACT_HASHTAG_POSTS_JS) or []
        for link in post_links:
            post_url = link.get("url", "")
            post_id = link.get("post_id", "")
            if not post_url or not post_id:
                continue
            try:
                page.goto(post_url, wait_until="domcontentloaded", timeout=60_000)
                time.sleep(3)
            except Exception:
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
            self._session.dismiss_overlays()
            try:
                details = page.evaluate(EXTRACT_POST_DETAILS_JS) or {}
            except Exception:
                details = {"caption": "", "like_text": "", "comment_text": "", "author": ""}

            caption = (details.get("caption") or "")[:800]
            author = (details.get("author") or "").strip().strip("/").lower()
            if not author:
                author = parse_author_from_caption(caption)
            like_count = parse_like_count(details.get("like_text", ""))
            comment_count = parse_comment_count(details.get("comment_text", ""))
            weeks_old = parse_post_age_weeks(caption)

            yield Post(
                platform=self.platform,
                post_id=post_id,
                post_url=post_url,
                text=caption,
                author=author or None,
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                platform_extra={
                    "like_count": like_count,
                    "comment_count": comment_count,
                    "weeks_old": weeks_old,
                    "category": source.category,
                },
            )

    def pre_filter(self, post: Post) -> str | None:
        """Return a rejection reason, or None to accept. Mirrors ig_scan.py:531-546."""
        author = (post.author or "").lower()
        caption_lower = post.text.lower()
        if author == OWN_ACCOUNT or caption_lower.startswith(OWN_ACCOUNT):
            return "own_account"
        weeks_old_raw = post.platform_extra.get("weeks_old", 0) or 0
        weeks_old = float(weeks_old_raw)  # type: ignore[arg-type]
        if weeks_old > 2:
            return "too_old"
        if author in COMPETITOR_ACCOUNTS:
            return "competitor"
        return None

    def adjust_score(self, post: Post, base: float) -> float:
        """IG-specific score adjustment. Verbatim thresholds from ig_score()."""
        like_count_raw = post.platform_extra.get("like_count", 0) or 0
        like_count = int(like_count_raw)  # type: ignore[call-overload]
        score = base
        if like_count < 500:
            score += 0.15
        if like_count > 5000:
            score -= 0.20
        return round(score, 2)

    def like(self, post: Post) -> LikeResult:
        """Click the like button on `post`. Page navigates to the post URL if needed."""
        page = self._session.require_page()
        try:
            if (page.url or "") != post.post_url:
                page.goto(post.post_url, wait_until="domcontentloaded")
                time.sleep(3)
                self._session.dismiss_overlays()
            result = page.evaluate(CLICK_LIKE_JS)
        except Exception as exc:
            return LikeResult.failed(f"exception:{exc.__class__.__name__}")
        if result == "liked":
            return LikeResult.ok()
        if result == "already_liked":
            return LikeResult.skipped("already_liked")
        return LikeResult.failed(f"button:{result}")

    def comment(self, post: Post, text: str) -> CommentResult:
        """Submit `text` as a comment on `post` (satisfies `SupportsComment`).

        Delegates the DOM walk to `lib.ig.comment_post.post_comment_ig`, the
        same posting path `scripts/ig_comment.py` uses — this adapter only
        supplies the authenticated page and maps the bool to a CommentResult.

        In single-pass mode `like()` has just left the page on this post's
        URL, so the navigation is skipped when we are already there (same
        guard `like()` uses); a full page load per comment is pure cost.
        """
        page = self._session.require_page()
        try:
            posted = post_comment_ig(
                page,
                post.post_url,
                text,
                skip_navigation=(page.url or "") == post.post_url,
            )
        except Exception as exc:
            return CommentResult.failed(f"exception:{exc.__class__.__name__}")
        if posted:
            return CommentResult.ok()
        return CommentResult.failed("comment_box_not_found")
