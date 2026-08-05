"""Tests for `lib/db_pool.py`'s DSN resolution (`DATABASE_URL` handling).

These are pure config-path tests -- no Postgres needed, so they always run.
They pin the "fail loudly when unconfigured" contract: with `DATABASE_URL`
unset or empty the pool must raise a clear, actionable error at resolution
time rather than silently falling back to a credential-less default DSN (which
used to die opaquely later with ``PoolTimeout: no password supplied``).
"""

from __future__ import annotations

import pytest

from lib import db_pool


def test_dsn_raises_clear_error_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_dsn()` must raise an actionable RuntimeError when DATABASE_URL is unset."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        db_pool._dsn()


def test_dsn_raises_clear_error_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty / whitespace-only DATABASE_URL is treated as unconfigured."""
    monkeypatch.setenv("DATABASE_URL", "   ")
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        db_pool._dsn()


def test_get_pool_raises_clear_error_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loud failure surfaces at pool creation, not at import time."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_pool.close_pool()
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
            db_pool.get_pool()
    finally:
        db_pool.close_pool()


def test_dsn_returns_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured DATABASE_URL is returned verbatim (stripped)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://persona:persona@localhost:5434/persona")
    assert db_pool._dsn() == "postgresql://persona:persona@localhost:5434/persona"
