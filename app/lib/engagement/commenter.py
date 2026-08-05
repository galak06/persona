# pyright: reportMissingImports=false
"""Shared engagement-commenter core for the per-platform comment actions.

``scripts/fb_comment.py`` and ``scripts/ig_comment.py`` are thin wrappers that
build a :class:`CommenterSpec` (platform mechanics: session, queue, draft fn,
post fn) and call :func:`main_for`. The drain loop — re-run guard, pending
filter (skip only already-commented posts), draft-at-post-time, Playwright
post, dedup + rate + engagements.db recording, pacing — lives here once so the
two platforms never duplicate it.

Bare ``import deduplication`` / ``import rate_limiter`` match the worker modules'
identity so tests that monkeypatch those bare modules still bite (the project's
dual-module-identity convention).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import deduplication
import rate_limiter
from lib import engagements_db
from lib.activity_log import log_trace
from lib.local_env import get_runtime_headless
from lib.logger import log_progress, log_step
from lib.runtime.singleton import LockAcquisitionError, SingletonLock
from notifier import skill_error, skill_finished, skill_skipped, skill_started

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class CommenterSpec:
    """Everything platform-specific about one comment action."""

    platform: str  # "facebook" | "instagram"
    skill_name: str  # "fb-comment" | "ig-comment"
    label: str  # short tag for logs/CLI, e.g. "FB" | "IG"
    guard_key: str  # per-platform re-run-guard key
    session_file: Path
    last_run_file: Path
    log_file: Path
    home_url: str  # homepage used for the session check
    login_markers: tuple[str, ...]  # url fragments that mean "logged out"
    target_field: str  # queue key with the human label (group_name/hashtag)
    draft_fn: Callable[[dict[str, Any]], str]  # item -> draft ("" => skip)
    post_fn: Callable[[Any, str, str], bool]  # (page, post_url, text) -> ok
    session_missing_msg: str
    queue_file: Path | None = None  # required when task_queue is None (JSON path)
    task_queue: Any = None  # if set, drain Redis instead of queue_file


def _load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _label_for(spec: CommenterSpec, item: dict[str, Any]) -> str:
    return str(item.get(spec.target_field) or "")


def _log_engagement(
    spec: CommenterSpec,
    target: str,
    content: str,
    *,
    post_url: str | None = None,
    post_id: str | None = None,
    relevance_score: float | None = None,
    post_text: str | None = None,
) -> None:
    entry: dict[str, Any] = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now(UTC).isoformat(),
        "action": "comment",
        "platform": spec.platform,
        "target_name": target,
        "content": content[:200],
    }
    if post_url is not None:
        entry["post_url"] = post_url
    if post_id is not None:
        entry["post_id"] = post_id
    if relevance_score is not None:
        entry["relevance_score"] = relevance_score
    if post_text is not None:
        entry["post_text"] = post_text[:300]
    spec.log_file.parent.mkdir(parents=True, exist_ok=True)
    with spec.log_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _already_ran_today(spec: CommenterSpec, last_run: dict[str, Any]) -> bool:
    cc = last_run.get(spec.guard_key, {})
    return (cc.get("last_run_at") or "")[:10] == date.today().isoformat() and (
        cc.get("status") == "success"
    )


def _pending_items(spec: CommenterSpec, queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Queue items still needing a comment, skipping posts already commented on.

    Two duplicate guards (a post is skipped if EITHER fires):
      - ``deduplication.already_commented`` — the 60-day dedup cache.
      - ``engagements_db.posted_comment_post_ids`` — the durable DB record, which
        still blocks a second comment even if the dedup cache was cleared.
    The scanner pre-marks queued/liked posts in the cache, so we gate on a real
    prior *comment*, never bare presence. Skipped items are stamped so they don't
    sit pending forever.
    """
    candidates = [
        item
        for item in queue
        if item.get("platform") == spec.platform and item.get("status") == "pending"
    ]
    pids = [str(item.get("post_id") or "") for item in candidates]
    posted_in_db = engagements_db.posted_comment_post_ids(spec.platform, pids)

    out: list[dict[str, Any]] = []
    for item in candidates:
        pid = str(item.get("post_id") or "")
        if pid and (pid in posted_in_db or deduplication.already_commented(spec.platform, pid)):
            item["status"] = "already_commented"
            item["_blocked_reason"] = "already commented on this post (dedup_cache/engagements.db)"
            continue
        out.append(item)
    return out


def _pending_items_pg(spec: CommenterSpec, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup filter for Redis-backed queue items using Postgres completed_tasks."""
    from lib.dedup_pg import already_done

    return [
        item
        for item in items
        if not already_done("comment", spec.platform, str(item.get("post_id") or ""))  # type: ignore[arg-type]
    ]


def _record_done_pg(platform: str, post_id: str) -> None:
    """Best-effort Postgres dedup record after a successful comment."""
    try:
        from lib.dedup_pg import record_done

        record_done("comment", platform, post_id)  # type: ignore[arg-type]
    except Exception:
        pass


def _record(
    spec: CommenterSpec, item: dict[str, Any], status: str, *, draft: str = "", error: str = ""
) -> None:
    """Record this comment publish (posted or failed) into engagements.db."""
    engagements_db.record_publish(
        platform=spec.platform,
        kind="comment",
        status=status,
        target_name=_label_for(spec, item),
        target_url=str(item.get("post_url") or ""),
        content=draft,
        ref=str(item.get("post_id") or ""),
        error=error,
        posted_at=str(item.get("posted_at") or "") or None,
    )


def run_commenter(spec: CommenterSpec, args: argparse.Namespace) -> int:
    """Drain ``spec.queue_file`` of pending posts: draft each, post, record."""
    log_trace(spec.platform, f"Started {spec.label} comment action")

    if not spec.session_file.exists():
        skill_skipped(spec.skill_name, spec.session_missing_msg)
        return 0

    last_run = _load_json(spec.last_run_file, {})
    if _already_ran_today(spec, last_run) and not args.force:
        skill_skipped(spec.skill_name, "already ran successfully today")
        return 0
    if not rate_limiter.can_act(spec.platform, "comment") and not args.force:
        skill_skipped(spec.skill_name, f"Daily {spec.label} comment limit reached")
        rate_limiter.print_status()
        return 0

    if spec.task_queue is not None:
        raw: list[dict[str, Any]] = []
        while (t := spec.task_queue.pop_nowait()) is not None:
            raw.append(t)
        queue: list[dict[str, Any]] = raw
        pending = _pending_items_pg(spec, raw)
    else:
        queue = _load_json(spec.queue_file, [])
        pending = _pending_items(spec, queue)
        if not args.dry_run:
            _save_json(spec.queue_file, queue)  # persist already-commented stamps (live only)
    if args.limit is not None:
        pending = pending[: args.limit]

    if not pending:
        skill_skipped(spec.skill_name, f"no pending {spec.label} posts to comment on")
        return 0

    skill_started(spec.skill_name, f"Drafting + posting up to {len(pending)} {spec.label} comments")
    rate_limiter.print_status()

    posted, failed, skipped = _process(spec, queue, pending, dry_run=args.dry_run)

    if not args.dry_run:
        last_run[spec.guard_key] = {
            "last_run_at": datetime.now(UTC).isoformat(),
            "comments_posted": posted,
            "comments_failed": failed,
            "status": "success",
        }
        _save_json(spec.last_run_file, last_run)

    summary = f"📝 Posted: {posted} | Failed: {failed} | Skipped: {skipped}"
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished(spec.skill_name, summary)
    rate_limiter.print_status()
    return 0


def _process(
    spec: CommenterSpec,
    queue: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Draft each pending item and (unless dry-run) post it. Returns counts."""
    if dry_run:
        skipped = 0
        for idx, item in enumerate(pending):
            draft = spec.draft_fn(item)
            log_progress(idx + 1, len(pending), f"{spec.label}: {_label_for(spec, item)}")
            print(f"    draft: {draft or '<empty — would skip>'}", flush=True)
            if not draft:
                skipped += 1
        return 0, 0, skipped

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=get_runtime_headless())
        ctx = browser.new_context(
            storage_state=str(spec.session_file),
            viewport={"width": 1280, "height": 900},
            user_agent=_UA,
        )
        page = ctx.new_page()
        try:
            page.goto(spec.home_url, wait_until="domcontentloaded")
            url = page.url.lower()
            if any(m in url for m in spec.login_markers):
                skill_error(spec.skill_name, f"{spec.label} session expired")
                return 0, 0, 0
            log_step(f"{spec.label} session OK")
            result = _post_loop(spec, page, queue, pending)
        finally:
            ctx.storage_state(path=str(spec.session_file))
            ctx.close()
            browser.close()
    return result


def _post_loop(
    spec: CommenterSpec,
    page: Any,
    queue: list[dict[str, Any]],
    pending: list[dict[str, Any]],
) -> tuple[int, int, int]:
    """Draft → post each pending item, honoring the daily cap + pacing."""
    posted = failed = skipped = 0
    for idx, item in enumerate(pending):
        if not rate_limiter.can_act(spec.platform, "comment"):
            print(f"\nDaily {spec.label} comment limit reached.", flush=True)
            break

        label = _label_for(spec, item)
        pid = str(item.get("post_id") or "")
        log_progress(idx + 1, len(pending), f"{spec.label}: {label}")

        draft = spec.draft_fn(item)
        if not draft:
            item["status"] = "USER_SKIPPED"
            # An empty draft is any of: agent declined (engage:false), blank
            # comment, upstream/LLM failure, or two voice-validation failures.
            # The specific cause is in the drafter's structured log (Grafana/
            # Loki); don't assert a single reason here that's wrong 3 times in 4.
            item["_blocked_reason"] = (
                "draft empty (declined / blank / upstream error / voice-failed)"
            )
            skipped += 1
            if spec.task_queue is None:
                _save_json(spec.queue_file, queue)
            continue

        try:
            ok = spec.post_fn(page, str(item.get("post_url") or ""), draft)
        except Exception as e:  # browser flakiness must not abort the batch
            print(f"    ERROR: {e}", flush=True)
            item["status"] = "POST_FAILED"
            item["error"] = str(e)[:200]
            deduplication.mark_engaged(spec.platform, pid, "comment", label, status="failed")
            _record(spec, item, "failed", draft=draft, error=str(e)[:200])
            failed += 1
            continue

        if not ok:
            item["status"] = "COMMENT_BOX_NOT_FOUND"
            deduplication.mark_engaged(spec.platform, pid, "comment", label, status="failed")
            _record(spec, item, "failed", draft=draft, error="comment box not found")
            failed += 1
            continue

        rate_limiter.record_action(spec.platform, "comment")
        deduplication.mark_engaged(spec.platform, pid, "comment", label)
        if spec.task_queue is not None:
            _record_done_pg(spec.platform, pid)
        _log_engagement(
            spec,
            label,
            draft,
            post_url=item.get("post_url"),
            post_id=item.get("post_id"),
            relevance_score=item.get("relevance_score"),
            post_text=item.get("post_text"),
        )
        item["status"] = "posted"
        item["posted_at"] = datetime.now(UTC).isoformat() + "Z"
        item["comment_text"] = draft
        _record(spec, item, "posted", draft=draft)
        posted += 1
        print("    ✅ Posted!", flush=True)
        if spec.task_queue is None:
            _save_json(spec.queue_file, queue)

        if idx < len(pending) - 1:
            rate_limiter.wait_random_delay(spec.platform, "comment")

    if spec.task_queue is None:
        _save_json(spec.queue_file, queue)
    return posted, failed, skipped


def _build_parser(label: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Draft + post {label} comments")
    parser.add_argument("--dry-run", action="store_true", help="draft + print; do not post")
    parser.add_argument("--force", action="store_true", help="skip the daily re-run guard")
    parser.add_argument("--limit", type=int, default=None, help="cap items handled this run")
    parser.add_argument("--health-check", action="store_true", help="verify session and exit")
    return parser


def main_for(spec: CommenterSpec) -> int:
    """Entrypoint: parse args, health-check or run under a singleton lock."""
    args = _build_parser(spec.label).parse_args()
    if args.health_check:
        ok = spec.session_file.exists()
        print(
            f"{spec.label} session {'OK' if ok else 'MISSING'} ({spec.session_file})",
            file=sys.stderr,
        )
        return 0 if ok else 1
    # Use a per-slot lock when running as a multi-instance trigger (WORKER_INDEX
    # is set by the API), so ×2/×3 instances don't block each other.
    _worker_index = os.environ.get("WORKER_INDEX", "")
    _lock_name = f"{spec.skill_name}-{_worker_index}" if _worker_index else spec.skill_name
    try:
        with SingletonLock(_lock_name):
            return run_commenter(spec, args)
    except LockAcquisitionError as exc:
        print(f"another instance of {spec.skill_name!r} is running: {exc}", file=sys.stderr)
        return 0
