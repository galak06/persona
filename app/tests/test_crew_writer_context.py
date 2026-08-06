"""Tests for lib.crew.writer.context -- data summarizers + defensive sanitizing.

No CrewAI, no network, no DATABASE_URL -- pure filesystem fixtures + pure
functions, same posture as test_crew_scout.py's non-kickoff tests.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

from lib.crew.writer.context import (
    filter_links_to_allowed,
    idea_priority,
    internal_link_candidates_from_cache,
    internal_link_candidates_json,
    mascot_facts_summary,
    read_brand_config,
    sanitize_internal_links,
    strip_unapproved_internal_links,
    unwrap_lists_from_paragraphs,
)
from lib.crew.writer.models import ContentBrief, InternalLinkCandidate


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: object) -> None:
    _write(path, json.dumps(data))


# ── mascot_facts_summary ────────────────────────────────────────────────────


def test_mascot_facts_summary_missing_file_returns_empty(tmp_path: Path) -> None:
    assert mascot_facts_summary(tmp_path) == ""


def test_mascot_facts_summary_returns_full_short_text(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "config" / "nalla_facts.md", "Nalla is a fluffy shepherd mix.")
    assert mascot_facts_summary(tmp_path) == "Nalla is a fluffy shepherd mix."


def test_mascot_facts_summary_truncates_long_text(tmp_path: Path) -> None:
    long_text = "line one\n" * 500
    _write(tmp_path / "data" / "config" / "nalla_facts.md", long_text)
    summary = mascot_facts_summary(tmp_path, max_chars=100)
    assert len(summary) <= 130
    assert summary.endswith("...(truncated)")


# ── internal_link_candidates_from_cache ─────────────────────────────────────


def test_internal_link_candidates_from_cache_extracts_real_posts() -> None:
    site_cache = {
        "recent_posts": [
            {"title": "Real Post A", "url": "https://dogfoodandfun.com/real-a/"},
            {"title": "Real Post B", "url": "https://dogfoodandfun.com/real-b/"},
        ]
    }
    candidates = internal_link_candidates_from_cache(site_cache)
    assert candidates == [
        InternalLinkCandidate(title="Real Post A", url="https://dogfoodandfun.com/real-a/"),
        InternalLinkCandidate(title="Real Post B", url="https://dogfoodandfun.com/real-b/"),
    ]


def test_internal_link_candidates_from_cache_skips_malformed_rows() -> None:
    site_cache = {
        "recent_posts": [
            {"title": "Has Both", "url": "https://x.com/a/"},
            {"title": "No URL"},
            {"url": "https://x.com/no-title/"},
            "not-a-dict",
            {},
        ]
    }
    candidates = internal_link_candidates_from_cache(site_cache)
    assert len(candidates) == 1
    assert candidates[0].title == "Has Both"


def test_internal_link_candidates_from_cache_empty_input() -> None:
    assert internal_link_candidates_from_cache({}) == []
    assert internal_link_candidates_from_cache({"recent_posts": []}) == []


def test_internal_link_candidates_json_round_trips() -> None:
    candidates = [InternalLinkCandidate(title="A", url="https://x.com/a/")]
    parsed = json.loads(internal_link_candidates_json(candidates))
    assert parsed == [{"title": "A", "url": "https://x.com/a/"}]


# ── sanitize_internal_links / filter_links_to_allowed ───────────────────────


def _brief(**overrides: object) -> ContentBrief:
    defaults: dict[str, object] = {
        "suggested_title": "Title 2026",
        "primary_keyword": "kw",
        "mascot_angle": "angle",
        "internal_link_candidates": [],
    }
    defaults.update(overrides)
    return ContentBrief(**defaults)  # type: ignore[arg-type]


def test_sanitize_internal_links_drops_invented_urls_keeps_real_ones() -> None:
    real = InternalLinkCandidate(title="Real Post", url="https://dogfoodandfun.com/real/")
    invented = InternalLinkCandidate(
        title="Invented Post", url="https://dogfoodandfun.com/invented/"
    )
    brief = _brief(internal_link_candidates=[real, invented])

    sanitized = sanitize_internal_links(brief, allowed_urls={real.url})

    assert sanitized.internal_link_candidates == [real]


def test_sanitize_internal_links_keeps_all_when_all_real() -> None:
    real1 = InternalLinkCandidate(title="A", url="https://x.com/a/")
    real2 = InternalLinkCandidate(title="B", url="https://x.com/b/")
    brief = _brief(internal_link_candidates=[real1, real2])

    sanitized = sanitize_internal_links(brief, allowed_urls={real1.url, real2.url})

    assert sanitized.internal_link_candidates == [real1, real2]


def test_sanitize_internal_links_drops_all_when_none_allowed() -> None:
    brief = _brief(
        internal_link_candidates=[InternalLinkCandidate(title="A", url="https://x.com/a/")]
    )
    sanitized = sanitize_internal_links(brief, allowed_urls=set())
    assert sanitized.internal_link_candidates == []


def test_filter_links_to_allowed_matches_sanitize_behavior() -> None:
    real = InternalLinkCandidate(title="Real", url="https://x.com/real/")
    fake = InternalLinkCandidate(title="Fake", url="https://x.com/fake/")
    kept = filter_links_to_allowed([real, fake], allowed_urls={real.url})
    assert kept == [real]


# ── unwrap_lists_from_paragraphs ─────────────────────────────────────────────


def test_unwrap_lists_from_paragraphs_splits_real_reproduced_shape() -> None:
    """The exact shape live-reproduced on post 4147."""
    body = '<p>Related reading:<br>\n<ul>\n<li><a href="https://x.com/a/">A</a></li>\n</ul></p>'
    out = unwrap_lists_from_paragraphs(body)
    assert out == (
        '<p>Related reading:<br></p><ul>\n<li><a href="https://x.com/a/">A</a></li>\n</ul>'
    )
    assert "<ul>" in out
    assert "</ul></p>" not in out


def test_unwrap_lists_from_paragraphs_keeps_trailing_text_as_own_paragraph() -> None:
    body = "<p>Before<ul><li>x</li></ul>After</p>"
    out = unwrap_lists_from_paragraphs(body)
    assert out == "<p>Before</p><ul><li>x</li></ul><p>After</p>"


def test_unwrap_lists_from_paragraphs_no_leading_text_no_empty_paragraph() -> None:
    body = "<p><ul><li>x</li></ul></p>"
    out = unwrap_lists_from_paragraphs(body)
    assert out == "<ul><li>x</li></ul>"
    assert "<p></p>" not in out


def test_unwrap_lists_from_paragraphs_handles_ordered_lists_too() -> None:
    body = "<p>Steps:<ol><li>one</li></ol></p>"
    out = unwrap_lists_from_paragraphs(body)
    assert out == "<p>Steps:</p><ol><li>one</li></ol>"


def test_unwrap_lists_from_paragraphs_leaves_correctly_nested_html_unchanged() -> None:
    body = "<p>Normal paragraph.</p><ul><li>Already a sibling.</li></ul><p>More text.</p>"
    assert unwrap_lists_from_paragraphs(body) == body


def test_unwrap_lists_from_paragraphs_no_lists_present_is_a_noop() -> None:
    body = "<p>Just prose, no lists anywhere.</p>"
    assert unwrap_lists_from_paragraphs(body) == body


# ── read_brand_config ─────────────────────────────────────────────────────────


def test_read_brand_config_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_brand_config(tmp_path) == {}


def test_read_brand_config_reads_real_file(tmp_path: Path) -> None:
    _write_json(tmp_path / "config.json", {"site": {"name": "DogFoodAndFun"}})
    assert read_brand_config(tmp_path) == {"site": {"name": "DogFoodAndFun"}}


def test_read_brand_config_malformed_json_returns_empty(tmp_path: Path) -> None:
    _write(tmp_path / "config.json", "{not valid")
    assert read_brand_config(tmp_path) == {}


# ── idea_priority ────────────────────────────────────────────────────────────


def test_idea_priority_reads_crewai_scout_priority_score() -> None:
    row = {"input": json.dumps({"source": "crewai_scout", "priority_score": 85.0})}
    assert idea_priority(row) == 85.0


def test_idea_priority_reads_gsc_scout_score() -> None:
    row = {"input": json.dumps({"source": "gsc_scout", "score": 42.07})}
    assert idea_priority(row) == 42.07


def test_idea_priority_missing_or_malformed_defaults_to_zero() -> None:
    assert idea_priority({"input": None}) == 0.0
    assert idea_priority({"input": ""}) == 0.0
    assert idea_priority({"input": "{not valid json"}) == 0.0
    assert idea_priority({"input": json.dumps({"other_key": 1})}) == 0.0
    assert idea_priority({}) == 0.0


# ── strip_unapproved_internal_links ──────────────────────────────────────────

_SITE_URL = "https://dogfoodandfun.com"
_REAL_POST_URL = "https://dogfoodandfun.com/real-post/"
_INVENTED_POST_URL = "https://dogfoodandfun.com/invented-post/"


def test_strip_unapproved_internal_links_keeps_approved_links() -> None:
    body = f'<p>See our <a href="{_REAL_POST_URL}">real post</a> for more.</p>'
    out = strip_unapproved_internal_links(body, site_url=_SITE_URL, allowed_urls={_REAL_POST_URL})
    assert out == body


def test_strip_unapproved_internal_links_defangs_invented_same_site_link() -> None:
    body = f'<p>Check the <a href="{_INVENTED_POST_URL}">full comparison</a> post.</p>'
    out = strip_unapproved_internal_links(body, site_url=_SITE_URL, allowed_urls={_REAL_POST_URL})
    assert "<a href" not in out
    assert "full comparison" in out


def test_strip_unapproved_internal_links_leaves_external_links_untouched() -> None:
    amazon_url = "https://www.amazon.com/dp/B0FH8HQS3V?tag=dogfoodandfun01-20"
    body = f'<p>Consider the <a href="{amazon_url}">Fi collar</a>.</p>'
    out = strip_unapproved_internal_links(body, site_url=_SITE_URL, allowed_urls=set())
    assert out == body


def test_strip_unapproved_internal_links_mixed_body() -> None:
    amazon_url = "https://www.amazon.com/dp/B0FH8HQS3V?tag=x-20"
    body = (
        f'<p><a href="{_REAL_POST_URL}">real</a> and '
        f'<a href="{_INVENTED_POST_URL}">invented</a> and '
        f'<a href="{amazon_url}">affiliate</a></p>'
    )
    out = strip_unapproved_internal_links(body, site_url=_SITE_URL, allowed_urls={_REAL_POST_URL})
    assert f'href="{_REAL_POST_URL}"' in out
    assert f'href="{_INVENTED_POST_URL}"' not in out
    assert f'href="{amazon_url}"' in out
