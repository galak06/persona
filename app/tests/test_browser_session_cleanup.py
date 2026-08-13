"""`BrowserSession.__enter__` must not leak Playwright when it fails partway.

`__exit__` only runs once `__enter__` RETURNS, so a raise between
`sync_playwright().start()` and the `return page` left the driver subprocess
and its event loop alive for the life of the process. That leak had teeth: a
still-running loop makes crewai refuse every later synchronous
`Crew.kickoff()`, so a missing Chromium binary in the API container turned the
content scout into a silent no-op rather than a skipped scrape.

No real browser is launched here -- `sync_playwright` is faked.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from lib.sessions.browser import BrowserSession, BrowserSessionConfig


class _FakePlaywright:
    """Records whether the driver was torn down."""

    def __init__(self, failure: Exception | None) -> None:
        self.stopped = False
        self._failure = failure
        self.chromium = types.SimpleNamespace(launch=self._launch)

    def _launch(self, **_kw: Any) -> Any:
        if self._failure is not None:
            raise self._failure
        raise AssertionError("this fake only covers the failure path")

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_playwright(monkeypatch: pytest.MonkeyPatch) -> _FakePlaywright:
    """Install a fake `playwright.sync_api` whose `chromium.launch()` fails the
    way a missing binary does."""
    driver = _FakePlaywright(RuntimeError("Executable doesn't exist at .../headless_shell"))

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: types.SimpleNamespace(start=lambda: driver)  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return driver


def _config(tmp_path: Path) -> BrowserSessionConfig:
    return BrowserSessionConfig(storage_state_path=tmp_path / "state.json")


def test_failed_launch_stops_the_driver(fake_playwright: _FakePlaywright, tmp_path: Path) -> None:
    """The whole point: no leaked driver, and therefore no leaked event loop."""
    with pytest.raises(RuntimeError), BrowserSession(_config(tmp_path)) as _page:
        pass  # pragma: no cover -- __enter__ raises before the body runs

    assert fake_playwright.stopped is True


def test_original_error_still_propagates(fake_playwright: _FakePlaywright, tmp_path: Path) -> None:
    """Cleanup must not swallow or replace the real cause -- callers log it."""
    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        BrowserSession(_config(tmp_path)).__enter__()


def test_handles_are_cleared_after_a_failed_enter(
    fake_playwright: _FakePlaywright, tmp_path: Path
) -> None:
    """A half-built session must not keep stale handles around."""
    session = BrowserSession(_config(tmp_path))
    with pytest.raises(RuntimeError):
        session.__enter__()

    assert session._playwright is None
    assert session._browser is None
    assert session._context is None
