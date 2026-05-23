"""InstagramHashtagAdapter — outbound engagement on IG hashtag pages.

Implements OutboundAdapter. Owns the Playwright session, hashtag iteration,
post detail extraction, the inline like action, and the IG-specific
pre-filters (competitor, own-account, age) + score adjustment.

Absorbs the former IG-like-helpers module (deleted during this refactor); its
constants, JS payloads, and helpers now live in instagram_dom.py + this file.

Scanner orchestration (loop over sources, score, draft, queue write) stays in
scripts/ig_scan.py until the next refactor wave.
"""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from lib.engagement.adapters.instagram_dom import (
    CLICK_LIKE_JS,
    COMPETITOR_ACCOUNTS,
    EXTRACT_HASHTAG_POSTS_JS,
    EXTRACT_POST_DETAILS_JS,
    OVERLAY_SELECTORS,
    OWN_ACCOUNT,
)
from lib.engagement.adapters.instagram_parsing import (
    parse_author_from_caption,
    parse_comment_count,
    parse_like_count,
    parse_post_age_weeks,
    should_scan_today,
)
from lib.engagement.post import Post
from lib.engagement.result import LikeResult
from lib.local_env import get_runtime_headless

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _IGSource:
    """A single IG hashtag scheduled to scan today."""

    id: str
    name: str
    url: str
    category: str = "general"


class InstagramHashtagAdapter:
    """OutboundAdapter for Instagram hashtag scanning + inline likes."""

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
        self._session_file: Path = Path(str(config["session_file"]))
        self._hashtag_file: Path = Path(str(config["hashtag_file"]))
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @contextmanager
    def session(self) -> Iterator[None]:
        """Open Playwright, restore IG cookies, validate login. Tear down on exit.

        Lifted from ig_scan.py:381-411. Raises RuntimeError if the session has
        expired so the scanner can log SESSION_EXPIRED + abort.
        """
        from playwright.sync_api import sync_playwright

        if not self._session_file.exists():
            raise RuntimeError(
                f"No saved Instagram session at {self._session_file}; "
                "run scripts/ig_login.py first."
            )

        playwright = sync_playwright().start()
        self._playwright = playwright
        try:
            browser = playwright.chromium.launch(headless=get_runtime_headless())
            self._browser = browser
            context = browser.new_context(
                storage_state=str(self._session_file),
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            self._context = context
            page = context.new_page()
            self._page = page
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
            time.sleep(4)
            url = (page.url or "").lower()
            if "login" in url or "accounts/login" in url:
                raise RuntimeError("SESSION_EXPIRED: Instagram login required")
            self._dismiss_overlays()
            time.sleep(2)
            yield
        finally:
            self._teardown_session()

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
        page = self._require_page()
        page.goto(source.url, wait_until="domcontentloaded")
        time.sleep(4)

        body_text = page.inner_text("body")[:500].lower()
        if "sorry" in body_text and "page isn't available" in body_text:
            _log.warning("instagram_hashtag_blocked", extra={"hashtag": source.name})
            return

        self._dismiss_overlays()
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
                page.goto(post_url, wait_until="domcontentloaded")
                time.sleep(3)
            except Exception:
                _log.exception("instagram_post_navigation_failed", extra={"post_id": post_id})
                continue
            self._dismiss_overlays()
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
        page = self._require_page()
        try:
            if (page.url or "") != post.post_url:
                page.goto(post.post_url, wait_until="domcontentloaded")
                time.sleep(3)
                self._dismiss_overlays()
            result = page.evaluate(CLICK_LIKE_JS)
        except Exception as exc:
            return LikeResult.failed(f"exception:{exc.__class__.__name__}")
        if result == "liked":
            return LikeResult.ok()
        if result == "already_liked":
            return LikeResult.skipped("already_liked")
        return LikeResult.failed(f"button:{result}")

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("InstagramHashtagAdapter: session() not active")
        return self._page

    def _teardown_session(self) -> None:
        """Persist cookies, close browser + playwright. Safe to call on partial init."""
        context = self._context
        if context is not None:
            try:
                context.storage_state(path=str(self._session_file))
            except Exception:
                _log.exception("instagram_session_save_failed")
        browser = self._browser
        if browser is not None:
            try:
                browser.close()
            except Exception:
                _log.debug("instagram_browser_close_failed", exc_info=True)
        playwright = self._playwright
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                _log.debug("instagram_playwright_stop_failed", exc_info=True)
        self._page = self._context = self._browser = self._playwright = None

    def _dismiss_overlays(self) -> None:
        """Click through Instagram popups (notifications, cookies, login prompts)."""
        page = self._page
        if page is None:
            return
        for sel in OVERLAY_SELECTORS:
            try:
                btn = page.locator(sel)
                if btn.count() > 0:
                    btn.first.click(timeout=2000)
                    time.sleep(1)
                    return
            except Exception:
                _log.debug("instagram_overlay_dismiss_failed", extra={"selector": sel})
                continue
