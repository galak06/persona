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

Engagement comments no longer surface to the web UI — they flow
autonomously through the scanner → inline Gemini draft → comment_poster
pipeline and are reported via ``/activity``. Items still sitting in
``comment_queue.json`` from before that cut-over are visible to
``get_item`` but every approve/reject/edit returns 410 Gone.

The route handlers below are thin — dispatch lives in
``api.routes_helpers`` to keep this module under the 300-line cap.

Run locally: ``cd social-automation && python -m api.approval_api``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Literal

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
    DecisionResponse,
    EditBody,
    GroupItem,
    PendingResponse,
    RejectBody,
    FacebookGroup,
    FacebookGroupsResponse,
    FacebookGroupUpdateBody,
    FlowsStateResponse,
    FlowState,
    LogTailResponse,
    MissingFlowEntry,
    MissingFlowsResponse,
    ScheduleEntry,
    TriggerResponse,
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
from lib import activity_log
from lib.config import settings

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
    """All blog-post pairs + group-join candidates awaiting a decision."""
    blog_posts_raw = rh.pending_only(state.read_queue(rh.BLOG_POST_QUEUE_PATH))
    from lib import groups_queue
    items: list[BlogPostItem | GroupItem] = []
    for raw in blog_posts_raw:
        try:
            items.append(rh.to_blog_post(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed blog_post %s: %s", raw.get("id"), exc)
    items.extend(groups_queue.read_pending_groups())
    counts = {
        "blog_posts": sum(1 for i in items if isinstance(i, BlogPostItem)),
        "groups_to_join": sum(1 for i in items if isinstance(i, GroupItem)),
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
def get_item(item_id: str) -> BlogPostItem | GroupItem:
    """Look up a single item by id. 410 for legacy comments."""
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, _path, raw = located
    if kind == "comment":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "blog_post":
        return rh.to_blog_post(raw)
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
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "blog_post":
        assert path is not None  # noqa: S101 - narrowed by queue_for_id return contract
        payload = body or ApproveBody()
        return rh.approve_blog_post(
            path, item_id, channel=channel, text=payload.text,
            fb_caption=payload.fb_caption, ig_caption=payload.ig_caption,
            decision_status="approved",
        )
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
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "blog_post":
        assert path is not None  # noqa: S101 - narrowed by queue_for_id return contract
        return rh.approve_blog_post(
            path, item_id, channel=None, text=None,
            fb_caption=None, ig_caption=None, decision_status="USER_SKIPPED",
        )
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
    if kind == "comment":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "group_to_join":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="group items have no editable text",
        )
    assert path is not None  # noqa: S101 - narrowed by queue_for_id return contract
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
    from lib.io.jsonio import read_json
    assert settings.paths is not None  # noqa: S101
    groups: list[FacebookGroup] = []

    try:
        tracker_data = read_json(settings.paths.groups_tracker, default=[])
        if isinstance(tracker_data, list):
            groups.extend(FacebookGroup.model_validate(g) for g in tracker_data)
    except FileNotFoundError:
        pass
    except Exception as exc:
        _log.error("Failed to read groups tracker: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read groups tracker")

    try:
        pending_data = read_json(settings.paths.pending_groups, default=[])
        if isinstance(pending_data, list):
            for p in pending_data:
                if not isinstance(p, dict):
                    continue
                mc = p.get("member_count")
                groups.append(FacebookGroup(
                    group_name=p.get("name", ""),
                    group_url=p.get("url", ""),
                    status="not_joined_yet",
                    privacy=p.get("privacy"),
                    member_count=str(mc) if mc is not None else None,
                ))
    except FileNotFoundError:
        pass
    except Exception as exc:
        _log.warning("Failed to read pending_groups (continuing): %s", exc)

    return FacebookGroupsResponse(groups=groups, total=len(groups), as_of=rh.now_iso())

@app.put("/api/v1/facebook/groups/{group_name}", response_model=FacebookGroup)
def update_facebook_group(group_name: str, body: FacebookGroupUpdateBody) -> FacebookGroup:
    """Update a Facebook group's status."""
    assert settings.paths is not None  # noqa: S101
    groups_file = settings.paths.groups_tracker
    if not groups_file.exists():
        raise HTTPException(status_code=404, detail="groups tracker not found")
    import json
    data = json.loads(groups_file.read_text(encoding="utf-8"))
    
    for g in data:
        if g.get("group_name") == group_name:
            if body.status is not None:
                g["status"] = body.status
            if body.posting_mode is not None:
                g["posting_mode"] = body.posting_mode
            groups_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return FacebookGroup.model_validate(g)
            
    raise HTTPException(status_code=404, detail=f"group {group_name} not found")


@app.get("/api/v1/health")
def health() -> Response:
    """Liveness probe for launchd / curl. 204 = OK, no body needed."""
    return Response(status_code=204)


@app.get("/api/v1/flows/state", response_model=FlowsStateResponse)
def get_flows_state() -> FlowsStateResponse:
    """Aggregate per-flow health + launchd schedule snapshot for the UI."""
    from api.flow_state import collect_flow_states, collect_schedule_state
    return FlowsStateResponse(
        flows=[FlowState(**f) for f in collect_flow_states()],
        schedule=[ScheduleEntry(**s) for s in collect_schedule_state()],
    )


_LABEL_RE = __import__("re").compile(r"com\.dogfoodandfun\.[a-z0-9-]+")


@app.post("/api/v1/schedule/{label}/trigger", response_model=TriggerResponse)
def trigger_schedule(label: str, force: bool = Query(default=False)) -> TriggerResponse:
    """Fire a launchd job on demand via ``launchctl start <label>``.

    Label is whitelisted against the ``com.dogfoodandfun.*`` namespace
    to keep this from being abused as a generic launchctl runner. All
    subprocess args are list-form; no shell. Unless ``force=true`` is
    passed, the task's declared inputs in ``schedule.json`` must be
    fresh -- failing a precondition short-circuits with a 200 body
    carrying ``ok=False`` and the human-readable reason.
    """
    import subprocess
    if not _LABEL_RE.fullmatch(label):
        raise HTTPException(status_code=400, detail="Invalid label format")
    if not force:
        from api.schedule_config import (
            check_inputs_satisfied,
            load_schedule_config,
            task_for_label,
        )
        config = load_schedule_config()
        task = task_for_label(label, config)
        if task is not None and task.inputs:
            ok, statuses = check_inputs_satisfied(task)
            if not ok:
                first_fail = next((s for s in statuses if not s.ok), None)
                reason = first_fail.reason if first_fail else "input check failed"
                return TriggerResponse(
                    ok=False,
                    message=f"Prerequisite not satisfied: {reason}",
                    label=label,
                )
    try:
        result = subprocess.run(  # noqa: S603 - launchctl is a trusted system binary
            ["/bin/launchctl", "start", label],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return TriggerResponse(ok=False, message="launchctl timed out", label=label)
    if result.returncode != 0:
        return TriggerResponse(
            ok=False,
            message=(result.stderr or "launchctl exited non-zero").strip()[:200],
            label=label,
        )
    return TriggerResponse(ok=True, message="Triggered", label=label)


@app.get("/api/v1/schedule/missing", response_model=MissingFlowsResponse)
def list_missing_flows() -> MissingFlowsResponse:
    """Return scheduled flows defined in schedule.json that aren't loaded in launchctl."""
    import json
    import subprocess
    assert settings.paths is not None  # noqa: S101 - BRAND_DIR-bound at startup

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
        result = subprocess.run(  # noqa: S603 - launchctl is a trusted system binary
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


@app.get("/api/v1/schedule/{label}/log", response_model=LogTailResponse)
def get_schedule_log(
    label: str,
    lines: int = Query(default=200, ge=1, le=1000),
) -> LogTailResponse:
    """Return the last N lines of the log file for a scheduled job.

    Label is whitelisted against the ``com.dogfoodandfun.*`` namespace.
    Log path is read from the matching plist's ``StandardOutPath`` —
    never user-supplied — so we cannot be coerced into reading arbitrary
    files via path traversal.
    """
    import plistlib

    if not _LABEL_RE.fullmatch(label):
        raise HTTPException(status_code=400, detail="Invalid label format")

    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    try:
        with plist_path.open("rb") as f:
            plist = plistlib.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"label {label} not found")

    log_path_str = plist.get("StandardOutPath")
    if not log_path_str:
        return LogTailResponse(label=label, path=None, lines=[], truncated=False)

    log_path = Path(log_path_str)
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


@app.get("/api/v1/schedule/{label}/artifact")
def get_schedule_artifact(label: str) -> dict[str, Any]:
    """Return the JSON content of the output_file for a scheduled job.

    Securely reads only files declared in schedule.json as output_file.
    """
    from api.schedule_config import task_for_label, load_schedule_config

    config = load_schedule_config()
    task = task_for_label(label, config)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task for {label} not found")

    output_file = getattr(task, "output_file", None)
    if not output_file:
        raise HTTPException(status_code=404, detail=f"No output_file defined for {label}")

    path = (rh.paths().brand_dir / output_file).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact {output_file} not found on disk")

    # Safety: ensure path is inside brand_dir
    if rh.paths().brand_dir not in path.parents:
        raise HTTPException(status_code=403, detail="Forbidden: path traversal detected")

    try:
        data = rh.read_json(path)
        if data is None:
             raise HTTPException(status_code=500, detail=f"Could not parse {output_file} as JSON")
        return {"label": label, "path": output_file, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading artifact: {exc}")


if __name__ == "__main__":  # pragma: no cover - manual run path
    import uvicorn

    host = os.getenv("WEB_UI_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_UI_PORT", "5001"))
    _log.info("starting approval_api on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
