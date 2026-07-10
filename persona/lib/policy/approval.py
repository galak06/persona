"""Single source of truth: should a comment-queue item go through manual approval?

Rule precedence — first match wins; `reason` reflects which fired.

    1. `manual_flag` — item.requires_approval is explicitly True.
       Upstream scanner already decided this needs review.

    2. `ig_platform` — all Instagram comments require approval. IG voice
       diverges from FB (more visual, more risk of brand misalignment),
       so every IG comment goes through Telegram before posting.

    3. `wp_platform` — all WordPress replies are on your-brand.com under
       the Nalla's Dad byline. Every one is brand-facing — manual review
       until we have track record of auto-approval.

    4. `url_in_draft` — the draft references your-brand.com. URLs in
       comments to OTHER people's groups can read as solicitation;
       review before posting.

    5. `first_post_to_target` — never engaged with this target before.
       First impression — review the voice match.

    6. `template_reused_recently` — same template snippet posted in same
       target within the last 30 days. Avoid copy-paste appearance.

    7. `auto_approved` — none of the above fired.

This replaces three drifting implementations:
    - scripts/comment_approver.py:92-102   (no template-reused rule)
    - lib/comment_graph.py:113-124         (no template-reused rule)
    - .claude/skills/comment-composer/SKILL.md:205-211  (template-reused
      documented but never implemented in Python)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lib.policy.approval_context import ApprovalContext
from lib.policy.approval_decision import ApprovalDecision

_PLATFORMS_REQUIRING_APPROVAL: frozenset[str] = frozenset({"instagram", "wordpress"})
_SITE_DOMAIN: str = "your-brand.com"
_TEMPLATE_REUSE_WINDOW_DAYS: int = 30
_TEMPLATE_SNIPPET_CHARS: int = 40


def requires_approval(
    item: dict[str, object],
    ctx: ApprovalContext,
) -> ApprovalDecision:
    """Apply the full approval-policy rule chain.

    Args:
        item: Comment-queue entry. Required keys: `platform` (str). Optional
            keys consulted: `requires_approval` (bool), `draft_comment`
            (str), `group_name` / `hashtag` / `parent_post_title` (str —
            target identifier, the first non-empty wins).
        ctx: Runtime context — engagement history + template usage.

    Returns:
        `ApprovalDecision(needed, reason)`. `reason` is `auto_approved`
        when no rule fires; otherwise the specific rule that did.
    """
    if item.get("requires_approval") is True:
        return ApprovalDecision(needed=True, reason="manual_flag")

    platform = str(item.get("platform", ""))
    if platform == "instagram":
        return ApprovalDecision(needed=True, reason="ig_platform")
    if platform == "wordpress":
        return ApprovalDecision(needed=True, reason="wp_platform")

    draft = str(item.get("draft_comment") or "").lower()
    if _SITE_DOMAIN in draft:
        return ApprovalDecision(needed=True, reason="url_in_draft")

    target = _resolve_target(item)
    if target and target not in ctx.previously_posted:
        return ApprovalDecision(needed=True, reason="first_post_to_target")

    if target and _template_reused_recently(target, draft, ctx):
        return ApprovalDecision(needed=True, reason="template_reused_recently")

    return ApprovalDecision(needed=False, reason="auto_approved")


def _resolve_target(item: dict[str, object]) -> str:
    """Pick the target identifier — first non-empty of group_name, hashtag, parent_post_title."""
    for key in ("group_name", "hashtag", "parent_post_title"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _template_reused_recently(
    target: str,
    draft_lower: str,
    ctx: ApprovalContext,
) -> bool:
    """True if the draft's first 40 chars match a template posted in this
    target within the last 30 days.

    Note: the draft is already lower-cased by the caller, but template
    usage stored in `ctx.template_usage` is keyed by the ORIGINAL casing
    of past comments. To compare consistently we lower-case the stored
    snippets too.
    """
    target_map = ctx.template_usage.get(target)
    if not target_map:
        return False
    snippet = draft_lower[:_TEMPLATE_SNIPPET_CHARS]
    today = ctx.today or datetime.now(UTC).date()
    cutoff = today - timedelta(days=_TEMPLATE_REUSE_WINDOW_DAYS)
    for stored_snippet, used_date in target_map.items():
        if stored_snippet[:_TEMPLATE_SNIPPET_CHARS].lower() != snippet:
            continue
        if used_date >= cutoff:
            return True
    return False
