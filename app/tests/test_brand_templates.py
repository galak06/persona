"""Tests for `lib/brand_templates.py` (pure rendering, no I/O/DB).

No Postgres needed -- every function here is a plain `spec -> str | dict`
transform. The `render_config_json` regression guard round-trips the output
through the real `AppSettings` Pydantic model (`lib/config.py`) so a schema
drift there is caught immediately.
"""

from __future__ import annotations

import csv
import io

import pytest

from lib.brand_templates import (
    BrandSpec,
    render_brand_facts_md,
    render_brand_json,
    render_config_json,
    render_instagram_hashtags_csv,
)
from lib.config import AppSettings

FULL_SPEC = BrandSpec(
    name="Acme Dogs",
    site_url="https://acmedogs.example",
    niche="dog nutrition",
    target_audience="new dog owners",
    mascot_name="Rex",
    brand_persona="Rex's Human",
    instagram_profile_url="https://instagram.com/acmedogs",
    facebook_page_url="https://facebook.com/acmedogs",
    primary_keywords=["dog food", "nutrition"],
    secondary_keywords=["gps", "running"],
    competitor_mentions=["brand x"],
    competitor_accounts=["@rival1"],
)

MINIMAL_SPEC = BrandSpec(name="X Co", site_url="https://x.example", niche="widgets")


# --------------------------------------------------------------- render_config_json


def test_render_config_json_round_trips_through_app_settings_full_spec() -> None:
    settings = AppSettings(**render_config_json(FULL_SPEC))
    assert settings.site.name == "Acme Dogs"
    assert settings.site.url == "https://acmedogs.example"
    assert settings.site.mascot_name == "Rex"
    assert settings.site.brand_persona == "Rex's Human"


def test_render_config_json_round_trips_through_app_settings_minimal_spec() -> None:
    settings = AppSettings(**render_config_json(MINIMAL_SPEC))
    assert settings.site.name == "X Co"
    assert settings.site.mascot_name == ""


def test_render_config_json_writes_keywords_and_competitor_accounts_explicitly() -> None:
    data = render_config_json(FULL_SPEC)
    keywords = data["content_analysis"]["keywords"]
    assert keywords == {
        "primary_keywords": ["dog food", "nutrition"],
        "secondary_keywords": ["gps", "running"],
        "competitor_mentions": ["brand x"],
    }
    assert data["content_analysis"]["competitor_accounts"] == ["@rival1"]


def test_render_config_json_omits_empty_keyword_categories() -> None:
    """A brand with no keywords renders an EMPTY keywords dict (categories
    omitted, never written as `[]`). An omitted category is what lets
    comment_generator.score_relevance fall back to its broad DEFAULT_* lists,
    so a freshly onboarded brand scores posts usefully instead of a
    present-but-empty list shadowing the defaults and collapsing every
    relevance score to ~0. competitor_accounts is a separate key (defaults []).
    """
    data = render_config_json(MINIMAL_SPEC)
    assert data["content_analysis"]["keywords"] == {}
    assert data["content_analysis"]["competitor_accounts"] == []
    # The (required) outer keywords key must still round-trip through AppSettings.
    AppSettings(**data)


def test_render_config_json_omits_only_the_empty_keyword_categories() -> None:
    """Partial keyword input: supplied categories are written verbatim, empty
    ones are omitted so each falls back to its own DEFAULT_* list independently
    (rather than one blank category shadowing its default)."""
    spec = BrandSpec(
        name="Partial",
        site_url="https://partial.example",
        niche="dog food",
        primary_keywords=["dog food", "kibble"],
        # secondary_keywords / competitor_mentions deliberately left empty.
    )
    keywords = render_config_json(spec)["content_analysis"]["keywords"]
    assert keywords == {"primary_keywords": ["dog food", "kibble"]}


def test_render_config_json_forces_twitter_and_tiktok_disabled() -> None:
    data = render_config_json(FULL_SPEC)
    assert data["social_channels"]["twitter"]["enabled"] is False
    assert data["social_channels"]["tiktok"]["enabled"] is False


def test_render_config_json_enables_instagram_and_facebook_with_supplied_urls() -> None:
    data = render_config_json(FULL_SPEC)
    assert data["social_channels"]["instagram"]["enabled"] is True
    assert data["social_channels"]["instagram"]["profile_url"] == "https://instagram.com/acmedogs"
    assert data["social_channels"]["facebook"]["enabled"] is True
    assert data["social_channels"]["facebook"]["page_url"] == "https://facebook.com/acmedogs"


def test_render_config_json_does_not_leak_dogfoodandfun_recipe_card_values() -> None:
    """recipe_card is out of Stage-1 scope; a new brand must never inherit
    dogfoodandfun's specific WP media id / font paths / header title."""
    data = render_config_json(FULL_SPEC)
    assert "recipe_card" not in data
    settings = AppSettings(**data)
    assert settings.recipe_card.stamp_media_id == 0
    assert settings.recipe_card.header_title != "Nalla Recipe Card"


@pytest.mark.parametrize(
    "block", ["rate_limits", "approval_gates", "deduplication", "file_paths", "voice_validation"]
)
def test_render_config_json_includes_engine_default_blocks(block: str) -> None:
    data = render_config_json(MINIMAL_SPEC)
    assert block in data
    assert data[block]  # non-empty


# ------------------------------------------------------------- render_brand_facts_md


def test_render_brand_facts_md_uses_verbatim_text_for_filled_fields() -> None:
    text = render_brand_facts_md(FULL_SPEC)
    assert 'Persona: "Rex\'s Human" — the voice behind Acme Dogs.' in text
    assert "Mascot: Rex" in text
    assert "Niche: dog nutrition" in text
    assert "We write for: new dog owners" in text


def test_render_brand_facts_md_never_invents_content_for_blank_fields() -> None:
    text = render_brand_facts_md(MINIMAL_SPEC)
    # No persona/mascot/target_audience were supplied -- must be TODOs, and
    # the literal FULL_SPEC values must not leak in from anywhere.
    assert "Rex" not in text
    assert "Rex's Human" not in text
    assert text.count("<!-- TODO (owner):") >= 5


def test_render_brand_facts_md_has_all_six_sections() -> None:
    text = render_brand_facts_md(FULL_SPEC)
    for header in (
        "## Who we are",
        "## What we genuinely do",
        '## Hard "do NOT claim" guardrails',
        "## Rex's diet",
        "## Gear we actually use",
        "## Real experiences to draw on",
    ):
        assert header in text


def test_render_brand_facts_md_diet_gear_experiences_are_always_todo() -> None:
    """BrandSpec carries no diet/gear/anecdote fields yet -- those three
    sections must always be TODO, even for a fully-filled spec."""
    text = render_brand_facts_md(FULL_SPEC)
    diet_section = text.split("## Rex's diet")[1].split("## Gear")[0]
    gear_section = text.split("## Gear we actually use")[1].split("## Real experiences")[0]
    experiences_section = text.split("## Real experiences to draw on")[1]
    assert "<!-- TODO (owner):" in diet_section
    assert "<!-- TODO (owner):" in gear_section
    assert "<!-- TODO (owner):" in experiences_section


def test_render_brand_facts_md_title_falls_back_to_brand_name_without_mascot() -> None:
    text = render_brand_facts_md(MINIMAL_SPEC)
    assert text.startswith("# X Co Facts")


# --------------------------------------------------------- render_instagram_hashtags_csv


def test_render_instagram_hashtags_csv_header_only_when_no_keywords() -> None:
    csv_text = render_instagram_hashtags_csv(MINIMAL_SPEC)
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows == [["hashtag", "tier", "scan_frequency", "category", "notes"]]


def test_render_instagram_hashtags_csv_shape_matches_reference_columns() -> None:
    csv_text = render_instagram_hashtags_csv(FULL_SPEC)
    reader = csv.DictReader(io.StringIO(csv_text))
    assert reader.fieldnames == ["hashtag", "tier", "scan_frequency", "category", "notes"]
    rows = list(reader)
    assert len(rows) == 4  # 2 primary + 2 secondary


def test_render_instagram_hashtags_csv_tiers_and_frequencies() -> None:
    csv_text = render_instagram_hashtags_csv(FULL_SPEC)
    rows = {row["hashtag"]: row for row in csv.DictReader(io.StringIO(csv_text))}

    assert rows["#dogfood"]["tier"] == "1"
    assert rows["#dogfood"]["scan_frequency"] == "daily"
    assert rows["#nutrition"]["tier"] == "1"

    assert rows["#gps"]["tier"] == "2"
    assert rows["#gps"]["scan_frequency"] == "every_2_days"
    assert rows["#running"]["tier"] == "2"


def test_render_instagram_hashtags_csv_derives_hashtags_mechanically() -> None:
    spec = BrandSpec(
        name="Z",
        site_url="https://z.example",
        niche="n",
        primary_keywords=["Dog Food", "GPS Tracker!"],
    )
    csv_text = render_instagram_hashtags_csv(spec)
    tags = [row["hashtag"] for row in csv.DictReader(io.StringIO(csv_text))]
    assert tags == ["#dogfood", "#gpstracker"]


# ------------------------------------------------------------------- render_brand_json


def test_render_brand_json_defaults() -> None:
    assert render_brand_json(MINIMAL_SPEC) == {
        "runtime": {"headless": True},
        "group_discovery": {"join_limit_per_day": 10},
    }


def test_render_brand_json_reflects_headless_false() -> None:
    spec = BrandSpec(name="X Co", site_url="https://x.example", niche="widgets", headless=False)
    assert render_brand_json(spec)["runtime"] == {"headless": False}


def test_render_brand_json_reflects_group_join_limit() -> None:
    spec = BrandSpec(name="X Co", site_url="https://x.example", niche="widgets", group_join_limit=3)
    assert render_brand_json(spec)["group_discovery"] == {"join_limit_per_day": 3}


def test_brand_spec_default_enabled_flows() -> None:
    assert MINIMAL_SPEC.enabled_flows == ["ig-scanner", "fb-scanner"]


def test_brand_spec_enabled_flows_can_include_fb_group_scout() -> None:
    spec = BrandSpec(
        name="X Co",
        site_url="https://x.example",
        niche="widgets",
        enabled_flows=["ig-scanner", "fb-scanner", "fb-group-scout"],
    )
    assert "fb-group-scout" in spec.enabled_flows
