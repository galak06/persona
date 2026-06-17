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

Run locally: ``cd social-automation && python -m api.approval_api``.
"""

from __future__ import annotations

import logging
import os
import sys
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

from api import state
from api.schemas import (
    ActivityEntry,
    ActivityResponse,
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
    MissingFlowEntry,
    MissingFlowsResponse,
    PendingItem,
    PendingResponse,
    RejectBody,
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
from api import routes_helpers as rh
from api.campaigns_api import router as _campaigns_router
from api.engagements_api import router as _engagements_router
from api.recipe_card_api import router as _recipe_card_router
from api.recipes_api import router as _recipes_router
from lib import activity_log
from lib.config import settings
from lib.worker_db import (
    get_all as worker_db_get_all,
    get_one as worker_db_get_one,
    record_start as worker_db_record_start,
)

app = FastAPI(
    title=f"{settings.site.name} Approval API",
    version="0.2.0",
    description="Localhost-only sidecar for parallel web/Telegram approvals.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(_campaigns_router, prefix="/api/v1/campaigns", tags=["campaigns"])
app.include_router(_recipe_card_router, prefix="/api/v1")
app.include_router(_recipes_router, prefix="/api/v1")
app.include_router(_engagements_router, prefix="/api/v1", tags=["engagements"])


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
    from lib import groups_queue

    blog_posts_raw = rh.pending_only(state.read_queue(rh.BLOG_POST_QUEUE_PATH))
    comments_raw = rh.pending_only(state.read_queue(rh.COMMENT_QUEUE_PATH))
    ideas_raw = rh.pending_only(state.read_queue(rh.IDEATOR_QUEUE_PATH))
    campaigns_raw = rh.pending_only(state.read_queue(rh.CAMPAIGN_VERIFY_QUEUE_PATH))

    items: list[CommentItem | BlogPostItem | GroupItem | IdeaItem | SeedItem | CampaignVerifyItem] = []

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
        limit=limit, platform=platform, action=action,
    )
    entries: list[ActivityEntry] = []
    for raw in raw_entries:
        try:
            entries.append(ActivityEntry.model_validate(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed activity row: %s", exc)
    return ActivityResponse(entries=entries, total=total, as_of=rh.now_iso())


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
        assert path is not None  # noqa: S101
        payload = body or ApproveBody()
        return rh.approve_comment(
            path, item_id, decision_status="approved", text=payload.text
        )
    if kind == "blog_post":
        assert path is not None  # noqa: S101
        payload = body or ApproveBody()
        return rh.approve_blog_post(
            path, item_id, channel=channel, text=payload.text,
            fb_caption=payload.fb_caption, ig_caption=payload.ig_caption,
            decision_status="approved",
        )
    if kind in ("idea", "seed", "campaign_verify"):
        assert path is not None  # noqa: S101
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
        assert path is not None  # noqa: S101
        return rh.approve_comment(
            path, item_id, decision_status="USER_SKIPPED", text=None
        )
    if kind == "blog_post":
        assert path is not None  # noqa: S101
        return rh.approve_blog_post(
            path, item_id, channel=None, text=None,
            fb_caption=None, ig_caption=None, decision_status="USER_SKIPPED",
        )
    if kind in ("idea", "seed", "campaign_verify"):
        assert path is not None  # noqa: S101
        return rh.approve_generic(path, item_id, decision_status="USER_SKIPPED")
    return rh.approve_group(
        raw, status_value="USER_SKIPPED", background_tasks=background_tasks,
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
    assert path is not None  # noqa: S101
    if kind == "comment":
        return rh.approve_comment(
            path, item_id, decision_status="edited", text=body.text
        )
    return rh.approve_blog_post(
        path, item_id, channel=None, text=body.text,
        fb_caption=body.fb_caption, ig_caption=body.ig_caption,
        decision_status="edited",
    )


@app.get("/api/v1/facebook/groups", response_model=FacebookGroupsResponse)
def list_facebook_groups() -> FacebookGroupsResponse:
    """List all Facebook groups bucketed by status.

    Merges two sources:
      - groups_tracker.json -> status in {joined, join_requested, rejected}
      - pending_groups.json -> projected with synthetic status="not_joined_yet"
    """
    from lib import groups_db
    from lib.io.jsonio import read_json

    assert settings.paths is not None  # noqa: S101
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
                groups.append(FacebookGroup(
                    group_name=str(p.get("name", "")),
                    group_url=str(p.get("url", "")),
                    status="not_joined_yet",
                    privacy=str(priv) if priv is not None else None,
                    member_count=str(mc) if mc is not None else None,
                ))
    except FileNotFoundError:
        pass
    except Exception as exc:
        _log.warning("Failed to read pending_groups (continuing): %s", exc)

    return FacebookGroupsResponse(groups=groups, total=len(groups), as_of=rh.now_iso())


@app.put("/api/v1/facebook/groups/{group_name}", response_model=FacebookGroup)
def update_facebook_group(group_name: str, body: FacebookGroupUpdateBody) -> FacebookGroup:
    """Update a Facebook group's status / posting_mode in the groups DB."""
    from lib import groups_db

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


_LABEL_RE = __import__("re").compile(r"com\.dogfoodandfun\.[a-z0-9-]+")
_SHORT_LABEL_RE = __import__("re").compile(r"[a-z0-9-]+")
_LABEL_PREFIX = "com.dogfoodandfun."


def _normalize_label(label: str) -> str:
    """Accept task id (dogfood-fb-scanner) or full launchd label (com.dogfoodandfun.fb-scanner).

    Uses label_for_task_id for task ids so the dogfood- prefix is stripped correctly,
    matching how launchd labels are actually generated (dogfood-fb-scanner → com.dogfoodandfun.fb-scanner).
    """
    if label.startswith(_LABEL_PREFIX):
        return label
    from api.schedule_config import label_for_task_id
    mapped = label_for_task_id(label)
    return mapped if mapped else f"{_LABEL_PREFIX}{label}"

_BRAND_DIR = Path(os.environ.get("BRAND_DIR", str(Path(__file__).parent.parent / "dogfoodandfun")))
_BRAND = _BRAND_DIR.name


@app.get("/api/v1/workers", response_model=list[WorkerStatus])
def list_workers() -> list[WorkerStatus]:
    """List all scheduled workers with their last run status from DB."""
    from api.schedule_config import load_schedule_config

    config = load_schedule_config()
    db_rows = {r["worker_label"]: r for r in worker_db_get_all(_BRAND_DIR, _BRAND)}
    results: list[WorkerStatus] = []
    for task in config.tasks:
        extra: dict = task.model_extra or {}
        label = task.id
        row = db_rows.get(label)
        results.append(WorkerStatus(
            label=label,
            title=extra.get("title") or label,
            description=extra.get("description") or "",
            status=row["status"] if row else "never",
            last_run=row["last_run"] if row else None,
            message=row.get("message") if row else None,
        ))
    return results


@app.get("/api/v1/workers/{label}/status", response_model=WorkerStatus)
def worker_status(label: str) -> WorkerStatus:
    """Return the last run status for a single worker."""
    from api.schedule_config import load_schedule_config

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
    )


@app.post("/api/v1/workers/{label}/trigger", response_model=TriggerResponse)
def trigger_worker(label: str) -> TriggerResponse:
    """Fire a scheduled worker on demand by spawning its script/skill directly.

    Label is whitelisted against the ``com.dogfoodandfun.*`` namespace.
    The task definition is read from schedule.json; the script or skill
    is launched as a detached subprocess so the HTTP response returns
    immediately while the job runs in the background.
    """
    import shlex
    import shutil as _shutil
    import subprocess
    import sys

    from api.schedule_config import load_schedule_config, task_for_label

    label = _normalize_label(label)
    if not _LABEL_RE.fullmatch(label):
        raise HTTPException(status_code=400, detail="Invalid label format")

    config = load_schedule_config()
    task = task_for_label(label, config)
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task for label: {label}")

    extra: dict = task.model_extra or {}
    script_str: str | None = extra.get("script")
    extra_args: list[str] = extra.get("args") or []

    if script_str:
        parts = shlex.split(script_str)
        if parts and parts[0] in ("python", "python3"):
            parts[0] = sys.executable
        elif parts and not parts[0].startswith("/"):
            parts = [sys.executable] + parts
        cmd = parts + extra_args
    elif task.skill:
        claude_bin = _shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        cmd = [claude_bin, "--dangerously-skip-permissions", f"/{task.skill}"]
    else:
        return TriggerResponse(ok=False, message="No script or skill defined for task", label=label)

    suffix = label[len("com.dogfoodandfun."):]
    log_name = f"cron_{suffix.replace('-', '_')}.log"
    brand_dir = Path(os.environ.get("BRAND_DIR", str(Path(__file__).parent.parent / "dogfoodandfun")))
    log_path = brand_dir / "logs" / log_name
    pid_path = brand_dir / "logs" / f"{suffix.replace('-', '_')}.pid"
    cwd = str(Path(__file__).parent.parent)
    env = {**os.environ, "BRAND_DIR": str(brand_dir), "PYTHONUNBUFFERED": "1"}

    # Guard: reject if a previous instance is still alive
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text().strip())
            os.kill(existing_pid, 0)  # signal 0 = is-alive probe
            raise HTTPException(
                status_code=409,
                detail=f"Worker already running (pid={existing_pid})",
            )
        except (ValueError, ProcessLookupError, PermissionError):
            pid_path.unlink(missing_ok=True)  # stale PID — clean up

    worker_db_record_start(brand_dir, label, brand_dir.name)

    log_fh = None
    try:
        log_fh = open(log_path, "a")  # noqa: WPS515
    except OSError:
        pass

    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=cwd,
        env=env,
        stdout=log_fh if log_fh is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        close_fds=True,
        start_new_session=True,
    )
    if log_fh is not None:
        log_fh.close()

    pid_path.write_text(str(proc.pid))

    return TriggerResponse(ok=True, message=f"Spawned (pid={proc.pid})", label=label)


@app.get("/api/v1/schedule/missing", response_model=MissingFlowsResponse)
def list_missing_flows() -> MissingFlowsResponse:
    """Return scheduled flows defined in schedule.json that aren't loaded in launchctl."""
    import json
    import subprocess

    assert settings.paths is not None  # noqa: S101

    schedule_file = settings.paths.schedule_file
    if not schedule_file.exists():
        return MissingFlowsResponse(missing=[], as_of=rh.now_iso())

    try:
        defined = json.loads(schedule_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.error("Failed to parse schedule.json: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to parse schedule.json") from exc

    # schedule.json shape: dict with "tasks": list[ {id, ...} ].
    # Tasks have no explicit launchd label; we derive it by stripping the
    # ``dogfood-`` prefix from ``id`` and prepending ``com.dogfoodandfun.``.
    # We also accept an explicit ``label`` / ``launchd_label`` if present.
    def _extract_label(entry: dict) -> str | None:
        lbl = entry.get("label") or entry.get("launchd_label")
        if isinstance(lbl, str) and _LABEL_RE.fullmatch(lbl):
            return lbl
        tid = entry.get("id")
        if isinstance(tid, str):
            suffix = tid.removeprefix("dogfood-")
            candidate = f"com.dogfoodandfun.{suffix}"
            if _LABEL_RE.fullmatch(candidate):
                return candidate
        return None

    defined_labels: list[str] = []
    if isinstance(defined, list):
        entries: list = defined
    elif isinstance(defined, dict):
        raw = defined.get("tasks") or defined.get("flows") or []
        entries = list(raw) if isinstance(raw, (list, tuple)) else list(raw.values())  # type: ignore[union-attr]
    else:
        entries = []

    for entry in entries:
        if isinstance(entry, dict):
            lbl = _extract_label(entry)
            if lbl is not None:
                defined_labels.append(lbl)

    # Query launchctl
    loaded_labels: set[str] = set()
    try:
        result = subprocess.run(
            ["/bin/launchctl", "list"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        for line in result.stdout.splitlines()[1:]:
            if not line:
                continue
            last = line.split("\t")[-1]
            if last.startswith("com.dogfoodandfun."):
                loaded_labels.add(last)
    except subprocess.TimeoutExpired:
        loaded_labels = set()

    home = Path.home()
    missing: list[MissingFlowEntry] = []
    for lbl in defined_labels:
        if lbl in loaded_labels:
            continue
        plist = home / "Library" / "LaunchAgents" / f"{lbl}.plist"
        plist_str = str(plist) if plist.exists() else None
        cmd = f"launchctl bootstrap gui/$(id -u) {plist}"
        missing.append(MissingFlowEntry(label=lbl, plist_path=plist_str, command=cmd))

    return MissingFlowsResponse(missing=missing, as_of=rh.now_iso())


@app.get("/api/v1/workers/{label}/log", response_model=LogTailResponse)
def get_schedule_log(
    label: str,
    lines: int = Query(default=200, ge=1, le=1000),
) -> LogTailResponse:
    """Return the last N lines of the log file for a scheduled job."""
    label = _normalize_label(label)
    if not _LABEL_RE.fullmatch(label):
        raise HTTPException(status_code=400, detail="Invalid label format")

    suffix = label[len("com.dogfoodandfun."):]
    log_name = f"cron_{suffix.replace('-', '_')}.log"
    brand_dir = Path(os.environ.get("BRAND_DIR", str(Path(__file__).parent.parent / "dogfoodandfun")))
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
    from api.schedule_config import load_schedule_config, task_for_label
    from lib.io.jsonio import read_json

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
