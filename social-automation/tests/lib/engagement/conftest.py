"""Shared fixtures and helpers for engagement scanner tests.

Both ``test_fb_scan_with_fake.py`` and ``test_ig_scan_with_fake.py`` follow
the same pattern: build a tmp brand-dir layout, point AppSettings + the
bare-module path constants at it, then stub external collaborators
(Gemini drafter, Telegram notifier, random delays, log writers).

This conftest exposes:
    * ``fb_environment`` fixture — full tmp-path wiring for ``scripts.fb_scan``
    * ``ig_environment`` fixture — full tmp-path wiring for ``scripts.ig_scan``
    * small helpers used by individual tests (config builder, queue reader, etc.)

Why the patching dance is unusual:
    ``pyproject.toml`` sets ``pythonpath = ["lib"]``, which means
    ``scripts/fb_scan.py`` (and ``ig_scan.py``) imports collaborators as
    bare top-level modules (``import rate_limiter``) — NOT via the
    ``lib.`` namespace. Python therefore creates two distinct module
    objects for the same source file: ``rate_limiter`` and
    ``lib.rate_limiter``. Patching ``lib.rate_limiter.STATE_FILE`` from a
    test would silently miss what the scanner reads. We patch the
    bare-name modules instead. This is the "dual-module-identity
    footgun" and should be fixed in slice 3.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# --- helpers ----------------------------------------------------------------


def _build_config_payload() -> dict[str, Any]:
    """Minimal config.json payload covering both FB and IG rate-limit blocks."""
    return {
        "content_analysis": {
            "relevance_threshold": 0.70,
            "approval_threshold": 0.80,
        },
        "rate_limits": {
            "facebook": {
                "comments_per_day": 5,
                "group_visits_per_day": 6,
            },
            "instagram": {
                "comments_per_day": 2,
                "likes_per_day": 8,
            },
        },
    }


def _seed_empty_state(*paths: Path) -> None:
    """Write the empty-collection shape each scanner expects on first read."""
    for p in paths:
        p.write_text("[]" if p.name == "comment_queue.json" else "{}")


def _patch_bare_path_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dedup_file: Path | None = None,
    rate_limit_file: Path | None = None,
    engagement_log_path: Path | None = None,
) -> None:
    """Redirect module-level path constants on the BARE-name modules.

    Scanners import these as bare modules; ``lib.<name>`` would miss.
    """
    if dedup_file is not None:
        import deduplication as bare_dedup

        monkeypatch.setattr(bare_dedup, "CACHE_FILE", dedup_file)
    if rate_limit_file is not None:
        import rate_limiter as bare_rate_limiter

        monkeypatch.setattr(bare_rate_limiter, "STATE_FILE", rate_limit_file)
    if engagement_log_path is not None:
        import activity_log as bare_activity_log

        monkeypatch.setattr(
            bare_activity_log, "ENGAGEMENT_LOG_PATH", engagement_log_path
        )


def _stub_skill_notifications(
    monkeypatch: pytest.MonkeyPatch, scanner_module: Any
) -> None:
    """No-op the Telegram skill-notification hooks on a scanner module."""
    for fn_name in ("skill_started", "skill_finished", "skill_skipped"):
        monkeypatch.setattr(scanner_module, fn_name, lambda *_a, **_k: None)


def read_queue(queue_file: Path) -> list[dict[str, Any]]:
    """Read+parse a comment_queue.json file (helper for assertions)."""
    return json.loads(queue_file.read_text())


# --- FB fixture -------------------------------------------------------------


@pytest.fixture
def fb_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Path]]:
    """Redirect every fb_scan state-file dependency into tmp_path.

    Patches the AppSettings singleton paths AND the module-level path
    constants in ``scripts.fb_scan``, bare ``deduplication``, and bare
    ``rate_limiter``. Stubs Gemini drafter, Telegram notifier, sleep
    delays, and ``mark_engaged`` — fb_scan calls it with only
    ``(platform, post_id)`` but the production signature requires
    ``action`` (existing bug, flag for slice 3); stub to a no-op.
    """
    brand_dir = tmp_path / "brand"
    state_dir = brand_dir / "state"
    logs_dir = brand_dir / "logs"
    data_dir = brand_dir / "data"
    for d in (state_dir, logs_dir, data_dir):
        d.mkdir(parents=True)

    queue_file = state_dir / "comment_queue.json"
    last_run_file = state_dir / "last_run.json"
    rate_limit_file = state_dir / "rate_limit_tracker.json"
    dedup_file = state_dir / "dedup_cache.json"
    fb_session_file = state_dir / "facebook_session.json"
    config_file = brand_dir / "config.json"
    error_log = logs_dir / "errors.log"

    config_file.write_text(json.dumps(_build_config_payload()))
    _seed_empty_state(dedup_file, rate_limit_file, queue_file)

    from lib.config import settings as live_settings

    assert live_settings.paths is not None
    monkeypatch.setattr(live_settings.paths, "brand_dir", brand_dir)
    monkeypatch.setattr(live_settings.paths, "state_dir", state_dir)
    monkeypatch.setattr(live_settings.paths, "logs_dir", logs_dir)
    monkeypatch.setattr(live_settings.paths, "data_dir", data_dir)
    monkeypatch.setattr(live_settings.paths, "comment_queue", queue_file)
    monkeypatch.setattr(live_settings.paths, "last_run", last_run_file)
    monkeypatch.setattr(live_settings.paths, "rate_limit_tracker", rate_limit_file)
    monkeypatch.setattr(live_settings.paths, "dedup_cache", dedup_file)
    monkeypatch.setattr(live_settings.paths, "facebook_session", fb_session_file)

    import scripts.fb_scan as fb_scan

    monkeypatch.setattr(fb_scan, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(fb_scan, "LAST_RUN_FILE", last_run_file)
    monkeypatch.setattr(fb_scan, "CONFIG_FILE", config_file)
    monkeypatch.setattr(fb_scan, "SESSION_FILE", fb_session_file)
    monkeypatch.setattr(fb_scan, "ERROR_LOG", error_log)
    _patch_bare_path_modules(
        monkeypatch, dedup_file=dedup_file, rate_limit_file=rate_limit_file
    )

    import activity_log as bare_activity_log
    import notifier as bare_notifier

    def _fake_draft(**kwargs: Any) -> str:
        return f"DRAFT for {kwargs.get('post_url', 'unknown')}"

    monkeypatch.setattr(fb_scan, "draft_comment_for_post", _fake_draft)
    monkeypatch.setattr(bare_notifier, "send", lambda *a, **kw: True)
    monkeypatch.setattr(fb_scan, "wait_random_delay", lambda *a, **kw: None)
    monkeypatch.setattr(bare_activity_log, "log_trace", lambda *a, **kw: None)
    monkeypatch.setattr(fb_scan, "log_trace", lambda *a, **kw: None)
    _stub_skill_notifications(monkeypatch, fb_scan)
    monkeypatch.setattr(fb_scan, "mark_engaged", lambda *a, **kw: None)

    yield {
        "state_dir": state_dir,
        "queue_file": queue_file,
        "last_run_file": last_run_file,
        "dedup_file": dedup_file,
        "rate_limit_file": rate_limit_file,
        "config_file": config_file,
        "brand_dir": brand_dir,
    }


# --- IG fixture -------------------------------------------------------------


@pytest.fixture
def ig_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Path]]:
    """Wire ig_scan state into ``tmp_path`` + stub external collaborators.

    Yields ``{"state_dir": ..., "tmp_path": ...}`` so individual tests can
    read the resulting queue / dedup / tracker files for assertions.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_build_config_payload()))

    queue_path = state_dir / "comment_queue.json"
    last_run_path = state_dir / "last_run.json"
    dedup_path = state_dir / "dedup_cache.json"
    rate_path = state_dir / "rate_limit_tracker.json"
    error_log_path = logs_dir / "errors.log"
    engagement_log_path = logs_dir / "engagement_log.jsonl"

    _seed_empty_state(queue_path, last_run_path, dedup_path, rate_path)

    from scripts import ig_scan

    monkeypatch.setattr(ig_scan, "QUEUE_FILE", queue_path)
    monkeypatch.setattr(ig_scan, "LAST_RUN_FILE", last_run_path)
    monkeypatch.setattr(ig_scan, "CONFIG_FILE", config_path)
    monkeypatch.setattr(ig_scan, "ERROR_LOG", error_log_path)
    _patch_bare_path_modules(
        monkeypatch,
        dedup_file=dedup_path,
        rate_limit_file=rate_path,
        engagement_log_path=engagement_log_path,
    )

    def _fake_draft(
        *,
        platform: str,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str | None = None,
        site_context: str | None = None,
    ) -> str:
        return f"DRAFT-{post_url or '?'}"

    monkeypatch.setattr(ig_scan, "draft_comment_for_post", _fake_draft)
    monkeypatch.setattr(ig_scan, "wait_random_delay", lambda *_a, **_k: None)
    _stub_skill_notifications(monkeypatch, ig_scan)

    yield {"state_dir": state_dir, "tmp_path": tmp_path}
