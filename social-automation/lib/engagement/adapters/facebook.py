"""FacebookGroupAdapter — outbound engagement on FB groups.

Implements OutboundAdapter. Owns the Playwright session, page-profile switch
(act as the brand Page), group iteration, post extraction, and inline liking
of Group posts (executed as the active Page profile).

This module is a refactor-by-extraction of scripts/fb_scan.py: same selectors,
same scroll counts, same login validation, same page-profile switch sequence.
DOM constants live in facebook_dom.py; page-level helpers in _facebook_helpers.py.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from playwright.sync_api import Browser, BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from lib.engagement.adapter import Source
from lib.engagement.adapters._facebook_helpers import (
    click_see_more,
    dismiss_overlays,
    extract_post_id,
    switch_to_page_profile,
)
from lib.engagement.adapters.facebook_dom import (
    ARTICLE_COUNT_JS,
    CLICK_LIKE_JS,
    EXTRACT_POSTS_JS,
    STORY_MESSAGE_COUNT_JS,
    USER_AGENT,
)
from lib.engagement.post import Post
from lib.engagement.result import LikeResult
from lib.local_env import get_runtime_headless

_PW_ERRORS: tuple[type[BaseException], ...] = (PlaywrightError, PlaywrightTimeoutError)


@dataclass
class _FBSource:
    """Lightweight Source implementation for FB groups.

    Not frozen — the Source Protocol declares its fields as settable variables,
    and a frozen dataclass would make them read-only (mypy incompatibility).
    """

    id: str
    name: str
    url: str
    category: str = ""


_CATEGORY_MAP = {
    "\U0001f356": "food",      # food
    "\U0001f3c3": "gps",       # runner
    "\U0001f3e5": "health",    # hospital
    "\U0001f3be": "training",  # tennis ball
    "\U0001f43e": "general",   # paw prints
}


def _category_from_source(source: Source) -> str:
    """Map a group category string (may contain an emoji marker) to canonical name."""
    raw = getattr(source, "category", "") or ""
    for emoji, cat in _CATEGORY_MAP.items():
        if emoji in raw:
            return cat
    return "food"


class FacebookGroupAdapter:
    """OutboundAdapter for Facebook Groups (browser-driven via Playwright)."""

    platform = "facebook"

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        paths = cast(Mapping[str, object], config.get("paths", {}) or {})
        self._session_file: Path = Path(str(paths.get("facebook_session", "")))
        self._tracker_path: Path = Path(str(paths.get("groups_tracker", "")))

        channels = cast(Mapping[str, object], config.get("social_channels", {}) or {})
        fb_cfg = cast(Mapping[str, object], channels.get("facebook", {}) or {})
        self._page_name: str = str(fb_cfg.get("page_name", "DogFoodAndFun"))

        scanning = cast(Mapping[str, object], config.get("scanning", {}) or {})
        scan_cfg = cast(Mapping[str, object], scanning.get("facebook", {}) or {})
        self._scroll_count: int = int(cast(int, scan_cfg.get("scroll_count", 5)))
        self._scroll_pause: float = float(cast(float, scan_cfg.get("scroll_pause_seconds", 2)))
        self._post_load_pause: float = float(
            cast(float, scan_cfg.get("post_load_pause_seconds", 4))
        )

        # Playwright runtime state — populated inside session()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # ---------- session lifecycle ----------

    @contextmanager
    def session(self) -> Iterator[None]:
        """Launch Playwright, restore cookies, validate login, switch to Page profile."""
        from playwright.sync_api import sync_playwright

        if not self._session_file.exists():
            raise RuntimeError(
                f"No saved Facebook session at {self._session_file}. Run fb_login.py first."
            )

        pw_ctx = sync_playwright().start()
        self._playwright = pw_ctx
        try:
            self._browser = pw_ctx.chromium.launch(headless=get_runtime_headless())
            self._context = self._new_context()
            self._page = self._context.new_page()

            # Login validation
            self._page.goto("https://www.facebook.com", wait_until="domcontentloaded")
            time.sleep(3)
            if "login" in self._page.url.lower():
                raise RuntimeError("SESSION_EXPIRED: Facebook login required")

            switch_to_page_profile(self._page, self._page_name)
            yield
        finally:
            self._teardown(pw_ctx)

    def _new_context(self) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("browser not initialized")
        return self._browser.new_context(
            storage_state=str(self._session_file),
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )

    def _teardown(self, pw_ctx: Playwright) -> None:
        try:
            if self._context is not None:
                self._context.storage_state(path=str(self._session_file))
        except _PW_ERRORS:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except _PW_ERRORS:
            pass
        try:
            pw_ctx.stop()
        except _PW_ERRORS:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    # ---------- OutboundAdapter Protocol methods ----------

    def list_sources(self) -> list[Source]:
        """Joined groups from the tracker (status='joined', self-promo allowed)."""
        if not self._tracker_path.exists():
            raise FileNotFoundError(f"groups tracker not found at {self._tracker_path}")

        with self._tracker_path.open() as f:
            records = json.load(f)

        sources: list[Source] = []
        for row in records:
            status = str(row.get("status", "")).strip().lower()
            if status != "joined":
                continue
            if str(row.get("self_promo_allowed", "")).strip().lower() == "no":
                continue
            url = str(row.get("group_url", "")).strip()
            if not url or "/groups/search" in url:
                continue
            group_id = str(row.get("group_id", "")) or url.rstrip("/").split("/")[-1]
            sources.append(
                _FBSource(
                    id=group_id,
                    name=str(row.get("group_name", "")),
                    url=url,
                    category=str(row.get("category", "")),
                )
            )
        return sources

    def iterate_posts(self, source: Source) -> Iterator[Post]:
        """Navigate to source.url, scroll, extract posts, yield normalized Posts."""
        if self._page is None:
            raise RuntimeError("session() must be entered before iterate_posts()")

        page = self._page
        page.goto(source.url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(self._post_load_pause)
        dismiss_overlays(page)

        for _ in range(self._scroll_count):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(self._scroll_pause)
            dismiss_overlays(page)

        click_see_more(page)

        # Diagnostic counters — preserve fb_scan's debug output via return values.
        try:
            page.evaluate(STORY_MESSAGE_COUNT_JS)
            page.evaluate(ARTICLE_COUNT_JS)
        except _PW_ERRORS:
            pass

        raw_evaluated = page.evaluate(EXTRACT_POSTS_JS) or []
        raw_posts: list[Mapping[str, object]] = list(raw_evaluated)

        # Text-fallback: scrape body paragraphs if JS extraction came back empty.
        if not raw_posts:
            raw_posts = self._text_fallback(page)

        category = _category_from_source(source)
        for raw in raw_posts:
            post = self._build_post(raw, source, category)
            if post is not None:
                yield post

    def _text_fallback(self, page: Page) -> list[Mapping[str, object]]:
        try:
            body_text = page.inner_text("body")
        except _PW_ERRORS:
            return []
        if len(body_text) <= 500:
            return []
        paragraphs = [p.strip() for p in body_text.split("\n") if len(p.strip()) > 50]
        return [{"text": p, "url": "", "comment_count": 0} for p in paragraphs[:15]]

    def _build_post(
        self, raw: Mapping[str, object], source: Source, category: str
    ) -> Post | None:
        post_text: str = str(raw.get("text", ""))
        post_url: str = str(raw.get("url", "") or "")
        comment_count: int = int(cast(int, raw.get("comment_count", 0) or 0))

        if post_url:
            post_id = extract_post_id(post_url)
        else:
            # Hash text into a stable id; fall back to the group URL.
            post_id = hashlib.md5(
                post_text[:200].encode(), usedforsecurity=False
            ).hexdigest()[:16]
            post_url = source.url
        if not post_id:
            return None

        return Post(
            platform="facebook",
            post_id=post_id,
            post_url=post_url,
            text=post_text[:600],
            author=None,
            source_id=source.id,
            source_name=source.name,
            source_url=source.url,
            platform_extra={"comment_count": comment_count, "category": category},
        )

    def pre_filter(self, post: Post) -> str | None:
        """FB has no platform-level pre-filters today."""
        return None

    def adjust_score(self, post: Post, base: float) -> float:
        """FB does not apply platform-specific score adjustments."""
        return base

    def like(self, post: Post) -> LikeResult:
        """Click 👍 on a Group post as the active Page profile.

        Requires session() to have switched to the Page profile (done in
        __enter__). Idempotent: returns LikeResult.skipped("already_liked")
        if the Page already liked the post. Any Playwright / navigation
        failure maps to LikeResult.failed(reason) so the pipeline keeps
        scanning instead of crashing.
        """
        if self._page is None:
            return LikeResult.failed("no_active_session")
        page = self._page
        try:
            current_url = page.url or ""
            if post.post_url and post.post_url not in current_url:
                page.goto(post.post_url, wait_until="domcontentloaded")
                # Brief settle time — FB's like-button DOM appears asynchronously
                # after navigation. Mirrors InstagramHashtagAdapter.like().
                page.wait_for_timeout(1500)
                dismiss_overlays(page)
            result = page.evaluate(CLICK_LIKE_JS)
        except Exception as exc:
            return LikeResult.failed(f"playwright_error:{type(exc).__name__}")

        if not isinstance(result, dict):
            return LikeResult.failed("no_status")
        status = result.get("status")
        if status == "ok":
            return LikeResult.ok()
        if status == "already_liked":
            return LikeResult.skipped("already_liked")
        reason = str(result.get("reason", "unknown"))
        return LikeResult.failed(reason)
