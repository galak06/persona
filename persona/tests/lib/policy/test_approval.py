"""Tests for lib.policy.approval — every rule in isolation + precedence."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from lib.config import settings
from lib.policy import (
    ApprovalContext,
    ApprovalDecision,
    requires_approval,
)
from lib.policy.approval import _brand_site_domain, _host_from_url

# The url_in_draft gate matches against the LOADED brand's host, derived from
# settings.site.url — not a hardcoded placeholder. Drive the URL tests off the
# same value production uses so they assert the gate fires for the real site.
BRAND_DOMAIN = _brand_site_domain()


def _item(**overrides: Any) -> dict[str, Any]:
    """Build a fresh Facebook queue item; overrides patch specific fields."""
    base: dict[str, Any] = {
        "platform": "facebook",
        "draft_comment": "Just sharing what worked for us.",
        "group_name": "Some Dog Group",
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────
# Rule 1: manual_flag
# ──────────────────────────────────────────────────────────────────────────


class TestManualFlag:
    def test_explicit_flag_overrides_otherwise_safe_item(self) -> None:
        item = _item(requires_approval=True)
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=True, reason="manual_flag")

    def test_false_flag_does_not_force_approval(self) -> None:
        item = _item(requires_approval=False)
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        result = requires_approval(item, ctx)
        assert result.needed is False


# ──────────────────────────────────────────────────────────────────────────
# Rule 2/3: platform
# ──────────────────────────────────────────────────────────────────────────


class TestPlatformRules:
    def test_instagram_always_requires(self) -> None:
        item = _item(platform="instagram", group_name="", hashtag="#dog")
        ctx = ApprovalContext(previously_posted=frozenset({"#dog"}))
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=True, reason="ig_platform")

    def test_wordpress_always_requires(self) -> None:
        item = _item(platform="wordpress", parent_post_title="Some Post")
        ctx = ApprovalContext(previously_posted=frozenset({"Some Post"}))
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=True, reason="wp_platform")

    def test_facebook_does_not_force_approval_alone(self) -> None:
        item = _item(platform="facebook")
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        assert requires_approval(item, ctx).needed is False


# ──────────────────────────────────────────────────────────────────────────
# Rule 4: url_in_draft
# ──────────────────────────────────────────────────────────────────────────


class TestUrlInDraft:
    def test_url_in_draft_requires_approval(self) -> None:
        item = _item(
            draft_comment=f"We covered this at https://{BRAND_DOMAIN}/x — full breakdown there"
        )
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=True, reason="url_in_draft")

    def test_url_match_is_case_insensitive(self) -> None:
        # Upper-cased brand host must still trip the gate (draft is lowered).
        item = _item(draft_comment=f"See {BRAND_DOMAIN.upper()}")
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        assert requires_approval(item, ctx).reason == "url_in_draft"

    def test_no_url_proceeds(self) -> None:
        item = _item(draft_comment="No site link — just our Nalla story.")
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        assert requires_approval(item, ctx).needed is False

    def test_gate_uses_loaded_brand_domain_not_placeholder(self) -> None:
        # Regression: the gate used to hardcode "your-brand.com", a placeholder
        # that never equals the real brand host — so it NEVER fired in prod.
        # It must now derive the host from settings.site.url and fire for it.
        assert BRAND_DOMAIN == _host_from_url(settings.site.url)
        assert BRAND_DOMAIN  # non-empty host resolved from config
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        real = _item(draft_comment=f"More at https://{BRAND_DOMAIN}/recipes/x")
        assert requires_approval(real, ctx).reason == "url_in_draft"
        # And the old placeholder must NOT be what drives the gate anymore.
        if BRAND_DOMAIN != "your-brand.com":
            placeholder = _item(draft_comment="See your-brand.com/x for more")
            assert requires_approval(placeholder, ctx).reason != "url_in_draft"


# ──────────────────────────────────────────────────────────────────────────
# Rule 5: first_post_to_target
# ──────────────────────────────────────────────────────────────────────────


class TestFirstPostToTarget:
    def test_unknown_group_requires_approval(self) -> None:
        item = _item(group_name="Brand New Group")
        ctx = ApprovalContext(previously_posted=frozenset({"Other Group"}))
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=True, reason="first_post_to_target")

    def test_known_group_does_not_trigger(self) -> None:
        item = _item(group_name="Familiar Group")
        ctx = ApprovalContext(previously_posted=frozenset({"Familiar Group"}))
        assert requires_approval(item, ctx).needed is False

    def test_target_resolution_falls_back_to_hashtag(self) -> None:
        item = _item(group_name="", hashtag="#dogfood")
        # Empty previously_posted → hashtag treated as new
        ctx = ApprovalContext(previously_posted=frozenset())
        result = requires_approval(item, ctx)
        assert result.reason == "first_post_to_target"

    def test_target_resolution_falls_back_to_parent_post_title(self) -> None:
        item = _item(group_name="", hashtag="", parent_post_title="On-site post")
        ctx = ApprovalContext(previously_posted=frozenset())
        result = requires_approval(item, ctx)
        assert result.reason == "first_post_to_target"

    def test_no_target_at_all_skips_first_post_rule(self) -> None:
        # No identifier at all — can't decide "is new", proceeds to next rule
        item = _item(group_name="", hashtag="", parent_post_title="")
        ctx = ApprovalContext(previously_posted=frozenset())
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=False, reason="auto_approved")


# ──────────────────────────────────────────────────────────────────────────
# Rule 6: template_reused_recently
# ──────────────────────────────────────────────────────────────────────────


class TestTemplateReusedRecently:
    def test_reused_within_30_days_requires_approval(self) -> None:
        today = date(2026, 4, 30)
        recent = today - timedelta(days=15)
        snippet_used_before = "Just sharing what worked for us."[:40].lower()
        ctx = ApprovalContext(
            previously_posted=frozenset({"Some Dog Group"}),
            template_usage={"Some Dog Group": {snippet_used_before: recent}},
            today=today,
        )
        item = _item()
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=True, reason="template_reused_recently")

    def test_outside_30_days_does_not_trigger(self) -> None:
        today = date(2026, 4, 30)
        old = today - timedelta(days=45)
        snippet = "Just sharing what worked for us."[:40].lower()
        ctx = ApprovalContext(
            previously_posted=frozenset({"Some Dog Group"}),
            template_usage={"Some Dog Group": {snippet: old}},
            today=today,
        )
        result = requires_approval(_item(), ctx)
        assert result.needed is False

    def test_different_target_not_considered(self) -> None:
        today = date(2026, 4, 30)
        recent = today - timedelta(days=5)
        snippet = "Just sharing what worked for us."[:40].lower()
        ctx = ApprovalContext(
            previously_posted=frozenset({"Some Dog Group", "Other Group"}),
            template_usage={"Other Group": {snippet: recent}},
            today=today,
        )
        result = requires_approval(_item(), ctx)
        assert result.needed is False

    def test_different_snippet_does_not_match(self) -> None:
        today = date(2026, 4, 30)
        recent = today - timedelta(days=5)
        ctx = ApprovalContext(
            previously_posted=frozenset({"Some Dog Group"}),
            template_usage={
                "Some Dog Group": {"completely different opening line".lower(): recent}
            },
            today=today,
        )
        result = requires_approval(_item(), ctx)
        assert result.needed is False


# ──────────────────────────────────────────────────────────────────────────
# Rule 7: auto_approved (none fire)
# ──────────────────────────────────────────────────────────────────────────


class TestAutoApproved:
    def test_known_safe_item_passes(self) -> None:
        item = _item()
        ctx = ApprovalContext(previously_posted=frozenset({"Some Dog Group"}))
        result = requires_approval(item, ctx)
        assert result == ApprovalDecision(needed=False, reason="auto_approved")


# ──────────────────────────────────────────────────────────────────────────
# Rule precedence — earlier rules short-circuit later ones
# ──────────────────────────────────────────────────────────────────────────


class TestPrecedence:
    def test_manual_flag_wins_over_platform(self) -> None:
        item = _item(platform="instagram", requires_approval=True)
        ctx = ApprovalContext()
        assert requires_approval(item, ctx).reason == "manual_flag"

    def test_platform_wins_over_url(self) -> None:
        item = _item(
            platform="instagram",
            draft_comment=f"See {BRAND_DOMAIN}/x for more",
        )
        ctx = ApprovalContext()
        assert requires_approval(item, ctx).reason == "ig_platform"

    def test_url_wins_over_first_post(self) -> None:
        # Draft has URL AND we've never posted to this group.
        # URL rule precedes first_post.
        item = _item(
            draft_comment=f"See {BRAND_DOMAIN}/x",
            group_name="Brand New Group",
        )
        ctx = ApprovalContext(previously_posted=frozenset())
        assert requires_approval(item, ctx).reason == "url_in_draft"

    def test_first_post_wins_over_template_reuse(self) -> None:
        today = date(2026, 4, 30)
        recent = today - timedelta(days=5)
        snippet = "Just sharing what worked for us."[:40].lower()
        item = _item(group_name="Brand New Group")
        # Even if there's a template-usage entry for Brand New Group,
        # the first_post rule fires first because the group isn't in
        # previously_posted.
        ctx = ApprovalContext(
            previously_posted=frozenset(),
            template_usage={"Brand New Group": {snippet: recent}},
            today=today,
        )
        assert requires_approval(item, ctx).reason == "first_post_to_target"


# ──────────────────────────────────────────────────────────────────────────
# Pytest parametric sanity over the platform constant set
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("platform", "expected_reason"),
    [
        ("instagram", "ig_platform"),
        ("wordpress", "wp_platform"),
    ],
)
def test_known_platforms_force_approval(platform: str, expected_reason: str) -> None:
    item = _item(platform=platform)
    ctx = ApprovalContext(
        previously_posted=frozenset({"Some Dog Group"}),
        template_usage={},
    )
    assert requires_approval(item, ctx).reason == expected_reason
