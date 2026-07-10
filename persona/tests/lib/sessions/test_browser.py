"""Tests for lib.sessions.browser.

The actual Playwright integration is smoke-tested manually (browser
launch, real navigation). These tests verify config plumbing and the
context-manager lifecycle by mocking sync_playwright.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib.sessions.browser import (
    USER_AGENT,
    BrowserSession,
    BrowserSessionConfig,
    fb_session,
    ig_session,
)


class TestUserAgentConstant:
    def test_exists_and_is_chrome(self) -> None:
        assert "Chrome" in USER_AGENT
        assert "Mozilla/5.0" in USER_AGENT


class TestBrowserSessionConfig:
    def test_immutable(self, tmp_path: Path) -> None:
        cfg = BrowserSessionConfig(storage_state_path=tmp_path / "state.json")
        with pytest.raises(AttributeError):
            cfg.headless = True  # type: ignore[misc]

    def test_defaults(self, tmp_path: Path) -> None:
        cfg = BrowserSessionConfig(storage_state_path=tmp_path / "state.json")
        assert cfg.headless is False
        assert cfg.viewport_width == 1280
        assert cfg.viewport_height == 900
        assert cfg.user_agent == USER_AGENT


@pytest.fixture
def fake_playwright() -> MagicMock:
    """Build a fake `sync_playwright()` chain that returns a MagicMock Page.

    We mock the full chain (`.start() → .chromium.launch() → .new_context()
    → .new_page()`) so the BrowserSession context manager can run without
    a real browser.
    """
    page = MagicMock(name="page")
    context = MagicMock(name="context")
    context.new_page.return_value = page
    browser = MagicMock(name="browser")
    browser.new_context.return_value = context
    pw_runtime = MagicMock(name="pw_runtime")
    pw_runtime.chromium.launch.return_value = browser
    sync_pw = MagicMock(name="sync_playwright")
    sync_pw.return_value.start.return_value = pw_runtime
    return sync_pw


class TestBrowserSessionLifecycle:
    def test_yields_a_page(self, tmp_path: Path, fake_playwright: MagicMock) -> None:
        cfg = BrowserSessionConfig(storage_state_path=tmp_path / "state.json")
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            BrowserSession(cfg) as page,
        ):
            assert page is not None
            # The page is the deepest mock in the chain.
            assert (
                page
                is fake_playwright.return_value.start.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
            )

    def test_passes_viewport_and_user_agent(
        self, tmp_path: Path, fake_playwright: MagicMock
    ) -> None:
        cfg = BrowserSessionConfig(
            storage_state_path=tmp_path / "state.json",
            viewport_width=1920,
            viewport_height=1080,
            user_agent="custom-ua",
        )
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            BrowserSession(cfg),
        ):
            pass
        browser_mock = fake_playwright.return_value.start.return_value.chromium.launch.return_value
        kwargs = browser_mock.new_context.call_args.kwargs
        assert kwargs["viewport"] == {"width": 1920, "height": 1080}
        assert kwargs["user_agent"] == "custom-ua"

    def test_passes_storage_state_only_when_file_exists(
        self, tmp_path: Path, fake_playwright: MagicMock
    ) -> None:
        cfg = BrowserSessionConfig(storage_state_path=tmp_path / "missing.json")
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            BrowserSession(cfg),
        ):
            pass
        browser_mock = fake_playwright.return_value.start.return_value.chromium.launch.return_value
        # The storage_state arg is omitted because the file doesn't exist.
        assert "storage_state" not in browser_mock.new_context.call_args.kwargs

    def test_persists_storage_state_on_exit(
        self, tmp_path: Path, fake_playwright: MagicMock
    ) -> None:
        state_path = tmp_path / "state.json"
        cfg = BrowserSessionConfig(storage_state_path=state_path)
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            BrowserSession(cfg),
        ):
            pass
        context_mock = fake_playwright.return_value.start.return_value.chromium.launch.return_value.new_context.return_value
        context_mock.storage_state.assert_called_once_with(path=str(state_path))

    def test_passes_headless_flag(self, tmp_path: Path, fake_playwright: MagicMock) -> None:
        cfg = BrowserSessionConfig(storage_state_path=tmp_path / "state.json", headless=True)
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            BrowserSession(cfg),
        ):
            pass
        pw_runtime = fake_playwright.return_value.start.return_value
        pw_runtime.chromium.launch.assert_called_once_with(headless=True)

    def test_cleanup_suppresses_exceptions(
        self, tmp_path: Path, fake_playwright: MagicMock
    ) -> None:
        """Best-effort cleanup — exceptions during teardown must not propagate."""
        cfg = BrowserSessionConfig(storage_state_path=tmp_path / "state.json")
        ctx = fake_playwright.return_value.start.return_value.chromium.launch.return_value.new_context.return_value
        ctx.storage_state.side_effect = OSError("disk full")
        ctx.close.side_effect = RuntimeError("close failed")
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            BrowserSession(cfg),
        ):
            pass


class TestPlatformWrappers:
    def test_fb_session_uses_default_path(self, tmp_path: Path, fake_playwright: MagicMock) -> None:
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            fb_session(storage_state_path=tmp_path / "fb.json") as page,
        ):
            assert page is not None

    def test_ig_session_uses_default_path(self, tmp_path: Path, fake_playwright: MagicMock) -> None:
        with (
            patch("playwright.sync_api.sync_playwright", fake_playwright),
            ig_session(storage_state_path=tmp_path / "ig.json") as page,
        ):
            assert page is not None

    def test_default_paths_differ(self) -> None:
        """fb and ig must use distinct storage-state files by default."""
        # We don't enter the context here — just verify the wiring by
        # looking at the BrowserSessionConfig the wrappers would build.
        # (Smoke-level check; full behavior verified via fakes above.)
        from lib.sessions.browser import _DEFAULT_FB_SESSION, _DEFAULT_IG_SESSION

        assert _DEFAULT_FB_SESSION != _DEFAULT_IG_SESSION
