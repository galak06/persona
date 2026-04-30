"""Tests for lib.runtime.shutdown.

Signals are global state — tests use the reset_for_tests() helper to
keep isolation. We don't actually fire SIGTERM in tests (would kill
pytest); instead we invoke the handler directly."""

from __future__ import annotations

import signal

import pytest

from lib.runtime import shutdown
from lib.runtime.shutdown import (
    install_shutdown_handler,
    is_shutdown_requested,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_for_tests()


class TestInstall:
    def test_install_registers_sigterm_handler(self) -> None:
        install_shutdown_handler()
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is not signal.SIG_DFL
        assert callable(handler)

    def test_install_registers_sigint_handler(self) -> None:
        install_shutdown_handler()
        handler = signal.getsignal(signal.SIGINT)
        assert handler is not signal.SIG_DFL
        assert callable(handler)

    def test_install_idempotent(self) -> None:
        install_shutdown_handler()
        first = signal.getsignal(signal.SIGTERM)
        install_shutdown_handler()
        second = signal.getsignal(signal.SIGTERM)
        assert first is second  # not re-registered


class TestEvent:
    def test_initial_state_not_requested(self) -> None:
        assert is_shutdown_requested() is False

    def test_signal_sets_event(self) -> None:
        install_shutdown_handler()
        # Direct invocation rather than os.kill — tests stay portable.
        shutdown._on_signal(signal.SIGTERM, None)
        assert is_shutdown_requested() is True

    def test_event_stays_set(self) -> None:
        install_shutdown_handler()
        shutdown._on_signal(signal.SIGTERM, None)
        # Shutdown is sticky — multiple checks see True.
        assert is_shutdown_requested() is True
        assert is_shutdown_requested() is True

    def test_reset_clears(self) -> None:
        install_shutdown_handler()
        shutdown._on_signal(signal.SIGTERM, None)
        assert is_shutdown_requested() is True
        reset_for_tests()
        assert is_shutdown_requested() is False
