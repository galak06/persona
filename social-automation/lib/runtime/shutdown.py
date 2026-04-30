"""Graceful shutdown — SIGTERM finishes the current item then exits.

Cron runs may need to be killed (e.g. a `launchctl unload` during
maintenance). The bare-default behavior of Python is to bail
immediately, leaving in-flight work in inconsistent state (queue
item posted but not marked, lock file held, browser session not
saved).

Pattern:

    install_shutdown_handler()
    for item in queue:
        if is_shutdown_requested():
            log.warning("shutdown_requested_breaking_loop")
            break
        process(item)        # safe to complete — no external interrupt
        save_state()         # always reached for completed items

For finer control (interrupt mid-item), call `is_shutdown_requested()`
inside long-running operations and short-circuit cleanly.
"""

from __future__ import annotations

import signal
import threading
from types import FrameType

from lib.errors.base import SocialAutomationError

_shutdown_event = threading.Event()
_handlers_installed = False


class ShutdownRequested(SocialAutomationError):
    """Raised inside a runner when it needs to abort due to a SIGTERM.

    Most code should poll `is_shutdown_requested()` between work items
    rather than catch this — the exception is for cases where you want
    to escape from deep call stacks (e.g. a graph node that's already
    inside a long Playwright operation).
    """


def install_shutdown_handler() -> None:
    """Install SIGTERM and SIGINT handlers that flag the shutdown event.

    Idempotent — safe to call from runner main(). Handlers are
    process-wide and persist for the lifetime of the interpreter.

    On signal:
        - sets `_shutdown_event` (visible via `is_shutdown_requested()`)
        - prior handlers are NOT chained — we want clean per-runner
          control, and these handlers are deliberately the only ones.
    """
    global _handlers_installed
    if _handlers_installed:
        return
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    _handlers_installed = True


def _on_signal(_signum: int, _frame: FrameType | None) -> None:
    """Module-internal handler — sets the shared event."""
    _shutdown_event.set()


def is_shutdown_requested() -> bool:
    """Return True if a SIGTERM/SIGINT has been received this process.

    Once True, stays True for the lifetime of the process — there's
    no recovery from SIGTERM, only clean wind-down.
    """
    return _shutdown_event.is_set()


def reset_for_tests() -> None:
    """Reset the shutdown event. Tests only — never call from production code."""
    global _handlers_installed
    _shutdown_event.clear()
    _handlers_installed = False
