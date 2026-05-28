"""Playwright-backed implementation of the `FbSession` protocol.

Delegates browser lifecycle to `lib.sessions.browser.BrowserSession`
so launch/viewport/user-agent/cookie-persist logic stays in one place.
This class only adds the FB-specific framing: pinning the storage
path passed by the brand-aware factory and exposing a cheap
authentication probe used by callers to short-circuit when cookies
are obviously missing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from lib.sessions.browser import BrowserSession, BrowserSessionConfig

if TYPE_CHECKING:
    from playwright.sync_api import Page


class PlaywrightFbSession:
    """Concrete `FbSession` driven by Playwright + chromium.

    Args:
        storage_path: Path to the persisted FB cookie/storage JSON.
            Read on enter (if exists), written on exit.
        headless: Run without a visible window. Callers usually pass
            the resolved value from `lib.local_env.get_runtime_headless`.

    Attributes:
        storage_path: Same as the constructor argument — exposed so
            callers (and the `FbSession` protocol) can introspect.
    """

    def __init__(self, *, storage_path: Path, headless: bool) -> None:
        self.storage_path: Path = storage_path
        self._headless: bool = headless

    @contextmanager
    def page(self) -> Iterator["Page"]:
        """Open a Playwright `Page` bound to this session's cookies.

        Yields:
            A ready `playwright.sync_api.Page`. Caller navigates from
            there. On exit, cookies are persisted back to
            `self.storage_path`.
        """
        config = BrowserSessionConfig(
            storage_state_path=self.storage_path,
            headless=self._headless,
        )
        with BrowserSession(config) as page:
            yield page

    def is_authenticated(self) -> bool:
        """Return True if the cookie file exists and is non-trivial.

        A `> 2` byte threshold rejects empty JSON files (`{}`, `[]`)
        that Playwright may write when a context is torn down before
        any cookies are set.
        """
        return self.storage_path.exists() and self.storage_path.stat().st_size > 2
