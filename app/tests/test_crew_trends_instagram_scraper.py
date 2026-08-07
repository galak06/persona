"""Tests for lib.crew.trends.instagram_scraper -- specifically the
`page.goto(...)` failure-handling log line. Split out of test_crew_trends.py
purely for file-size discipline (see that file's docstring).

`fetch_instagram_trends_text` has no constructor-injected seam for its
Playwright `Page`/navigation logic (unlike `insert_idea_fn`/
`trends_execute_fn` elsewhere in this crew package) -- it builds
`BrowserSession(config)` directly inside the function body. It also wasn't
designed with one, since every real call site is meant to hit a live
browser. It DOES, however, import `BrowserSession` by name at module scope
(`from lib.sessions.browser import BrowserSession, ...`), so monkeypatching
that imported name with a plain fake context manager -- yielding a fake
`Page` whose `goto` raises -- exercises the real control flow (log the new
`crew_trends_instagram_goto_stalled` warning, then fall back to
`wait_for_load_state`, then still scrape) without needing a real browser or
network access. This is the one targeted test this specific log line gets;
no other test in this file (or test_crew_trends.py) calls
`fetch_instagram_trends_text` for real.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


class _FakePage:
    """Minimal stand-in for `playwright.sync_api.Page` -- only the methods
    `fetch_instagram_trends_text` actually calls."""

    def __init__(self, *, inner_text: str) -> None:
        self._inner_text = inner_text
        self.wait_for_load_state_calls: list[tuple[str, int]] = []

    def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
        raise TimeoutError("stalled on domcontentloaded")

    def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.wait_for_load_state_calls.append((state, timeout))

    def wait_for_timeout(self, _ms: int) -> None:
        pass

    def evaluate(self, _script: str) -> None:
        pass

    def inner_text(self, _selector: str) -> str:
        return self._inner_text


class _FakeBrowserSession:
    """Stand-in for `lib.sessions.browser.BrowserSession` -- constructed
    with the real `BrowserSessionConfig` (ignored) and used as a context
    manager exactly like the real thing, but yields a `_FakePage` instead
    of launching Chromium."""

    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def __call__(self, _config: Any) -> _FakeBrowserSession:
        return self

    def __enter__(self) -> _FakePage:
        return self._page

    def __exit__(self, *_args: object) -> None:
        return None


def test_fetch_instagram_trends_text_logs_goto_stalled_and_still_scrapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`page.goto(...)` raising must log `crew_trends_instagram_goto_stalled`
    with the error BEFORE falling back to `wait_for_load_state`, and control
    flow must still proceed to scrape (not abort) -- the exact behavior this
    fix added."""
    import lib.crew.trends.instagram_scraper as instagram_scraper_module

    # `_SCROLL_PASSES` real `time.sleep(_SCROLL_WAIT_S)` calls would add
    # several real seconds to this test; the fake page's own methods are
    # already no-ops, so only the module-level `time.sleep` needs stubbing.
    monkeypatch.setattr(instagram_scraper_module.time, "sleep", lambda _seconds: None)

    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        instagram_scraper_module.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )

    fake_page = _FakePage(inner_text="  Trending: cozy autumn dog walks  \n\n  more trend text  ")
    monkeypatch.setattr(
        instagram_scraper_module, "BrowserSession", _FakeBrowserSession(fake_page)
    )

    result = instagram_scraper_module.fetch_instagram_trends_text(tmp_path)

    # Falls back to wait_for_load_state and still produces a scrape result --
    # control flow is unchanged by the new log line.
    assert fake_page.wait_for_load_state_calls == [("networkidle", 15_000)]
    assert result is not None
    assert "Trending: cozy autumn dog walks" in result
    assert "more trend text" in result

    goto_stalled = [
        (event, kwargs) for event, kwargs in warnings if event == "crew_trends_instagram_goto_stalled"
    ]
    assert len(goto_stalled) == 1
    assert "stalled on domcontentloaded" in goto_stalled[0][1]["error"]
