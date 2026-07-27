"""Tmp-path environment builders for the FB + IG scanner tests.

Extracted from ``conftest.py`` to keep that file under the 300-line cap.
The conftest still owns the public ``@pytest.fixture`` declarations and
re-exports ``read_queue``; this module owns the heavy wiring (config
payloads, bare-module path patches, collaborator stubs).

Both FB and IG follow the same recipe: build a tmp brand-dir layout,
point ``AppSettings`` + scanner-module path constants at it, then stub
the external collaborators the pipeline calls (Gemini drafter, Telegram
notifier, random delays, log writers).

Why the bare-module patching dance:
    ``pyproject.toml`` sets ``pythonpath = ["lib"]``, so
    ``scripts/fb_scan.py`` (and ``ig_scan.py``) imports collaborators as
    bare top-level modules (``import rate_limiter``) — NOT via the
    ``lib.`` namespace. Python creates two distinct module objects for
    the same source file: ``rate_limiter`` and ``lib.rate_limiter``.
    Patching ``lib.rate_limiter.STATE_FILE`` would silently miss what
    the scanner reads. We patch the bare-name modules instead. This is
    the "dual-module-identity footgun" — slice 5 work.
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
                "group_visits_per_day": 6,
            },
            "instagram": {
                "comments_per_day": 10,
                "likes_per_day": 8,
            },
        },
    }


def seed_empty_state(*paths: Path) -> None:
    """Write the empty-collection shape each scanner expects on first read."""
    for p in paths:
        p.write_text("[]" if p.name == "comment_queue.json" else "{}")


def patch_bare_path_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dedup_file: Path | None = None,
    rate_limit_file: Path | None = None,
    engagement_log_path: Path | None = None,
) -> None:
    """Redirect module-level path constants on the BARE-name modules.

    Scanners + the new shared pipeline both reach for these as bare
    modules (``import deduplication``); ``lib.<name>`` would miss.
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


def stub_pipeline_collaborators(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the bare-module collaborators the new pipeline injects.

    Post-slice-3 the scanners pass ``draft_helper`` / ``rate_limiter`` /
    ``deduplication`` modules into ``run_outbound_scan`` instead of looking
    those names up on the scanner module. Patching must happen on the
    bare modules now. The real ``can_act`` is used unchanged — the pipeline
    gates the like step by ``policy.daily_like_quota[platform] > 0`` before
    probing it, so ``facebook:like`` (absent from ``DAILY_LIMITS``) is never
    looked up in production.
    """
    import draft_helper as bare_drafter
    import rate_limiter as bare_rate_limiter

    def _fake_draft(
        *,
        platform: str,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str,
        site_context: str | None = None,
    ) -> str:
        return f"DRAFT for {post_url}"

    monkeypatch.setattr(bare_drafter, "draft_comment_for_post", _fake_draft)
    monkeypatch.setattr(
        bare_rate_limiter, "wait_random_delay", lambda *_a, **_k: None
    )


def stub_skill_notifications(
    monkeypatch: pytest.MonkeyPatch, scanner_module: Any
) -> None:
    """No-op the Telegram skill-notification hooks on a scanner module."""
    for fn_name in ("skill_started", "skill_finished", "skill_skipped"):
        monkeypatch.setattr(scanner_module, fn_name, lambda *_a, **_k: None)


def build_fb_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stub_mark_engaged: bool = True,
) -> dict[str, Path]:
    """Tmp-path environment for ``scripts.fb_scan`` tests.

    Patches the AppSettings singleton paths AND the module-level path
    constants in ``scripts.fb_scan``, bare ``deduplication``, and bare
    ``rate_limiter``. Stubs Gemini drafter, Telegram notifier, sleep
    delays, and (optionally) ``mark_engaged``. Pass ``stub_mark_engaged=
    False`` to let the production call path execute against the tmp
    dedup file (used by the signature-regression test).
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

    config_file.write_text(json.dumps(build_config_payload()))
    seed_empty_state(dedup_file, rate_limit_file, queue_file)

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
    patch_bare_path_modules(
        monkeypatch, dedup_file=dedup_file, rate_limit_file=rate_limit_file
    )
    stub_pipeline_collaborators(monkeypatch)

    import activity_log as bare_activity_log
    import deduplication as bare_dedup
    import notifier as bare_notifier

    monkeypatch.setattr(bare_notifier, "send", lambda *a, **kw: True)
    monkeypatch.setattr(bare_activity_log, "log_trace", lambda *a, **kw: None)
    monkeypatch.setattr(fb_scan, "log_trace", lambda *a, **kw: None)
    stub_skill_notifications(monkeypatch, fb_scan)
    if stub_mark_engaged:
        monkeypatch.setattr(bare_dedup, "mark_engaged", lambda *a, **kw: None)

    return {
        "state_dir": state_dir,
        "queue_file": queue_file,
        "last_run_file": last_run_file,
        "dedup_file": dedup_file,
        "rate_limit_file": rate_limit_file,
        "config_file": config_file,
        "brand_dir": brand_dir,
    }


def neutralize_scan_dedup_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sever ``ScanDedup``'s Postgres side so IG single-pass tests stay hermetic.

    Post-PR#36 IG runs the single-pass pipeline with ``lib.scan_dedup.ScanDedup``,
    whose iterate-once seen-marks live in Postgres ``completed_tasks``. Tests want
    neither the DB dependency nor the cross-test pollution real marks cause (post
    ids repeat across cases), so we stub the two Postgres calls ``scan_dedup``
    binds by name: reads return an empty set, writes are no-ops. Iterate-once
    still works WITHIN a run via ScanDedup's in-memory ``_seen_ids`` set; the JSON
    ``deduplication`` side keeps using the tmp cache (via ``patch_bare_path_modules``).

    Also silence the inline-comment engagement-log writer: a posted comment calls
    ``log_engagement`` with no path override, which would append to the REAL brand
    ``engagement_log.jsonl`` — a live-brand side effect a test must not have.
    """
    import lib.engagement.inline_comment as inline_comment
    import lib.scan_dedup as scan_dedup

    monkeypatch.setattr(scan_dedup, "completed_entity_ids", lambda *_a, **_k: set())
    monkeypatch.setattr(scan_dedup, "record_done", lambda *_a, **_k: True)
    monkeypatch.setattr(inline_comment, "log_engagement", lambda *_a, **_k: None)


def build_ig_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Tmp-path environment for ``scripts.ig_scan`` tests (SINGLE-PASS).

    Post-PR#36 Instagram likes AND comments in one visit and persists no queue,
    so ``ig_scan`` no longer exposes a ``QUEUE_FILE`` to patch. We redirect the
    two path constants it still owns (``LAST_RUN_FILE``, ``CONFIG_FILE``) plus
    the bare dedup/rate/log path modules, and neutralize the single-pass
    collaborators the run now reaches for (``ScanDedup``'s Postgres backend, the
    trace + engagement JSONL writers) so a run touches no real brand state.

    Returns ``{"state_dir", "tmp_path", "config_path", "rate_path",
    "last_run_path"}`` so individual tests can pre-spend the rate-limiter budget
    (``rate_path``), assert the last-run stamp (``last_run_path``), or rewrite
    the config file to override the policy.
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

    from scripts import ig_scan

    # No QUEUE_FILE: IG is single-pass, no Redis/queue handoff (PR#36). The
    # scan likes+comments inline, so only these two path constants remain.
    monkeypatch.setattr(ig_scan, "LAST_RUN_FILE", last_run_path)
    monkeypatch.setattr(ig_scan, "CONFIG_FILE", config_path)
    # `log_trace` writes to the real brand engagement log; no-op it in tests.
    monkeypatch.setattr(ig_scan, "log_trace", lambda *_a, **_k: None)
    patch_bare_path_modules(
        monkeypatch,
        dedup_file=dedup_path,
        rate_limit_file=rate_path,
        engagement_log_path=engagement_log_path,
    )
    neutralize_scan_dedup_backend(monkeypatch)

    def _fake_draft(
        *,
        platform: str,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str,
    ) -> str:
        return f"DRAFT-{post_url or '?'}"

    # Stub collaborators on the BARE-name modules — the thin scanner now
    # delegates drafting + delays to the pipeline, which calls these via
    # `draft_helper.draft_comment_for_post` and the rate_tracker protocol.
    import draft_helper as bare_draft_helper
    import rate_limiter as bare_rate_limiter

    monkeypatch.setattr(
        bare_draft_helper, "draft_comment_for_post", _fake_draft
    )
    monkeypatch.setattr(
        bare_rate_limiter, "wait_random_delay", lambda *_a, **_k: None
    )
    stub_skill_notifications(monkeypatch, ig_scan)

    return {
        "state_dir": state_dir,
        "tmp_path": tmp_path,
        "config_path": config_path,
        "rate_path": rate_path,
        "last_run_path": last_run_path,
    }
