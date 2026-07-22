"""Pytest fixtures for engagement scanner tests.

Thin shim over ``_env_builders``: this module exposes the public
``@pytest.fixture`` declarations + the ``read_queue`` helper; the heavy
tmp-path wiring lives in ``_env_builders.py`` to keep both files under
the 300-line cap.

See ``_env_builders`` for the bare-module-patching rationale (the
"dual-module-identity footgun" that slice 5 will fix).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.lib.engagement._env_builders import (
    build_fb_environment,
    build_ig_environment,
)


def read_queue(queue_file: Path) -> list[dict[str, Any]]:
    """Read+parse a comment_queue.json file (helper for assertions)."""
    return json.loads(queue_file.read_text())


# --- FB fixtures ------------------------------------------------------------


@pytest.fixture
def fb_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Path]]:
    """Redirect every fb_scan state-file dependency into tmp_path.

    Patches the AppSettings singleton paths AND the module-level path
    constants in ``scripts.fb_scan``, bare ``deduplication``, and bare
    ``rate_limiter``. Stubs Gemini drafter, Telegram notifier, sleep
    delays, and ``mark_engaged``.
    """
    yield build_fb_environment(tmp_path, monkeypatch, stub_mark_engaged=True)


@pytest.fixture
def fb_environment_real_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Path]]:
    """Same as ``fb_environment`` but DOES NOT stub ``mark_engaged``.

    Used by the signature-regression test to prove the production
    ``mark_engaged`` call path actually works (writes to the dedup cache
    without raising ``TypeError``).
    """
    yield build_fb_environment(tmp_path, monkeypatch, stub_mark_engaged=False)


# --- IG fixture -------------------------------------------------------------


@pytest.fixture
def ig_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Path]]:
    """Wire ig_scan (single-pass) state into ``tmp_path`` + stub collaborators.

    Yields ``{"state_dir", "tmp_path", "config_path", "rate_path",
    "last_run_path"}`` so individual tests can pre-spend the rate-limiter
    comment budget (``rate_path``), assert the last-run stamp
    (``last_run_path``), or rewrite the config file to override the policy.
    IG no longer queues — it likes+comments inline — so there is no
    ``queue_path`` to read (see ``_env_builders.build_ig_environment``).
    """
    yield build_ig_environment(tmp_path, monkeypatch)
