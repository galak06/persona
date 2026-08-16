# pyright: reportMissingImports=false
"""FastAPI approval sidecar.

Mirrors the Telegram approval queues over localhost HTTP so a web UI can
decide in parallel. Surfaces:

- ``GET  /api/v1/pending``       → blog-post pairs + group-join candidates
- ``GET  /api/v1/activity``      → tail of engagement_log.jsonl (read-only)
- ``GET  /api/v1/items/{id}``    → single-item lookup
- ``POST /api/v1/items/{id}/approve`` → dispatches by item ``type``
- ``POST /api/v1/items/{id}/reject``  → dispatches by item ``type``
- ``POST /api/v1/items/{id}/edit``    → blog_post only

Engagement comments are fully managed via the web UI.

The route handlers below are thin — dispatch lives in
``api.routes_helpers`` to keep this module under the 300-line cap.

Run locally: ``cd app && python -m api.approval_api``.
"""

from __future__ import annotations

import datetime
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api import state
from api.schemas import (
    ActivityEntry,
    ActivityResponse,
    ActivitySummaryResponse,
    ApproveBody,
    BlogPostItem,
    CampaignVerifyItem,
    CommentItem,
    DecisionResponse,
    EditBody,
    FacebookGroup,
    FacebookGroupsResponse,
    FacebookGroupUpdateBody,
    GroupItem,
    IdeaItem,
    LogTailResponse,
    PendingItem,
    PendingResponse,
    RejectBody,
    ScheduleUpdateRequest,
    SeedItem,
    TriggerResponse,
    WorkerStatus,
)

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent

_log = logging.getLogger("approval_api")
if not _log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)


def _load_secrets() -> None:
    """Best-effort import of ``lib.local_env`` to merge settings.local.json
    into ``os.environ``. We do this at module import so uvicorn workers see
    secrets, and tolerate a missing module so the API still boots in CI."""
    try:
        sys.path.insert(0, str(_REPO_ROOT))
        from lib.local_env import load_local_env
    except ImportError:
        _log.warning("local_env not importable; secrets not auto-loaded")
        return
    loaded = load_local_env()
    _log.info("local_env loaded: %d secrets", loaded)


_load_secrets()

# Import the new lib + helper modules *after* secrets load so any
# module-level config-driven paths resolve correctly.
from croniter import croniter

from api import routes_helpers as rh
from api.brand_flows_api import router as _brand_flows_router
from api.brand_settings_api import router as _brand_settings_router
from api.brands_api import router as _brands_router
from api.campaigns_api import router as _campaigns_router
from api.engagements_api import router as _engagements_router
from api.flow_templates_api import router as _flow_templates_router
from api.ideas_api import router as _ideas_router
from api.ideas_generate_api import router as _ideas_generate_router
from api.oauth_api import router as _oauth_router
from api.oauth_openart_api import router as _oauth_openart_router
from api.recipe_card_api import router as _recipe_card_router
from api.recipes_api import router as _recipes_router
from api.reels_compose_api import router as _reels_compose_router
from api.schedule_config import label_for_task_id, load_schedule_config, task_for_label
from api.session_status_api import router as _session_status_router
from api.social_posts_api import router as _social_posts_router
from api.social_posts_compose_api import router as _social_posts_compose_router
from api.tiktok_candidates_api import router as _tiktok_router
from lib import activity_log, groups_db, groups_queue, schedule_db
from lib.config import default_brand_dir, settings
from lib.io.jsonio import read_json
from lib.rate_limiter import get_daily_status
from lib.worker_db import (
    get_all as worker_db_get_all,
)
from lib.worker_db import (
    get_one as worker_db_get_one,
)
from lib.worker_db import (
    record_complete as worker_db_record_complete,
)
from lib.worker_db import (
    record_start as worker_db_record_start,
)

app = FastAPI(
    title=f"Persona — {settings.site.name}",
    version="0.2.0",
    description="Social automation approval, scheduling, and OAuth management API.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(_campaigns_router, prefix="/api/v1/campaigns", tags=["campaigns"])
app.include_router(_recipe_card_router, prefix="/api/v1")
app.include_router(_recipes_router, prefix="/api/v1")
app.include_router(_engagements_router, prefix="/api/v1", tags=["engagements"])
app.include_router(_ideas_router, prefix="/api/v1", tags=["ideas"])
app.include_router(_social_posts_router, prefix="/api/v1", tags=["social-posts"])
app.include_router(_social_posts_compose_router, prefix="/api/v1", tags=["social-posts"])
app.include_router(_ideas_generate_router, prefix="/api/v1", tags=["ideas"])
app.include_router(_reels_compose_router, prefix="/api/v1", tags=["reels"])
app.include_router(_tiktok_router, prefix="/api/v1", tags=["tiktok"])
app.include_router(_oauth_router, prefix="/api/v1/oauth", tags=["oauth"])
app.include_router(_oauth_openart_router, prefix="/api/v1/oauth", tags=["oauth"])
app.include_router(_brands_router, prefix="/api/v1", tags=["brands"])
app.include_router(_brand_settings_router, prefix="/api/v1", tags=["brands"])
app.include_router(_brand_flows_router, prefix="/api/v1", tags=["brands"])
app.include_router(_flow_templates_router, prefix="/api/v1", tags=["flow-templates"])
app.include_router(_session_status_router, prefix="/api/v1", tags=["sessions"])


@app.get("/api/v1/config")
def get_config():
    """Returns the current site configuration."""
    return {
        "name": settings.site.name,
        "url": settings.site.url,
        "persona": settings.site.brand_persona,
        "mascot": settings.site.mascot_name,
    }


@app.get("/api/v1/pending", response_model=PendingResponse)
def list_pending() -> PendingResponse:
    """All blog-post pairs, group-join candidates, ideas, seeds, campaign-verify items, and comments awaiting a decision."""
    blog_posts_raw = rh.pending_only(state.read_queue(rh.BLOG_POST_QUEUE_PATH))
    comments_raw = rh.pending_only(state.read_queue(rh.COMMENT_QUEUE_PATH))
    ideas_raw = rh.pending_only(state.read_queue(rh.IDEATOR_QUEUE_PATH))
    campaigns_raw = rh.pending_only(state.read_queue(rh.CAMPAIGN_VERIFY_QUEUE_PATH))

    items: list[
        CommentItem | BlogPostItem | GroupItem | IdeaItem | SeedItem | CampaignVerifyItem
    ] = []

    for raw in comments_raw:
        try:
            items.append(rh.to_comment(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed comment %s: %s", raw.get("id"), exc)

    for raw in blog_posts_raw:
        try:
            items.append(rh.to_blog_post(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed blog_post %s: %s", raw.get("id"), exc)

    items.extend(groups_queue.read_pending_groups())

    for raw in ideas_raw:
        try:
            item_type = raw.get("type", "idea")
            if item_type == "seed":
                items.append(rh.to_seed(raw))
            else:
                items.append(rh.to_idea(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed idea/seed %s: %s", raw.get("id"), exc)

    for raw in campaigns_raw:
        try:
            items.append(rh.to_campaign_verify(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed campaign_verify %s: %s", raw.get("id"), exc)

    counts = {
        "comments": sum(1 for i in items if isinstance(i, CommentItem)),
        "blog_posts": sum(1 for i in items if isinstance(i, BlogPostItem)),
        "groups_to_join": sum(1 for i in items if isinstance(i, GroupItem)),
        "ideas": sum(1 for i in items if isinstance(i, IdeaItem)),
        "seeds": sum(1 for i in items if isinstance(i, SeedItem)),
        "campaigns_to_verify": sum(1 for i in items if isinstance(i, CampaignVerifyItem)),
        "total": len(items),
    }
    return PendingResponse(items=items, counts=counts, as_of=rh.now_iso())


@app.get("/api/v1/activity", response_model=ActivityResponse)
def list_activity(
    limit: int = Query(default=50, ge=1, le=500),
    platform: Literal["facebook", "instagram", "wordpress"] | None = Query(default=None),
    action: str | None = Query(default=None),
) -> ActivityResponse:
    """Tail of ``logs/engagement_log.jsonl``, most recent first."""
    raw_entries, total = activity_log.read_recent(
        limit=limit,
        platform=platform,
        action=action,
    )
    entries: list[ActivityEntry] = []
    for raw in raw_entries:
        try:
            entries.append(ActivityEntry.model_validate(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed activity row: %s", exc)
    return ActivityResponse(entries=entries, total=total, as_of=rh.now_iso())


@app.get("/api/v1/activity/summary", response_model=ActivitySummaryResponse)
def activity_summary(
    date: str | None = Query(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Day to tally, YYYY-MM-DD. Defaults to today (UTC).",
    ),
) -> ActivitySummaryResponse:
    """Per-action tally for one day, computed over the whole log.

    Defaults to the UTC day, which is the day everything else that meters
    the estate already counts — the rate limiter, the daily re-run guard,
    and the ``date`` stamp the log writers put on each row. The Dashboard
    relies on that default and echoes back the ``date`` it was given rather
    than deciding the day from the browser clock; the explicit param is for
    looking at any other day.
    """
    day = date or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    counts, total = activity_log.summarize_day(day)
    return ActivitySummaryResponse(date=day, counts=counts, total=total, as_of=rh.now_iso())


@app.get("/api/v1/items/{item_id}")
def get_item(item_id: str) -> PendingItem:
    """Look up a single item by id."""
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, _path, raw = located
    if kind == "comment":
        return rh.to_comment(raw)
    if kind == "blog_post":
        return rh.to_blog_post(raw)
    if kind == "idea":
        return rh.to_idea(raw)
    if kind == "seed":
        return rh.to_seed(raw)
    if kind == "campaign_verify":
        return rh.to_campaign_verify(raw)
    return GroupItem.model_validate(raw)


@app.post("/api/v1/items/{item_id}/approve", response_model=DecisionResponse)
def approve_item(
    item_id: str,
    background_tasks: BackgroundTasks,
    body: ApproveBody | None = None,
    channel: str | None = Query(default=None, pattern="^(both|fb_only|ig_only)$"),
) -> DecisionResponse:
    """Approve an item. Dispatches on type."""
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, path, raw = located
    if kind == "comment":
        assert path is not None
        payload = body or ApproveBody()
        return rh.approve_comment(path, item_id, decision_status="approved", text=payload.text)
    if kind == "blog_post":
        assert path is not None
        payload = body or ApproveBody()
        return rh.approve_blog_post(
            path,
            item_id,
            channel=channel,
            text=payload.text,
            fb_caption=payload.fb_caption,
            ig_caption=payload.ig_caption,
            decision_status="approved",
        )
    if kind in ("idea", "seed", "campaign_verify"):
        assert path is not None
        return rh.approve_generic(path, item_id, decision_status="approved")
    return rh.approve_group(raw, status_value="approved", background_tasks=background_tasks)


@app.post("/api/v1/items/{item_id}/reject", response_model=DecisionResponse)
def reject_item(
    item_id: str,
    background_tasks: BackgroundTasks,
    body: RejectBody | None = None,
) -> DecisionResponse:
    """Reject an item. Dispatches on type. Logs ``body.reason`` (free-form)."""
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, path, raw = located
    if body and body.reason:
        _log.info("reject reason for %s: %s", item_id, body.reason)
    if kind == "comment":
        assert path is not None
        return rh.approve_comment(path, item_id, decision_status="USER_SKIPPED", text=None)
    if kind == "blog_post":
        assert path is not None
        return rh.approve_blog_post(
            path,
            item_id,
            channel=None,
            text=None,
            fb_caption=None,
            ig_caption=None,
            decision_status="USER_SKIPPED",
        )
    if kind in ("idea", "seed", "campaign_verify"):
        assert path is not None
        return rh.approve_generic(path, item_id, decision_status="USER_SKIPPED")
    return rh.approve_group(
        raw,
        status_value="USER_SKIPPED",
        background_tasks=background_tasks,
    )


@app.post("/api/v1/items/{item_id}/edit", response_model=DecisionResponse)
def edit_item(item_id: str, body: EditBody) -> DecisionResponse:
    """Approve with edited content. Only valid for blog_post items."""
    if body.text is None and body.fb_caption is None and body.ig_caption is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="edit requires at least one of: text, fb_caption, ig_caption",
        )
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, path, _raw = located
    if kind == "group_to_join":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="group items have no editable text",
        )
    assert path is not None
    if kind == "comment":
        return rh.approve_comment(path, item_id, decision_status="edited", text=body.text)
    return rh.approve_blog_post(
        path,
        item_id,
        channel=None,
        text=body.text,
        fb_caption=body.fb_caption,
        ig_caption=body.ig_caption,
        decision_status="edited",
    )


@app.get("/api/v1/facebook/groups", response_model=FacebookGroupsResponse)
def list_facebook_groups() -> FacebookGroupsResponse:
    """List all Facebook groups bucketed by status.

    Merges two sources:
      - groups_tracker.json -> status in {joined, join_requested, rejected}
      - pending_groups.json -> projected with synthetic status="not_joined_yet"
    """
    assert settings.paths is not None
    groups: list[FacebookGroup] = []

    try:
        tracker_data = groups_db.load_all()
        if isinstance(tracker_data, list):
            groups.extend(FacebookGroup.model_validate(g) for g in tracker_data)
    except FileNotFoundError:
        pass
    except Exception as exc:
        _log.error("Failed to read groups tracker: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read groups tracker") from exc

    try:
        pending_data = read_json(settings.paths.pending_groups, default=[])
        if isinstance(pending_data, list):
            for p in pending_data:
                if not isinstance(p, dict):
                    continue
                mc = p.get("member_count")
                priv = p.get("privacy")
                groups.append(
                    FacebookGroup(
                        group_name=str(p.get("name", "")),
                        group_url=str(p.get("url", "")),
                        status="not_joined_yet",
                        privacy=str(priv) if priv is not None else None,
                        member_count=str(mc) if mc is not None else None,
                    )
                )
    except FileNotFoundError:
        pass
    except Exception as exc:
        _log.warning("Failed to read pending_groups (continuing): %s", exc)

    return FacebookGroupsResponse(groups=groups, total=len(groups), as_of=rh.now_iso())


@app.put("/api/v1/facebook/groups/{group_name}", response_model=FacebookGroup)
def update_facebook_group(group_name: str, body: FacebookGroupUpdateBody) -> FacebookGroup:
    """Update a Facebook group's status / posting_mode in the groups DB."""
    group = groups_db.get_by_name(group_name)
    if group is None:
        raise HTTPException(status_code=404, detail=f"group {group_name} not found")

    url = str(group["group_url"])
    if body.status is not None:
        groups_db.set_status(url, body.status)
    if body.posting_mode is not None:
        groups_db.set_posting_mode(url, body.posting_mode)

    updated = groups_db.get_by_name(group_name)
    return FacebookGroup.model_validate(updated)


@app.get("/api/v1/health")
def health() -> Response:
    """Liveness probe for launchd / curl. 204 = OK, no body needed."""
    return Response(status_code=204)


_LABEL_RE = __import__("re").compile(r"com\.persona\.[a-z0-9-]+")
_SHORT_LABEL_RE = __import__("re").compile(r"[a-z0-9-]+")
_LABEL_PREFIX = "com.persona."


def _normalize_label(label: str) -> str:
    """Accept task id (dogfood-fb-engager) or full launchd label (com.persona.fb-engager).

    Uses label_for_task_id for task ids so the dogfood- prefix is stripped correctly,
    matching how launchd labels are actually generated (dogfood-fb-engager → com.persona.fb-engager).
    """
    if label.startswith(_LABEL_PREFIX):
        return label
    mapped = label_for_task_id(label)
    return mapped if mapped else f"{_LABEL_PREFIX}{label}"


_BRAND_DIR = Path(os.environ.get("BRAND_DIR", str(default_brand_dir())))
_BRAND = _BRAND_DIR.name


def _cron_of(extra: dict) -> str | None:
    """This task's `schedule.cron`, if the schedule_tasks row has one set."""
    return (extra.get("schedule") or {}).get("cron")


@app.get("/api/v1/workers", response_model=list[WorkerStatus])
def list_workers() -> list[WorkerStatus]:
    """List all scheduled workers with their last run status from DB."""
    config = load_schedule_config()
    all_rows = {r["worker_label"]: r for r in worker_db_get_all(_BRAND_DIR, _BRAND)}
    # Separate base rows from per-instance rows (label format: "{base}--{slot}")
    _instance_pat = re.compile(r"^(.+)--(\d+)$")
    base_rows = {k: v for k, v in all_rows.items() if not _instance_pat.match(k)}
    instance_rows = [
        v | {"_base": m.group(1), "_slot": int(m.group(2))}
        for k, v in all_rows.items()
        if (m := _instance_pat.match(k))
    ]

    task_meta: dict[str, dict] = {}
    results: list[WorkerStatus] = []
    for task in config.tasks:
        extra: dict = task.model_extra or {}
        label = task.id
        task_meta[label] = extra
        row = base_rows.get(label)
        results.append(
            WorkerStatus(
                label=label,
                title=extra.get("title") or label,
                description=extra.get("description") or "",
                status=row["status"] if row else "never",
                last_run=row["last_run"] if row else None,
                message=row.get("message") if row else None,
                re_run_guard=int(
                    task.model_extra.get("re_run_guard", 1) if task.model_extra else 1
                ),
                cron=_cron_of(extra),
            )
        )

    # Append running/recent per-instance rows so the Running tab shows each slot
    _recent_cutoff = 60  # seconds
    for row in sorted(instance_rows, key=lambda r: (r["_base"], r["_slot"])):
        status = row["status"]
        last_run_str = row.get("last_run") or ""
        if status != "running":
            if not last_run_str:
                continue
            try:
                age = time.time() - datetime.datetime.fromisoformat(last_run_str).timestamp()
                if age > _recent_cutoff:
                    continue
            except Exception as exc:
                _log.debug("skipping instance row with unparseable last_run: %s", exc)
                continue
        base = row["_base"]
        slot = row["_slot"]
        meta = task_meta.get(base, {})
        base_title = meta.get("title") or base
        results.append(
            WorkerStatus(
                label=row["worker_label"],
                title=f"{base_title} #{slot + 1}",
                description=meta.get("description") or "",
                status=status,
                last_run=last_run_str or None,
                message=row.get("message"),
                is_instance=True,
                cron=_cron_of(meta),
            )
        )

    return results


@app.get("/api/v1/workers/{label}/status", response_model=WorkerStatus)
def worker_status(label: str) -> WorkerStatus:
    """Return the last run status for a single worker."""
    config = load_schedule_config()
    task = next((t for t in config.tasks if t.id == label), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Worker '{label}' not found")
    extra: dict = task.model_extra or {}
    row = worker_db_get_one(_BRAND_DIR, label, _BRAND)
    return WorkerStatus(
        label=label,
        title=extra.get("title") or label,
        description=extra.get("description") or "",
        status=row["status"] if row else "never",
        last_run=row["last_run"] if row else None,
        message=row.get("message") if row else None,
        cron=_cron_of(extra),
    )


@app.patch("/api/v1/workers/{label}/schedule", response_model=WorkerStatus)
def update_worker_schedule(label: str, body: ScheduleUpdateRequest) -> WorkerStatus:
    """Change a worker's cron timing.

    Only affects when `scripts/task_dispatcher.py`'s croniter due-check next
    fires this flow -- it does not trigger a run. Manual runs stay on the
    brand-scoped Human Mimic path (`POST /brands/{id}/flows/{flow}/run`).
    """
    cron = body.cron.strip()
    if not croniter.is_valid(cron):
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {cron!r}")

    config = load_schedule_config()
    task = next((t for t in config.tasks if t.id == label), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Worker '{label}' not found")

    extra: dict = task.model_extra or {}
    schedule = dict(extra.get("schedule") or {})
    schedule["cron"] = cron
    schedule_db.save_task(None, {"id": task.id, "schedule": schedule})

    return worker_status(label)


class _TriggerBody(BaseModel):
    count: int = 1  # number of parallel worker instances (1-3)
    force: bool = False  # skip the "already ran today" guard
    recipe_ids: list[str] = []
    headless: bool | None = None  # override PLAYWRIGHT_HEADLESS; None = defer to brand.json


def _build_trigger_command(task: Any, extra: dict, body: _TriggerBody) -> list[str] | None:
    """Assemble the subprocess argv for a script- or skill-based task.

    None means neither `script` nor `skill` is defined -- the caller returns
    a `TriggerResponse(ok=False, ...)` rather than treating it as an error.
    """
    script_str: str | None = extra.get("script")
    if script_str:
        parts = shlex.split(script_str)
        if parts and parts[0] in ("python", "python3"):
            parts[0] = sys.executable
        elif parts and not parts[0].startswith("/"):
            parts = [sys.executable] + parts
        cmd = parts + (extra.get("args") or [])
        if body.force:
            cmd = cmd + ["--force"]
        for recipe_id in body.recipe_ids:
            cmd += ["--seed", recipe_id]
        return cmd
    if task.skill:
        claude_bin = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        return [claude_bin, "--dangerously-skip-permissions", f"/{task.skill}"]
    return None


def _enforce_daily_rerun_guard(extra: dict, brand_dir: Path, task_id: str, force: bool) -> None:
    """Raise 409 if this task already ran successfully today and the guard isn't bypassed."""
    if force or int(extra.get("re_run_guard", 1)) == 0:
        return
    today = datetime.date.today().isoformat()
    row = worker_db_get_one(brand_dir, task_id, brand_dir.name)
    if row and row.get("status") == "success" and (row.get("last_run") or "")[:10] == today:
        raise HTTPException(
            status_code=409,
            detail=f"Already ran successfully today ({row['last_run'][:16]}). Use force to run again.",
        )


def _wrap_for_macos_session(cmd: list[str], headless: bool | None) -> list[str]:
    """On macOS the API may run as a daemon (PPID=1, session 0) with no
    Window Server access -- wrap in `launchctl asuser` so it runs inside the
    user's login session and Playwright can open a real Chrome window.
    """
    if platform.system() != "Darwin":
        return cmd
    env_inject: list[str] = []
    if headless is not None:
        env_inject = ["env", f"PLAYWRIGHT_HEADLESS={'1' if headless else '0'}"]
    return ["launchctl", "asuser", str(os.getuid())] + env_inject + cmd


def _pid_path(brand_dir: Path, base: str, count: int, i: int) -> Path:
    return brand_dir / "logs" / (f"{base}_{i}.pid" if count > 1 else f"{base}.pid")


def _find_occupied_pids(brand_dir: Path, base: str, count: int) -> list[int]:
    """Alive PIDs from any slot's existing .pid file; deletes stale
    (dead-process) files it encounters along the way."""
    alive: list[int] = []
    for i in range(count):
        pp = _pid_path(brand_dir, base, count, i)
        if not pp.exists():
            continue
        try:
            pid = int(pp.read_text().strip())
            os.kill(pid, 0)
            alive.append(pid)
        except (ValueError, ProcessLookupError, PermissionError):
            pp.unlink(missing_ok=True)
    return alive


def _spawn_worker_instance(
    cmd: list[str], env: dict[str, str], cwd: str, log_path: Path, pid_path: Path
) -> subprocess.Popen:
    log_fh = None
    try:
        log_fh = open(log_path, "a")
    except OSError:
        pass
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=log_fh if log_fh is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    if log_fh is not None:
        log_fh.close()
    pid_path.write_text(str(proc.pid))
    return proc


def _reap_worker(
    proc: subprocess.Popen,
    pid_path: Path,
    timeout_s: int,
    brand_dir: Path,
    label: str,
    log_path: Path,
) -> None:
    """Wait for a spawned worker to exit (or kill it on timeout), recording the outcome."""
    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
        msg = f"timeout after {timeout_s // 60}m"
        try:
            with log_path.open("a") as lf:
                lf.write(f"\n[timeout] Worker killed — {msg}\n")
        except OSError:
            pass
        worker_db_record_complete(brand_dir, label, brand_dir.name, "error", msg)
    finally:
        if not timed_out:
            status = "error" if (proc.returncode or 0) != 0 else "success"
            worker_db_record_complete(brand_dir, label, brand_dir.name, status)
        pid_path.unlink(missing_ok=True)


def _at_limit_rate_summary(extra: dict, task: Any) -> dict[str, dict[str, int]]:
    """Rate-limit keys currently at zero remaining, scoped to this task's
    platform when it's inferable from its script/skill name."""
    at_limit: dict[str, dict[str, int]] = {}
    try:
        script_hint = (extra.get("script") or task.skill or "").lower()
        if script_hint.startswith("ig") or "ig_" in script_hint or "ig-" in script_hint:
            relevant_prefix = "instagram:"
        elif script_hint.startswith("fb") or "fb_" in script_hint or "fb-" in script_hint:
            relevant_prefix = "facebook:"
        elif "wp_" in script_hint or "wp-" in script_hint:
            relevant_prefix = "wordpress:"
        else:
            relevant_prefix = ""
        for key, status in get_daily_status().items():
            if status["remaining"] == 0 and (
                not relevant_prefix or key.startswith(relevant_prefix)
            ):
                at_limit[key] = status
    except Exception as exc:
        _log.debug("rate_limiter check failed, skipping: %s", exc)
    return at_limit


@app.post("/api/v1/workers/{label}/trigger", response_model=TriggerResponse)
def trigger_worker(label: str, body: _TriggerBody = _TriggerBody()) -> TriggerResponse:
    """Fire a scheduled worker on demand.

    ``body.count`` (1–3) spawns that many parallel instances — useful once
    workers are backed by a Redis task queue so each instance independently
    pops and processes tasks.  Each instance gets its own PID file
    ``<suffix>_<i>.pid``.  If any slot is already occupied the whole
    request is rejected with 409.
    """
    label = _normalize_label(label)
    if not _LABEL_RE.fullmatch(label):
        raise HTTPException(status_code=400, detail="Invalid label format")

    count = max(1, min(3, body.count))

    config = load_schedule_config()
    task = task_for_label(label, config)
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task for label: {label}")

    extra: dict = task.model_extra or {}
    suffix = label[len("com.persona.") :]
    base = suffix.replace("-", "_")
    brand_dir = Path(os.environ.get("BRAND_DIR", str(default_brand_dir())))

    _enforce_daily_rerun_guard(extra, brand_dir, task.id, body.force)

    cmd = _build_trigger_command(task, extra, body)
    if cmd is None:
        return TriggerResponse(ok=False, message="No script or skill defined for task", label=label)

    log_path = brand_dir / "logs" / f"cron_{base}.log"
    cwd = str(Path(__file__).parent.parent)
    # PYTHONPATH ensures `lib/` is importable even when launchctl asuser
    # spawns the child in a different working directory than `cwd`.
    env = {**os.environ, "BRAND_DIR": str(brand_dir), "PYTHONUNBUFFERED": "1", "PYTHONPATH": cwd}
    if body.headless is not None:
        env["PLAYWRIGHT_HEADLESS"] = "1" if body.headless else "0"
    cmd = _wrap_for_macos_session(cmd, body.headless)

    alive = _find_occupied_pids(brand_dir, base, count)
    if alive:
        raise HTTPException(
            status_code=409,
            detail=f"Already running (pid={alive[0]})"
            + (f" +{len(alive) - 1} more" if len(alive) > 1 else ""),
        )

    timeout_s = int(extra.get("timeout_minutes") or 30) * 60
    if count == 1:
        worker_db_record_start(brand_dir, task.id, brand_dir.name)
    else:
        for si in range(count):
            worker_db_record_start(brand_dir, f"{task.id}--{si}", brand_dir.name)

    pids: list[int] = []
    for i in range(count):
        instance_env = {**env, "WORKER_INDEX": str(i), "WORKER_COUNT": str(count)}
        instance_label = task.id if count == 1 else f"{task.id}--{i}"
        pid_path = _pid_path(brand_dir, base, count, i)
        proc = _spawn_worker_instance(cmd, instance_env, cwd, log_path, pid_path)
        pids.append(proc.pid)
        threading.Thread(
            target=_reap_worker,
            args=(proc, pid_path, timeout_s, brand_dir, instance_label, log_path),
            daemon=True,
        ).start()

    msg = f"Spawned {count} instance(s): pids={pids}" if count > 1 else f"Spawned (pid={pids[0]})"
    return TriggerResponse(
        ok=True, message=msg, label=label, rate_limits=_at_limit_rate_summary(extra, task) or None
    )


@app.get("/api/v1/workers/{label}/log", response_model=LogTailResponse)
def get_schedule_log(
    label: str,
    lines: int = Query(default=200, ge=1, le=1000),
) -> LogTailResponse:
    """Return the last N lines of the log file for a scheduled job."""
    label = re.sub(r"--\d+$", "", label)

    # Modern multi-brand task ids (e.g. "dogfoodandfun-ig-engager", as written
    # by scripts/task_worker.py's _write_flow_log) never match the legacy
    # launchd-style com.persona.* convention _normalize_label/_LABEL_RE
    # validate below -- handle them directly rather than mangling them
    # through that legacy path first.
    if label.startswith(f"{_BRAND}-"):
        suffix = label[len(_BRAND) + 1 :]
    else:
        label = _normalize_label(label)
        if not _LABEL_RE.fullmatch(label):
            raise HTTPException(status_code=400, detail="Invalid label format")
        suffix = label[len("com.persona.") :]

    log_name = f"cron_{suffix.replace('-', '_')}.log"
    brand_dir = Path(os.environ.get("BRAND_DIR", str(default_brand_dir())))
    log_path = brand_dir / "logs" / log_name
    max_bytes = 256 * 1024
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()
                truncated = True
            else:
                truncated = False
            raw = f.read()
    except FileNotFoundError:
        return LogTailResponse(label=label, path=str(log_path), lines=[], truncated=False)

    all_lines = raw.decode("utf-8", errors="replace").splitlines()
    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return LogTailResponse(
        label=label,
        path=str(log_path),
        lines=tail,
        truncated=truncated or len(all_lines) > lines,
    )


@app.get("/api/v1/workers/{label}/artifact")
def get_schedule_artifact(label: str) -> dict[str, Any]:
    """Return the JSON content of the output_file for a scheduled job."""
    label = _normalize_label(label)
    config = load_schedule_config()
    task = task_for_label(label, config)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task for {label} not found")

    output_file = getattr(task, "output_file", None)
    if not output_file:
        raise HTTPException(status_code=404, detail=f"No output_file defined for {label}")

    if settings.paths is None:
        raise HTTPException(status_code=500, detail="BRAND_DIR not resolved")
    brand_dir = settings.paths.brand_dir
    path = (brand_dir / output_file).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact {output_file} not found on disk")

    # Safety: ensure path is inside brand_dir
    if brand_dir not in path.parents:
        raise HTTPException(status_code=403, detail="Forbidden: path traversal detected")

    try:
        data = read_json(path, default=None)
        if data is None:
            raise HTTPException(status_code=500, detail=f"Could not parse {output_file} as JSON")
        return {"label": label, "path": output_file, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading artifact: {exc}") from exc


if __name__ == "__main__":  # pragma: no cover - manual run path
    import uvicorn

    host = os.getenv("WEB_UI_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_UI_PORT", "5001"))
    _log.info("starting approval_api on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
