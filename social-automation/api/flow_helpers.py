# pyright: reportMissingImports=false
"""Shared helpers + per-flow readers for ``api.flow_state``.

Split out of ``flow_state.py`` to keep both files under the 300-line cap
defined in the global rules. Readers MUST NOT raise — the aggregator
treats a missing state file as ``last_status="never"``.

Privacy: ``redact_sample`` walks each sample dict and deletes keys
whose lowercased name contains any of: token / secret / password /
cookie / auth. Applied recursively to nested dicts + lists.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from lib.config import BrandPaths
from lib.config import settings as _settings

_log = logging.getLogger("approval_api.flow_state")

REDACT_KEY_SUBSTRINGS = ("token", "secret", "password", "cookie", "auth")
_ERROR_RE = re.compile(r"traceback|exception|error", re.IGNORECASE)


def paths() -> BrandPaths:
    """Resolve ``settings.paths`` to a non-None ``BrandPaths``.

    ``AppSettings.paths`` is typed Optional but is always populated by
    ``load_config()`` at import time.
    """
    return cast(BrandPaths, _settings.paths)


def redact_sample(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recursively strip sensitive keys from each sample dict."""

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: _walk(v)
                for k, v in obj.items()
                if not any(s in k.lower() for s in REDACT_KEY_SUBSTRINGS)
            }
        if isinstance(obj, list):
            return [_walk(v) for v in obj]
        return obj

    cleaned: list[dict[str, Any]] = []
    for item in items:
        walked = _walk(item)
        if isinstance(walked, dict):
            cleaned.append(walked)
    return cleaned


def read_json(path: Path) -> Any | None:
    """Read JSON file, return None on any failure (missing / malformed)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def last_nonempty_lines(path: Path, n: int) -> list[str]:
    """Return the last ``n`` non-empty lines of ``path`` (best-effort)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def derive_status_and_error(
    log_paths: list[Path],
) -> tuple[str, str | None, datetime | None]:
    """Compute ``(last_status, error_message, last_run_at)`` from cron logs."""
    existing = [p for p in log_paths if p.exists()]
    if not existing:
        return "never", None, None

    newest = max(existing, key=lambda p: p.stat().st_mtime)
    newest_mtime = newest.stat().st_mtime
    last_run_at = datetime.fromtimestamp(newest_mtime, tz=UTC)
    fresh = (time.time() - newest_mtime) < 24 * 3600

    if not fresh:
        return "stale", None, last_run_at

    tail = last_nonempty_lines(newest, 5)
    if tail and any(_ERROR_RE.search(line) for line in tail[-3:]):
        return "error", "\n".join(tail)[:500], last_run_at
    return "ok", None, last_run_at


# ---------------------------------------------------------------------------
# Per-flow readers
# ---------------------------------------------------------------------------


def read_engagement_comment() -> dict[str, Any]:
    p = paths()
    queue_data = read_json(p.comment_queue) or []
    if not isinstance(queue_data, list):
        queue_data = []

    statuses = Counter(item.get("status", "pending") for item in queue_data)
    output_counts = {
        "pending": int(statuses.get("pending", 0)),
        "approved": int(statuses.get("approved", 0)),
        "posted": int(statuses.get("posted", 0)),
        "skipped": int(statuses.get("USER_SKIPPED", 0)),
    }
    posted = [item for item in queue_data if item.get("status") == "posted"]
    sample = redact_sample(posted[-3:])

    log_paths = [
        p.logs_dir / "cron_fb_scan.log",
        p.logs_dir / "cron_ig_scan.log",
        p.logs_dir / "cron_comment_approver.log",
    ]
    last_status, error_message, last_run_at = derive_status_and_error(log_paths)
    return {
        "id": "engagement-comment",
        "name": "Engagement Comment Flow",
        "last_run_at": last_run_at,
        "last_status": last_status,
        "error_message": error_message,
        "output_counts": output_counts,
        "sample": sample,
    }


def read_brand_campaigns() -> dict[str, Any]:
    p = paths()
    campaigns_dir = p.campaigns_dir

    total_campaigns = 0
    total_published_runs = 0
    last_run_at = None
    last_status = "never"
    error_message = None
    sample = []

    if campaigns_dir.exists():
        for campaign_dir_path in sorted(campaigns_dir.iterdir(), key=lambda d: d.name):
            if not campaign_dir_path.is_dir():
                continue

            state_file = campaign_dir_path / "state.json"
            if not state_file.exists():
                continue

            total_campaigns += 1
            state_data = read_json(state_file) or {}
            if not isinstance(state_data, dict):
                continue

            history = state_data.get("history", [])
            if not isinstance(history, list):
                history = []

            runs_success = sum(1 for h in history if isinstance(h, dict) and h.get("status") == "success")
            total_published_runs += runs_success

            campaign_last_run = state_data.get("last_run")
            if campaign_last_run:
                # Keep the absolute latest run time across all campaigns
                if not last_run_at or campaign_last_run > last_run_at:
                    last_run_at = campaign_last_run

                # If any campaign failed recently, mark the overall flow as error
                if history:
                    last_event = history[-1]
                    if isinstance(last_event, dict) and last_event.get("status") == "error":
                        last_status = "error"
                        error_message = f"Error in campaign: {campaign_dir_path.name}"

            sample.append({
                "campaign": campaign_dir_path.name,
                "last_run": campaign_last_run,
                "successful_runs": runs_success
            })

    if total_campaigns > 0 and last_status != "error":
        last_status = "ok"

    output_counts = {
        "active_campaigns": total_campaigns,
        "successful_published_runs": total_published_runs,
    }

    return {
        "id": "brand-campaigns",
        "name": "Brand Campaigns",
        "last_run_at": last_run_at,
        "last_status": last_status,
        "error_message": error_message,
        "output_counts": output_counts,
        "sample": sample[-3:],  # Keep latest 3 for sample
    }

def read_blog_campaign() -> dict[str, Any]:
    p = paths()
    enrichment = read_json(p.state_dir / "enrichment_cache.json") or []
    if not isinstance(enrichment, list):
        enrichment = []
    ideation = read_json(p.state_dir / "ideation_history.json") or {}
    if not isinstance(ideation, dict):
        ideation = {}

    runs_raw = ideation.get("runs")
    runs: list[Any] = runs_raw if isinstance(runs_raw, list) else []
    ideated_total = sum(
        int(r.get("ideas_generated") or 0) for r in runs if isinstance(r, dict)
    )
    approval_counts = Counter(
        e.get("approval_status", "unknown")
        for e in enrichment
        if isinstance(e, dict)
    )
    output_counts = {
        "ideated_total": int(ideated_total),
        "enriched_approved": int(approval_counts.get("approved", 0)),
        "enriched_pending": int(approval_counts.get("pending", 0)),
    }
    sample = redact_sample([e for e in enrichment[-3:] if isinstance(e, dict)])

    last_status, error_message, last_run_at = derive_status_and_error(
        [p.logs_dir / "cron_content_pipeline.log"],
    )
    return {
        "id": "blog-campaign",
        "name": "Blog & Campaign Pipeline",
        "last_run_at": last_run_at,
        "last_status": last_status,
        "error_message": error_message,
        "output_counts": output_counts,
        "sample": sample,
    }


def read_community_growth() -> dict[str, Any]:
    from lib import groups_db

    p = paths()
    groups_data: Any = groups_db.load_all()
    if not isinstance(groups_data, list):
        groups_data = []

    statuses = Counter(
        g.get("status", "unknown") for g in groups_data if isinstance(g, dict)
    )
    output_counts = {
        "joined": int(statuses.get("joined", 0)),
        "join_requested": int(statuses.get("join_requested", 0)),
        "pending": int(statuses.get("pending", 0)),
    }
    joined_sorted = sorted(
        [g for g in groups_data if isinstance(g, dict) and g.get("joined_at")],
        key=lambda g: g.get("joined_at") or "",
        reverse=True,
    )
    sample = redact_sample(joined_sorted[:3])

    last_status, error_message, last_run_at = derive_status_and_error(
        [p.logs_dir / "cron_fb_group_scout.log"],
    )
    return {
        "id": "community-growth",
        "name": "Community Growth Flow",
        "last_run_at": last_run_at,
        "last_status": last_status,
        "error_message": error_message,
        "output_counts": output_counts,
        "sample": sample,
    }


def read_social_loyalty() -> dict[str, Any]:
    p = paths()
    pinterest_files: list[Path] = (
        sorted(p.state_dir.glob("pinterest*.json"))
        if p.state_dir.exists() else []
    )
    pinterest_entries = 0
    pinterest_sample: list[dict[str, Any]] = []
    for pf in pinterest_files:
        data = read_json(pf)
        if isinstance(data, list):
            pinterest_entries += len(data)
            pinterest_sample.extend(d for d in data[-2:] if isinstance(d, dict))
        elif isinstance(data, dict):
            pinterest_entries += len(data)
            pinterest_sample.append(data)

    last_status, error_message, last_run_at = derive_status_and_error([
        p.logs_dir / "cron_reply_follower.log",
        p.logs_dir / "cron_ig_own_comments.log",
    ])

    sample = redact_sample(pinterest_sample[:3]) if pinterest_sample else []
    if not sample:
        tail = last_nonempty_lines(p.logs_dir / "cron_reply_follower.log", 3)
        sample = [{"log_line": ln} for ln in tail]

    return {
        "id": "social-loyalty",
        "name": "Social Loyalty & Outreach",
        "last_run_at": last_run_at,
        "last_status": last_status,
        "error_message": error_message,
        "output_counts": {
            "pinterest_entries": int(pinterest_entries),
            "pinterest_state_files": len(pinterest_files),
        },
        "sample": sample,
    }


def read_market_intel() -> dict[str, Any]:
    p = paths()
    cache_data = read_json(p.state_dir / "keyword_research_cache.json")
    keywords_tracked = 0
    cache_age_hours = 0
    newest_cached_at: datetime | None = None
    sample_pool: list[dict[str, Any]] = []

    if isinstance(cache_data, dict):
        keywords_tracked = len(cache_data)
        for key, val in cache_data.items():
            if not isinstance(val, dict):
                continue
            cached_at = val.get("cached_at")
            if isinstance(cached_at, str):
                try:
                    dt = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
                    if newest_cached_at is None or dt > newest_cached_at:
                        newest_cached_at = dt
                except ValueError:
                    pass
            sample_pool.append({"keyword": key, **(val.get("data") or {})})

    if newest_cached_at is not None:
        delta = datetime.now(UTC) - newest_cached_at
        cache_age_hours = max(0, int(delta.total_seconds() // 3600))

    top = sorted(
        sample_pool,
        key=lambda e: float(e.get("avg_likes") or e.get("score") or 0.0),
        reverse=True,
    )[:3]
    sample = redact_sample(top)

    last_status, error_message, last_run_at = derive_status_and_error(
        [p.logs_dir / "cron_refresh_trends.log"],
    )
    return {
        "id": "market-intel",
        "name": "Market Intelligence & Trends",
        "last_run_at": last_run_at,
        "last_status": last_status,
        "error_message": error_message,
        "output_counts": {
            "keywords_tracked": int(keywords_tracked),
            "cache_age_hours": int(cache_age_hours),
        },
        "sample": sample,
    }


def read_content_ideas() -> dict[str, Any]:
    p = paths()
    hist_path = p.brand_dir / "state" / "ideation_history.json"
    history = read_json(hist_path) or {}

    last_run_at: datetime | None = None
    last_run_str = history.get("last_run")
    if last_run_str:
        try:
            last_run_at = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    # Count approved ideas from enrichment cache
    enrich_path = p.state_dir / "enrichment_cache.json"
    enrich_cache = read_json(enrich_path) or []
    approved_count = 0
    if isinstance(enrich_cache, list):
        approved_count = sum(
            1 for c in enrich_cache if c.get("approval_status") == "approved"
        )

    # Sample of recent runs
    runs = history.get("runs", [])
    sample = redact_sample(runs[-3:]) if isinstance(runs, list) else []

    last_status, error_message, _ = derive_status_and_error(
        [p.logs_dir / "cron_content_ideator.log"]
    )
    if last_status == "never" and last_run_at:
        last_status = "manual"

    return {
        "id": "content-ideas",
        "name": "Content Ideas",
        "last_run_at": last_run_at,
        "last_status": last_status,
        "error_message": error_message,
        "output_counts": {
            "approved_ideas": approved_count,
            "runs_recorded": len(runs) if isinstance(runs, list) else 0,
        },
        "sample": sample,
    }
