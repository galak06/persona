# pyright: reportMissingImports=false
"""Pydantic v2 models for the approval API.

The pending feed is a discriminated union on the ``type`` field so the web
UI can render blog-post pairs and group-join candidates from a single
endpoint. Engagement comments no longer surface to the web UI — they flow
autonomously (scanner → inline Gemini draft → comment_queue.json with
``decided_by=auto`` → comment_poster cron). The ``CommentItem`` schema is
kept around for backward-compat with legacy items still sitting in
``comment_queue.json``; the API never returns it from ``/pending``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.schedule_schemas import InputStatus as InputStatus
from api.schedule_schemas import LogTailResponse as LogTailResponse
from api.schedule_schemas import MissingFlowEntry as MissingFlowEntry
from api.schedule_schemas import MissingFlowsResponse as MissingFlowsResponse
from api.schedule_schemas import ScheduleEntry as ScheduleEntry
from api.schedule_schemas import TriggerResponse as TriggerResponse

PlatformLiteral = Literal["facebook", "instagram", "wordpress", "system"]
StatusLiteral = Literal[
    "pending",
    "approved",
    "USER_SKIPPED",
    "edited",
]
DecidedByLiteral = Literal["telegram", "web_ui", "auto"]
ChannelLiteral = Literal["both", "fb_only", "ig_only"]
PrivacyLiteral = Literal["public", "private"]
ActionLiteral = Literal[
    "comment",
    "like",
    "group_post",
    "reply",
    "own_reply",
    "page_post",
    "feed_post",
    "group_join",
    "trace",
]


class _ItemBase(BaseModel):
    """Common fields across every queue item shape."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str = "pending"
    decided_by: DecidedByLiteral | None = None
    decided_at: str | None = None
    created_at: str | None = None


class CommentItem(_ItemBase):
    """A Facebook / Instagram / WordPress engagement comment.

    Legacy schema — engagement comments no longer flow through the web UI,
    but the model is retained so the autonomous pipeline (comment_poster)
    and legacy items in ``comment_queue.json`` can still validate.
    """

    type: Literal["comment"] = "comment"
    platform: PlatformLiteral
    group_or_hashtag: str | None = None
    post_url: str | None = None
    post_text: str = ""
    draft_comment: str = ""
    relevance_score: float | None = None


class BlogPostItem(_ItemBase):
    """A WordPress post awaiting FB + IG caption sign-off.

    ``channel`` is the per-platform fan-out decision set by the approver:

    - Web UI: ``POST /items/{id}/approve?channel=fb_only`` stamps this field
      before flipping ``status`` to ``approved``.
    - Telegram: the reply parser in ``lib/notifier.py`` maps text replies
      (``approve``/``fb``/``ig``) onto ``"both"``/``"fb_only"``/``"ig_only"``.

    The publisher (``scripts/content_pipeline.py::stage_publish``) reads
    ``channel`` after ``send_and_wait`` returns to decide which platforms to
    actually post to. ``None`` is valid only while the item is still pending
    or was skipped.
    """

    type: Literal["blog_post"] = "blog_post"
    post_title: str = ""
    post_url: str = ""
    post_id: int = 0
    fb_caption: str = ""
    ig_caption: str = ""
    image_url: str | None = None
    channel: ChannelLiteral | None = None


class GroupItem(_ItemBase):
    """A Facebook group surfaced by ``fb_group_scout`` awaiting a join
    decision.

    Producer side (``lib/group_discovery/state.py:add_to_pending``) writes
    the discovery payload without ``id``/``status``/``decided_by`` — the API
    layer synthesises those when reading (see ``lib/groups_queue.py``). The
    ``model_config = extra="allow"`` inherited from ``_ItemBase`` keeps the
    extra producer fields (``post_frequency``, ``competitor_names``,
    ``description``) intact for the UI even though they aren't declared.
    """

    type: Literal["group_to_join"] = "group_to_join"
    name: str
    url: str
    member_count: int | None = None
    score: float | None = None
    privacy: PrivacyLiteral | None = None
    found_via_query: str | None = None
    competitor_mentions: int | None = None
    added_to_pending: str  # ISO-8601 date


PendingItem = Annotated[
    CommentItem | BlogPostItem | GroupItem,
    Field(discriminator="type"),
]

# Legacy alias retained for callers that still import ``QueueItem``. The
# /pending endpoint now only surfaces blog posts and group-join items;
# CommentItem is read by comment_poster directly, never via the API union.
QueueItem = Annotated[
    CommentItem | BlogPostItem | GroupItem,
    Field(discriminator="type"),
]


class PendingResponse(BaseModel):
    """Envelope for ``GET /api/v1/pending``."""

    items: list[PendingItem]
    counts: dict[str, int]
    as_of: str


class ActivityEntry(BaseModel):
    """One row in ``logs/engagement_log.jsonl`` as exposed by ``/activity``.

    Some legacy rows in the JSONL file use ``action="group_join_request"``;
    the API layer normalises those to ``"group_join"`` before constructing
    this model so the frontend only ever sees the canonical value. Older
    rows may also lack the ``date`` field — ``lib.activity_log`` derives
    it from ``timestamp`` so the UI can rely on both being present.
    """

    model_config = ConfigDict(extra="allow")

    date: str
    timestamp: str
    action: ActionLiteral
    platform: PlatformLiteral
    target_name: str | None = None
    target_url: str | None = None
    content: str | None = None  # truncated to 200 chars by the writer
    reply_url: str | None = None  # populated when poster captures the URL


class ActivityResponse(BaseModel):
    """Envelope for ``GET /api/v1/activity``."""

    entries: list[ActivityEntry]
    total: int  # total entries in the file, before tail-limit
    as_of: str


class ApproveBody(BaseModel):
    """Body for ``POST /items/{id}/approve``.

    All fields optional: a bare approve just commits the existing draft.
    Pass ``text`` to override a comment, or ``fb_caption``/``ig_caption``
    to override a blog post pair before approval.
    """

    text: str | None = None
    fb_caption: str | None = None
    ig_caption: str | None = None


class RejectBody(BaseModel):
    """Body for ``POST /items/{id}/reject``. ``reason`` is free-form."""

    reason: str | None = None


class EditBody(BaseModel):
    """Body for ``POST /items/{id}/edit``.

    Either ``text`` (comment items) or one/both of ``fb_caption`` /
    ``ig_caption`` (blog-post items) must be set — enforced in the route
    handler so the response is a clean 422 instead of an opaque ValidationError.
    """

    text: str | None = None
    fb_caption: str | None = None
    ig_caption: str | None = None


class DecisionResponse(BaseModel):
    """Returned on every commit endpoint."""

    id: str
    status: str
    decided_by: DecidedByLiteral
    decided_at: str
    join_status: Literal["queued"] | None = None


class ErrorResponse(BaseModel):
    """Shape for 404 / 409 / 410 / 429 error bodies."""

    detail: str
    code: str

class FacebookGroup(BaseModel):
    """A Facebook group from groups_tracker.json."""
    model_config = ConfigDict(extra="allow")
    group_name: str
    group_url: str
    status: str
    joined_at: str | None = None
    rules: str | None = None
    last_post_at: str | None = None
    source_notification: str | None = None
    privacy: str | None = None
    member_count: str | None = None
    posting_mode: str | None = None
    notes: list[dict[str, str]] | None = None
    last_post_status: str | None = None
    last_post_caption: str | None = None

    @field_validator("member_count", mode="before")
    @classmethod
    def _coerce_member_count(cls, v: Any) -> str | None:
        """Coerce raw ints (e.g. 87000) to strings; pass None/str through."""
        if v is None:
            return None
        if isinstance(v, int):
            return str(v)
        return v

class FacebookGroupsResponse(BaseModel):
    """Envelope for GET /api/v1/facebook/groups."""
    groups: list[FacebookGroup]
    total: int
    as_of: str

class FacebookGroupUpdateBody(BaseModel):
    """Body for PUT /api/v1/facebook/groups/{group_name}."""
    status: str | None = None
    posting_mode: str | None = None


FlowStatusLiteral = Literal["ok", "error", "never", "stale", "manual"]


class FlowState(BaseModel):
    """One row in the ``/flows/state`` response — health snapshot of a flow.

    Six top-level flows surface to the UI: engagement-comment, blog-campaign,
    community-growth, social-loyalty, market-intel, content-ideas. Each
    reader assembles its own ``output_counts`` shape (keys vary per flow);
    the UI renders them generically. ``sample`` is a small (≤3) tail of the
    most recent items the flow produced, redacted of any token/secret/
    password/cookie/auth-keyed fields before serialisation.
    """

    id: str
    name: str
    last_run_at: datetime | None = None
    last_status: FlowStatusLiteral
    error_message: str | None = None
    output_counts: dict[str, int]
    sample: list[dict[str, Any]]


class FlowsStateResponse(BaseModel):
    """Envelope for ``GET /api/v1/flows/state``."""

    flows: list[FlowState]
    schedule: list[ScheduleEntry]


class JobDescription(BaseModel):
    """One-liner describing a single cron job inside a flow."""

    id: str
    summary: str
    category: str | None = None


class FlowDescription(BaseModel):
    """Static, code-defined description of a top-level flow."""

    id: str
    title: str
    summary: str
    jobs: list[JobDescription] = []


class FlowGuideEntry(FlowDescription):
    """A flow description merged with its live last-run state."""

    last_run_at: datetime | None = None
    last_status: str | None = None


class FlowGuideResponse(BaseModel):
    """Response shape for ``GET /api/v1/flows/guide``."""

    flows: list[FlowGuideEntry]


CampaignStatusLiteral = Literal["success", "error", "never"]


class CampaignSummary(BaseModel):
    """Summary row for ``GET /api/v1/campaigns``.

    Aggregates the on-disk campaign config + state.json + ready/published
    folder counts into a single UI-friendly shape. ``last_status`` is
    derived from ``state.history[-1].status`` and defaults to ``"never"``
    when the campaign has no run history yet.
    """

    name: str
    last_run: datetime | None = None
    current_task_index: int = 0
    last_status: CampaignStatusLiteral = "never"
    ready_count: int = 0
    published_count: int = 0
    has_prepare_tasks: bool = False
    has_publish_tasks: bool = False


class CampaignDetail(CampaignSummary):
    """Detail payload for ``GET /api/v1/campaigns/{name}``.

    Extends ``CampaignSummary`` with the full run history list so the UI
    can render the timeline view without a second round-trip.
    """

    history: list[dict[str, Any]] = []


class CampaignListResponse(BaseModel):
    """Envelope for ``GET /api/v1/campaigns``."""

    campaigns: list[CampaignSummary]
