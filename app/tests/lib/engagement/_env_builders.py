"""Tmp-path environment builders for the FB + IG engager tests.

Extracted from ``conftest.py`` (which owns the public ``@pytest.fixture``
declarations) to keep both files under the 300-line cap. Both FB and IG
follow the same recipe: build a tmp brand-dir layout, point ``AppSettings``
+ engager-module path constants at it, then stub the external collaborators
the pipeline calls (Telegram notifier, random delays, log writers, the
groups-db-backed first-comment gate).

Historical note: this file used to carry a "bare-module patching dance"
because ``pyproject.toml`` set ``pythonpath = ["lib"]``, so the engagers
imported collaborators as bare top-level modules (``import rate_limiter``)
rather than through the ``lib.`` namespace. Python built two distinct module
objects for one source file, and patching ``lib.rate_limiter.STATE_FILE``
silently missed what the engager actually read. That was the
"dual-module-identity footgun"; it was removed on 2026-08-15, and there is
now exactly one module object per file. The helpers below are ordinary
path-redirection and collaborator stubs — patch ``lib.<name>`` and it works.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def build_config_payload() -> dict[str, Any]:
    """Minimal config.json payload covering both FB and IG rate-limit blocks."""
    return {
        "content_analysis": {
            "relevance_threshold": 0.70,
            "approval_threshold": 0.80,
        },
        "rate_limits": {
            "facebook": {
                "comments_per_day": 5,
                "likes_per_day": 5,
                "group_visits_per_day": 6,
            },
            "instagram": {
                "comments_per_day": 10,
                "likes_per_day": 8,
            },
        },
    }


def seed_empty_state(*paths: Path) -> None:
    """Write the empty-collection shape each engager expects on first read."""
    for p in paths:
        p.write_text("{}")


def redirect_state_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dedup_file: Path | None = None,
    rate_limit_file: Path | None = None,
    engagement_log_path: Path | None = None,
) -> None:
    """Point the state-file constants at tmp paths for the duration of a test.

    These are module-level constants (``deduplication.CACHE_FILE``,
    ``rate_limiter.STATE_FILE``, ``activity_log.ENGAGEMENT_LOG_PATH``) read at
    call time, so redirecting them on the module object is enough.
    """
    if dedup_file is not None:
        from lib import deduplication

        monkeypatch.setattr(deduplication, "CACHE_FILE", dedup_file)
    if rate_limit_file is not None:
        from lib import rate_limiter

        monkeypatch.setattr(rate_limiter, "STATE_FILE", rate_limit_file)
    if engagement_log_path is not None:
        from lib import activity_log

        monkeypatch.setattr(activity_log, "ENGAGEMENT_LOG_PATH", engagement_log_path)


def stub_pipeline_collaborators(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the collaborator modules the pipeline is handed.

    The engagers pass the ``lib.rate_limiter`` / ``lib.deduplication`` modules
    into ``run_outbound_scan``. The real ``can_act`` is used unchanged — quotas
    resolve against the tmp state file redirected by ``redirect_state_paths``.
    Drafting is NOT stubbed here: both engagers inject a
    ``draft_helper.SkillDrafter`` instance bound at import, so tests patch that
    instance (see ``test_ig_engager_with_fake`` / ``test_fb_engager_with_fake``).
    """
    from lib import rate_limiter

    monkeypatch.setattr(rate_limiter, "wait_random_delay", lambda *_a, **_k: None)


def stub_skill_notifications(
    monkeypatch: pytest.MonkeyPatch, scanner_module: Any
) -> None:
    """No-op the Telegram skill-notification hooks on an engager module."""
    for fn_name in ("skill_started", "skill_finished", "skill_skipped"):
        monkeypatch.setattr(scanner_module, fn_name, lambda *_a, **_k: None)


def neutralize_scan_dedup_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sever ``ScanDedup``'s Postgres side so single-pass tests stay hermetic.

    Both engagers pass ``lib.scan_dedup.ScanDedup``, whose iterate-once
    seen-marks live in Postgres ``completed_tasks``. Tests want neither the
    DB dependency nor the cross-test pollution real marks cause, so the two
    Postgres calls ``scan_dedup`` binds by name are stubbed: reads return an
    empty set, writes are no-ops. Iterate-once still works WITHIN a run via
    ScanDedup's in-memory ``_seen_ids`` set; the JSON ``deduplication`` side
    keeps using the tmp cache (via ``redirect_state_paths``).

    The inline-comment persistence sinks (``log_engagement`` JSONL +
    ``engagements_db.record_publish``) are already no-oped for every test in
    this directory by conftest's autouse ``_hermetic_engagement_sinks``.
    """
    import lib.scan_dedup as scan_dedup

    monkeypatch.setattr(scan_dedup, "completed_entity_ids", lambda *_a, **_k: set())
    monkeypatch.setattr(scan_dedup, "record_done", lambda *_a, **_k: True)


def build_fb_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Tmp-path environment for ``scripts.fb_engager`` tests (SINGLE-PASS).

    Facebook mirrors Instagram now: no ``QUEUE_FILE`` to patch. Redirects
    the AppSettings singleton paths AND the path constants ``fb_engager``
    owns (``LAST_RUN_FILE``, ``CONFIG_FILE``, ``SESSION_FILE``), plus the
    bare dedup/rate/log path modules, then neutralizes what a run reaches
    for: ``ScanDedup``'s Postgres backend, the Telegram hooks (``skill_*``
    no-oped, ``notifier.send`` stubbed to return True), the warmup filter
    (``lib.group_warmup._load_tracker`` returns
    ``[]`` so the REAL ``is_group_warm`` logic says warm; warmup tests
    re-patch it with recent ``joined_at`` rows), ``log_trace`` and delays.
    Returns the tmp paths tests assert against.
    """
    brand_dir = tmp_path / "brand"
    state_dir = brand_dir / "state"
    logs_dir = brand_dir / "logs"
    data_dir = brand_dir / "data"
    for d in (state_dir, logs_dir, data_dir):
        d.mkdir(parents=True)

    last_run_file = state_dir / "last_run.json"
    rate_limit_file = state_dir / "rate_limit_tracker.json"
    dedup_file = state_dir / "dedup_cache.json"
    fb_session_file = state_dir / "facebook_session.json"
    config_file = brand_dir / "config.json"
    engagement_log_path = logs_dir / "engagement_log.jsonl"

    config_file.write_text(json.dumps(build_config_payload()))
    seed_empty_state(dedup_file, rate_limit_file, last_run_file)

    from lib.config import settings as live_settings

    assert live_settings.paths is not None
    monkeypatch.setattr(live_settings.paths, "brand_dir", brand_dir)
    monkeypatch.setattr(live_settings.paths, "state_dir", state_dir)
    monkeypatch.setattr(live_settings.paths, "logs_dir", logs_dir)
    monkeypatch.setattr(live_settings.paths, "data_dir", data_dir)
    monkeypatch.setattr(live_settings.paths, "last_run", last_run_file)
    monkeypatch.setattr(live_settings.paths, "rate_limit_tracker", rate_limit_file)
    monkeypatch.setattr(live_settings.paths, "dedup_cache", dedup_file)
    monkeypatch.setattr(live_settings.paths, "facebook_session", fb_session_file)

    import scripts.fb_engager as fb_engager

    monkeypatch.setattr(fb_engager, "LAST_RUN_FILE", last_run_file)
    monkeypatch.setattr(fb_engager, "CONFIG_FILE", config_file)
    monkeypatch.setattr(fb_engager, "SESSION_FILE", fb_session_file)
    redirect_state_paths(
        monkeypatch,
        dedup_file=dedup_file,
        rate_limit_file=rate_limit_file,
        engagement_log_path=engagement_log_path,
    )
    stub_pipeline_collaborators(monkeypatch)
    neutralize_scan_dedup_backend(monkeypatch)

    # `log_trace` is imported by name (`from lib.activity_log import
    # log_trace`), so patch the bound name on the engager module.
    monkeypatch.setattr(fb_engager, "log_trace", lambda *_a, **_k: None)
    stub_skill_notifications(monkeypatch, fb_engager)
    # fb_engager sends no Telegram of its own since the first-comment gate
    # was removed (2026-08-13), but `notifier.send` stays stubbed so any
    # future notify path can't reach the real bot from a test.
    from lib import notifier

    monkeypatch.setattr(notifier, "send", lambda *_a, **_k: True)

    # Warmup filter: real `is_group_warm` logic over an empty tracker
    # (joined_at unknown -> warm). Warmup tests re-patch `_load_tracker`.
    import lib.group_warmup as group_warmup

    monkeypatch.setattr(group_warmup, "_load_tracker", lambda: [])

    return {
        "state_dir": state_dir,
        "brand_dir": brand_dir,
        "last_run_file": last_run_file,
        "dedup_file": dedup_file,
        "rate_limit_file": rate_limit_file,
        "config_file": config_file,
        "session_file": fb_session_file,
    }


def build_ig_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Tmp-path environment for ``scripts.ig_engager`` tests (SINGLE-PASS).

    Post-PR#36 Instagram likes AND comments in one visit and persists no
    queue, so there is no ``QUEUE_FILE`` to patch. We redirect the two path
    constants it still owns (``LAST_RUN_FILE``, ``CONFIG_FILE``) plus the
    bare dedup/rate/log path modules, and neutralize ``ScanDedup``'s
    Postgres backend + the trace writer so a run touches no real brand
    state. Returns ``{"state_dir", "tmp_path", "config_path", "rate_path",
    "last_run_path"}`` so tests can pre-spend the rate budget, assert the
    last-run stamp, or rewrite the config file to override the policy.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(build_config_payload()))

    last_run_path = state_dir / "last_run.json"
    dedup_path = state_dir / "dedup_cache.json"
    rate_path = state_dir / "rate_limit_tracker.json"
    engagement_log_path = logs_dir / "engagement_log.jsonl"

    seed_empty_state(last_run_path, dedup_path, rate_path)

    from scripts import ig_engager

    # No QUEUE_FILE: IG is single-pass, no Redis/queue handoff (PR#36). The
    # scan likes+comments inline, so only these two path constants remain.
    monkeypatch.setattr(ig_engager, "LAST_RUN_FILE", last_run_path)
    monkeypatch.setattr(ig_engager, "CONFIG_FILE", config_path)
    # `log_trace` writes to the real brand engagement log; no-op it in tests.
    monkeypatch.setattr(ig_engager, "log_trace", lambda *_a, **_k: None)
    redirect_state_paths(
        monkeypatch,
        dedup_file=dedup_path,
        rate_limit_file=rate_path,
        engagement_log_path=engagement_log_path,
    )
    neutralize_scan_dedup_backend(monkeypatch)

    # Stub delays on the BARE-name module — the thin scanner delegates them
    # to the pipeline, which calls them via the rate_tracker protocol.
    # Drafting is left alone: the scan injects its own ``SkillDrafter``
    # instance, so tests patch that instance (see ``test_ig_engager_with_fake``).
    from lib import rate_limiter as bare_rate_limiter

    monkeypatch.setattr(
        bare_rate_limiter, "wait_random_delay", lambda *_a, **_k: None
    )
    stub_skill_notifications(monkeypatch, ig_engager)

    return {
        "state_dir": state_dir,
        "tmp_path": tmp_path,
        "config_path": config_path,
        "rate_path": rate_path,
        "last_run_path": last_run_path,
    }
