# pyright: reportMissingImports=false
"""Static descriptions of every social-automation flow.

Consumed by ``GET /api/v1/flows/guide``. Edit here; no JSON file, no UI
editing. Every flow id MUST exist in ``api.flow_state._READERS`` — the
drift guard at module import time enforces this.
"""

from __future__ import annotations

from api.schemas import FlowDescription, JobDescription

FLOW_DESCRIPTIONS: list[FlowDescription] = [
    FlowDescription(
        id="engagement-comment",
        title="Engagement Comment",
        summary=(
            "OUTBOUND engagement. Scans Facebook groups and Instagram hashtags for "
            "relevant posts by other people, drafts a comment inline at scan time, "
            "auto-approves without a human gate, and posts the comment to drive "
            "traffic to dogfoodandfun.com."
        ),
        jobs=[
            JobDescription(
                id="fb-scanner",
                summary="Scans joined FB groups for relevant posts; drafts comments inline and appends to comment_queue.json.",
            ),
            JobDescription(
                id="ig-scanner",
                summary="Scans IG hashtags, likes posts, drafts comments inline, queues them.",
            ),
            JobDescription(
                id="comment-approver",
                summary="Phase 3 — auto-approves pending queued comments (no Telegram round-trip).",
            ),
            JobDescription(
                id="comment-poster",
                summary="Posts approved comments to FB/IG via Playwright/Graph API.",
            ),
        ],
    ),
    FlowDescription(
        id="blog-campaign",
        title="Blog & Campaign Pipeline",
        summary=(
            "OUTBOUND content production. Generates blog ideas, enriches them with "
            "SEO/social research through Telegram-approved gates, drafts WordPress "
            "posts, and publishes prepared recipe campaigns to WP + FB page + IG."
        ),
        jobs=[
            JobDescription(
                id="content-pipeline",
                summary="Multi-stage pipeline (ideate -> enrich -> write -> publish) with Telegram approval gates between stages.",
            ),
            JobDescription(
                id="daily-wp-draft",
                summary="Daily nudge — picks the top-scored approved brief from cache and pings Telegram so the user runs wp-post-creator.",
            ),
            JobDescription(
                id="auto-drafter",
                summary="Fills draft_comment on queued items the template generator can handle so they reach Telegram with lead time.",
            ),
            JobDescription(
                id="content-ideator",
                summary="Generates 5 fresh content ideas, sends them to Telegram, persists approved picks for enrichment.",
            ),
            JobDescription(
                id="content-publish",
                summary="Pushes approved WP posts to Facebook Page + Instagram via Graph API after a final Telegram approval.",
            ),
            JobDescription(
                id="recipe-ideator",
                summary="Generates fresh recipe campaign seeds (4-slide IG carousel + WP post) for the recipe-publisher pipeline.",
            ),
            JobDescription(
                id="recipe-publisher",
                summary="Cron drainer — publishes the next verified recipe campaign in campaigns/prepared/ to WP, IG carousel, and FB.",
            ),
        ],
    ),
    FlowDescription(
        id="brand-campaigns",
        title="Brand Campaigns",
        summary=(
            "OUTBOUND brand campaign worker. Iterates over every campaign in "
            "campaigns_dir, evaluates each campaign's cron schedule against last "
            "run, and executes the configured tasks when ready/ is populated; "
            "moves ready/ -> published/ on success."
        ),
        jobs=[
            JobDescription(
                id="campaign-worker",
                summary="Background worker — reads campaign_config.json + state.json per campaign, runs due tasks, atomically promotes ready -> published.",
            ),
            JobDescription(
                id="publish-campaign-manual",
                summary=(
                    "Manual trigger — POST /api/v1/campaigns/{name}/publish runs "
                    "scripts/publish_campaign.py for one specific campaign. Bypasses "
                    "the cron schedule. Reuses the same per-campaign worker.lock as "
                    "the cron worker, so manual and cron runs can never collide."
                ),
            ),
        ],
    ),
    FlowDescription(
        id="community-growth",
        title="Community Growth",
        summary=(
            "OUTBOUND community expansion, publishing, and reconciliation. Discovers "
            "and joins new dog-related FB groups (Community Expansion), publishes "
            "prepared recipes to eligible groups (Publishing), and reconciles posting "
            "outcomes to verify delivery and engagement (Reconciliation)."
        ),
        jobs=[
            JobDescription(
                id="fb-group-scout",
                category="community_expansion",
                summary="Weekly Sun 15:03 — search FB for dog groups (5/day, 15/week cap); send join requests. --dry-run shows matches; --health-check validates FbSession.",
            ),
            JobDescription(
                id="fb-notification-scan",
                category="community_expansion",
                summary="Daily piggyback — scan FB notifications for 'approved your request to join'; upsert joined groups into groups_tracker.json. Supports --dry-run, --health-check.",
            ),
            JobDescription(
                id="fb-group-post",
                category="publishing",
                summary="Daily — pick next published WP post; distribute to eligible groups (10/day cap, 72h warmup, Telegram approval-before-browser per group). Supports --dry-run, --health-check.",
            ),
            JobDescription(
                id="fb-groups-posting-scan",
                category="reconciliation",
                summary="A few hours post-publish — scan group timelines for our posted content; verify pending_admin_approval → posted/stale_pending transitions. Supports --dry-run, --health-check.",
            ),
            JobDescription(
                id="fb-pending-posts-check",
                category="reconciliation",
                summary="A few hours post-publish — check for pending approvals still waiting in groups; reconcile with groups_tracker. Supports --dry-run, --health-check.",
            ),
        ],
    ),
    FlowDescription(
        id="social-loyalty",
        title="Social Loyalty & Outreach",
        summary=(
            "OUTBOUND second-touch engagement. Revisits our own recent FB comments "
            "to reply to threaded responses (10-30x more profile visits), and "
            "answers visitor comments on our own IG media via Graph API."
        ),
        jobs=[
            JobDescription(
                id="reply-follower",
                summary="Revisits FB posts where we commented, finds new replies under our comment, drafts + Telegram-approves a warm response, posts it.",
            ),
            JobDescription(
                id="reply-follower-morning",
                summary="Morning launchd run of reply-follower — picks up overnight FB replies.",
            ),
            JobDescription(
                id="reply-follower-evening",
                summary="Evening launchd run of reply-follower — picks up daytime FB replies.",
            ),
            JobDescription(
                id="ig-own-comments",
                summary="Hourly — replies to visitor comments on our IG media via Graph API after Telegram approval; 30d rolling seen-id store.",
            ),
        ],
    ),
    FlowDescription(
        id="market-intel",
        title="Market Intelligence & Trends",
        summary=(
            "INBOUND market signals. Refreshes the keyword research cache (IG "
            "hashtags, FB page topics, Google Trends US+CA) so content-ideator "
            "and content-enricher can prioritise topics with real-world demand."
        ),
        jobs=[
            JobDescription(
                id="refresh-trends",
                summary="Slow daily Google Trends refresher (~4am IL) — fetches US+CA trends per pending-idea keyword with 60s sleeps to dodge pytrends rate limits.",
            ),
            JobDescription(
                id="refresh-keyword-research",
                summary="Fast refresh — pulls IG hashtag engagement + FB page topic performance + best-effort Google Trends; writes keyword_research_cache.json.",
            ),
        ],
    ),
    FlowDescription(
        id="content-ideas",
        title="Content Ideas",
        summary=(
            "State-only view. Surfaces the current backlog of content ideas "
            "(generated, approved, enriching, drafted) from local state files "
            "for dashboard visibility; no cron jobs run inside this flow."
        ),
        jobs=[],
    ),
]


def _assert_no_drift() -> None:
    """Fail-fast import-time guard against flow_descriptions / flow_state drift.

    Every flow id declared here must appear in ``api.flow_state._READERS``,
    and vice versa. ``_READERS`` is shaped
    ``list[tuple[str, str, Callable[[], dict[str, Any]]]]`` — ``(id, name, reader)``.
    """
    from api.flow_state import _READERS  # type: ignore[attr-defined]

    declared = {desc.id for desc in FLOW_DESCRIPTIONS}
    runtime = {flow_id for flow_id, _name, _reader in _READERS}
    missing = runtime - declared
    extra = declared - runtime
    if missing:
        raise RuntimeError(f"flow_descriptions missing entries for: {missing}")
    if extra:
        raise RuntimeError(f"flow_descriptions has unknown ids: {extra}")


_assert_no_drift()


__all__ = ["FLOW_DESCRIPTIONS"]
