"""Structural type for a Facebook session.

Defines the minimal surface that consumers (publishers, scanners,
warmup loops) depend on so that they can be exercised against any
backend — Playwright today, a fake/in-memory stub in tests tomorrow —
without leaking Playwright types into their signatures.

The protocol is deliberately small (ISP): one context manager that
yields a navigable page, one cheap auth probe, and the on-disk path
used for cookie persistence.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from playwright.sync_api import Page


@runtime_checkable
class FbSession(Protocol):
    """Minimal Facebook session contract.

    Attributes:
        storage_path: On-disk path to the persisted cookie/storage JSON.
            Implementations read it on enter (if present) and write it
            back on exit so auth refreshes survive across runs.
    """

    storage_path: Path

    def page(self) -> AbstractContextManager["Page"]:
        """Open the session and yield a Playwright `Page` to drive."""
        ...

    def is_authenticated(self) -> bool:
        """Cheap probe — does the storage file look populated?"""
        ...
