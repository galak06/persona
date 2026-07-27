"""Playwright session lifecycle for the Instagram adapter.

Owns browser launch, cookie restore, login validation, overlay dismissal and
teardown — everything about *having* an authenticated page, as opposed to
what `InstagramHashtagAdapter` does *with* one (iterate, score, like,
comment).

Split out of `instagram.py` to keep it under the 300-line cap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from lib.engagement.adapters.instagram_dom import OVERLAY_SELECTORS
from lib.local_env import get_runtime_headless

_log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class InstagramSession:
    """Holds the Playwright objects for one authenticated Instagram session."""

    def __init__(self, session_file: Path) -> None:
        self._session_file = session_file
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @contextmanager
    def open(self) -> Iterator[None]:
        """Open Playwright, restore IG cookies, validate login. Tear down on exit.

        Raises RuntimeError if the session has expired so the scanner can log
        SESSION_EXPIRED + abort.
        """
        from playwright.sync_api import sync_playwright

        if not self._session_file.exists():
            raise RuntimeError(
                f"No saved Instagram session at {self._session_file}; "
                "run scripts/ig_login.py first."
            )

        self._playwright = sync_playwright().start()
        try:
            self._launch()
            self._verify_logged_in()
            self.dismiss_overlays()
            time.sleep(2)
            yield
        finally:
            self.teardown()

    def require_page(self) -> Page:
        """The authenticated page, or RuntimeError if no session is active."""
        if self._page is None:
            raise RuntimeError("InstagramHashtagAdapter: session() not active")
        return self._page

    def _launch(self) -> None:
        """Start the browser with the saved storage state and open a page."""
        playwright = self._playwright
        assert playwright is not None
        self._browser = playwright.chromium.launch(headless=get_runtime_headless())
        self._context = self._browser.new_context(
            storage_state=str(self._session_file),
            viewport={"width": 1280, "height": 900},
            user_agent=_USER_AGENT,
        )
        self._page = self._context.new_page()

    def _verify_logged_in(self) -> None:
        """Load the IG home page and fail loudly if we were bounced to login."""
        page = self.require_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        time.sleep(4)
        url = (page.url or "").lower()
        if "login" in url or "accounts/login" in url:
            raise RuntimeError("SESSION_EXPIRED: Instagram login required")

    def teardown(self) -> None:
        """Persist cookies, close browser + playwright. Safe on partial init."""
        if self._context is not None:
            try:
                self._context.storage_state(path=str(self._session_file))
            except Exception:
                _log.exception("instagram_session_save_failed")
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                _log.debug("instagram_browser_close_failed", exc_info=True)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                _log.debug("instagram_playwright_stop_failed", exc_info=True)
        self._page = self._context = self._browser = self._playwright = None

    def dismiss_overlays(self) -> None:
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
