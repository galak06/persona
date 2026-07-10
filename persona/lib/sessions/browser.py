"""Playwright browser session managers — fb_session, ig_session.

Replaces 14 inline `sync_playwright()` + `chromium.launch()` +
`new_context(storage_state=..., viewport=..., user_agent=...)` +
`new_page()` reimplementations across `scripts/*.py`. Single source of
truth for the User-Agent string and viewport defaults.

Two layers:
    - `BrowserSession` — generic context manager parameterized by config
    - `fb_session()` / `ig_session()` — platform-specific wrappers that
      bind the right session-state file and persist cookies on exit

Each session yields a `playwright.sync_api.Page` ready to navigate.
On exit, the storage_state is written back so cookie/auth refreshes
persist across runs.

Note: Playwright import is deferred until first use so test environments
without Playwright (or CI lacking the browser binary) can import this
module without erroring. Production runners install Playwright; tests
that exercise these helpers are marked `@pytest.mark.browser` and skip
in CI by default.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        ViewportSize,
    )

USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
"""Shared User-Agent string. Kept in one place so we don't drift to
~14 different copies across scripts/."""

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _default_brand_dir() -> Path:
    """Resolve BRAND_DIR default via lib.config (authoritative), falling
    back to the correct repo-root-relative path if the import fails."""
    try:
        from lib.config import default_brand_dir as _resolve
        return _resolve()
    except Exception:
        return _PROJECT_ROOT.parent / "persona"


_BRAND_DIR = _default_brand_dir()
_DEFAULT_FB_SESSION = _BRAND_DIR / "state/facebook_session.json"
_DEFAULT_IG_SESSION = _BRAND_DIR / "state/instagram_session.json"


def _default_tiktok_session() -> Path:
    """Resolve TikTok session path via settings (authoritative) or fallback."""
    try:
        from lib.config import settings
        return Path(settings.paths.tiktok_session)
    except Exception:
        return _BRAND_DIR / "state/tiktok_session.json"


_DEFAULT_TIKTOK_SESSION = _default_tiktok_session()


@dataclass(frozen=True)
class BrowserSessionConfig:
    """Configuration for a Playwright session.

    Attributes:
        storage_state_path: Path to the persisted cookie/storage JSON.
            Read on enter (if exists), written on exit.
        headless: Run without a visible window. Default False because
            FB/IG flag headless agents more aggressively.
        viewport_width: Browser width in pixels.
        viewport_height: Browser height in pixels.
        user_agent: User-Agent string.
    """

    storage_state_path: Path
    headless: bool = False
    viewport_width: int = 1280
    viewport_height: int = 900
    user_agent: str = USER_AGENT


class BrowserSession:
    """Generic Playwright session context manager.

    Lifecycle on `__enter__`:
        1. Start `sync_playwright`
        2. Launch chromium with the config's headless flag
        3. Create a context with viewport, user agent, and (if the
           storage_state file exists) the persisted cookies
        4. Create one page and return it

    Lifecycle on `__exit__`:
        1. Persist the context's storage_state back to disk
        2. Close context, browser, and the playwright runtime
        3. Best-effort — exceptions during cleanup are suppressed so
           the original exception (if any) propagates cleanly

    Args:
        config: A `BrowserSessionConfig`.
    """

    def __init__(self, config: BrowserSessionConfig) -> None:
        self._config: BrowserSessionConfig = config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def __enter__(self) -> Page:
        # Deferred import so the module is importable without Playwright.
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        self._playwright = playwright
        browser = playwright.chromium.launch(headless=self._config.headless)
        self._browser = browser
        viewport: ViewportSize = {
            "width": self._config.viewport_width,
            "height": self._config.viewport_height,
        }
        # Branch on storage_state existence so we can pass typed kwargs
        # rather than dict-spread (which mypy strict rejects against
        # Playwright's narrowly-typed signature).
        if self._config.storage_state_path.exists():
            context = browser.new_context(
                viewport=viewport,
                user_agent=self._config.user_agent,
                storage_state=str(self._config.storage_state_path),
            )
        else:
            context = browser.new_context(
                viewport=viewport,
                user_agent=self._config.user_agent,
            )
        self._context = context
        page = context.new_page()
        self._page = page
        return page

    def __exit__(self, *_args: object) -> None:
        # Persist cookies/storage back to disk before tearing down.
        # Best-effort cleanup — exceptions during teardown are suppressed
        # so the original (in-block) exception (if any) propagates cleanly.
        if self._context is not None:
            try:
                self._config.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                self._context.storage_state(path=str(self._config.storage_state_path))
            except Exception:
                pass
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None


@contextmanager
def fb_session(
    *,
    headless: bool | None = None,
    storage_state_path: Path | None = None,
) -> Iterator[Page]:
    """Open a Playwright session against the persisted Facebook cookies.

    Args:
        headless: Run headless. Default None — consult the brand overlay
            via `get_runtime_headless()` (production-safe True when no
            overlay is present; False in local dev).
        storage_state_path: Override session-state file (tests).
            Default `.claude/state/facebook_session.json`.

    Yields:
        A ready `playwright.sync_api.Page`. Caller navigates from there.
    """
    from lib.local_env import get_runtime_headless

    config = BrowserSessionConfig(
        storage_state_path=storage_state_path or _DEFAULT_FB_SESSION,
        headless=get_runtime_headless() if headless is None else headless,
    )
    with BrowserSession(config) as page:
        yield page


@contextmanager
def ig_session(
    *,
    headless: bool | None = None,
    storage_state_path: Path | None = None,
) -> Iterator[Page]:
    """Open a Playwright session against the persisted Instagram cookies.

    Same shape as `fb_session`, different storage-state file.
    """
    from lib.local_env import get_runtime_headless

    config = BrowserSessionConfig(
        storage_state_path=storage_state_path or _DEFAULT_IG_SESSION,
        headless=get_runtime_headless() if headless is None else headless,
    )
    with BrowserSession(config) as page:
        yield page


@contextmanager
def tiktok_session(
    *,
    headless: bool | None = None,
    storage_state_path: Path | None = None,
) -> Iterator[Page]:
    """Open a Playwright session against the persisted TikTok cookies.

    Same shape as `fb_session` / `ig_session`, different storage-state file.
    TikTok's bot-detection is aggressive — defaults to headless=False and
    applies playwright-stealth fingerprint patches to avoid CAPTCHA triggers.
    """
    from lib.local_env import get_runtime_headless

    config = BrowserSessionConfig(
        storage_state_path=storage_state_path or _DEFAULT_TIKTOK_SESSION,
        headless=get_runtime_headless() if headless is None else headless,
    )
    with BrowserSession(config) as page:
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
        except ImportError:
            pass
        yield page
