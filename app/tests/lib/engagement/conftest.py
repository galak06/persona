"""Pytest fixtures for engagement engager tests.

Thin shim over ``_env_builders``: this module exposes the public
``@pytest.fixture`` declarations; the heavy tmp-path wiring lives in
``_env_builders.py`` to keep both files under the 300-line cap.

See ``_env_builders`` for the bare-module-patching rationale (the
"dual-module-identity footgun" that slice 5 will fix).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.lib.engagement._env_builders import (
    build_fb_environment,
    build_ig_environment,
)


@pytest.fixture(autouse=True)
def _hermetic_engagement_sinks(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op the inline-comment persistence sinks for EVERY test in this dir.

    A posted inline comment calls ``log_engagement`` (appends to the REAL
    brand ``engagement_log.jsonl`` when no path override is given) and
    ``engagements_db.record_publish`` (a Postgres write; it swallows DB
    errors, but an unset ``DATABASE_URL`` still costs a connection attempt
    per comment). Neither side effect belongs in a unit test, so both are
    stubbed by default. Tests that ASSERT on them re-patch with a recorder
    — a test-applied ``monkeypatch.setattr`` overrides this fixture's.
    """
    import lib.engagement.inline_comment as inline_comment

    monkeypatch.setattr(inline_comment, "log_engagement", lambda *_a, **_k: None)
    monkeypatch.setattr(
        inline_comment.engagements_db, "record_publish", lambda **_k: None
    )


# --- FB fixture -------------------------------------------------------------


@pytest.fixture
def fb_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Path]]:
    """Wire fb_engager (single-pass) state into ``tmp_path`` + stub collaborators.

    Patches the AppSettings singleton paths AND the module-level path
    constants in ``scripts.fb_engager``, bare ``deduplication``, and bare
    ``rate_limiter``; severs ``ScanDedup``'s Postgres side; defaults the
    first-comment gate to APPROVED for every group and the warmup filter
    to warm (empty tracker). Yields ``{"state_dir", "brand_dir",
    "last_run_file", "dedup_file", "rate_limit_file", "config_file",
    "session_file"}`` so individual tests can pre-spend rate budgets,
    assert the last-run stamp, or read the dedup cache. Gate/warmup tests
    re-patch the seams described in ``_env_builders.build_fb_environment``.
    """
    yield build_fb_environment(tmp_path, monkeypatch)


# --- IG fixture -------------------------------------------------------------


@pytest.fixture
def ig_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Path]]:
    """Wire ig_engager (single-pass) state into ``tmp_path`` + stub collaborators.

    Yields ``{"state_dir", "tmp_path", "config_path", "rate_path",
    "last_run_path"}`` so individual tests can pre-spend the rate-limiter
    comment budget (``rate_path``), assert the last-run stamp
    (``last_run_path``), or rewrite the config file to override the policy.
    IG does not queue — it likes+comments inline — so there is no
    ``queue_path`` to read (see ``_env_builders.build_ig_environment``).
    """
    yield build_ig_environment(tmp_path, monkeypatch)
