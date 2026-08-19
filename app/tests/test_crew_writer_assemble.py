"""Tests for lib.crew.writer.orchestrator -- final HTML assembly + full pipeline composition.

Split out of test_crew_writer_orchestrator.py purely for file-size
discipline (see that file's docstring). Same conventions: `execute_fn` is
always a plain injected callable, no real CrewAI/DeepSeek/Postgres calls.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from crewai import Agent, Task

from lib.affiliate_resolver import AffiliateResolverError
from lib.crew.writer.models import ContentBrief, FaqPair, InternalLinkCandidate, WrittenPost
from lib.crew.writer.orchestrator import assemble_final_html, strategist_and_writer

_DISCLOSURE = "Affiliate disclosure: Amazon affiliate links, purchases support this site."
_REAL_URL = "https://dogfoodandfun.com/real-post/"


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "config.json",
        {
            "site": {
                "name": "DogFoodAndFun",
                "url": "https://dogfoodandfun.com",
                "brand_persona": "Nalla's Dad",
                "mascot_name": "Nalla",
            }
        },
    )
    _write_json(
        tmp_path / "data" / "cache" / "site_content_cache.json",
        {"recent_posts": [{"title": "Real Post", "url": _REAL_URL}]},
    )
    return tmp_path


def _idea_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "idea-1",
        "topic": "GPS Trackers Without the Monthly Bill",
        "target_keyword": "dog gps tracker without subscription",
        "category": "GPS/Gear",
        "persona_context": "Reddit threads show owners frustrated by subscription trackers.",
        "status": "publish",
        "input": json.dumps({"source": "crewai_scout", "priority_score": 85.0}),
        "created_at": "2026-08-01T00:00:00Z",
    }
    defaults.update(overrides)
    return defaults


def _brief(**overrides: Any) -> ContentBrief:
    defaults: dict[str, Any] = {
        "suggested_title": "Best No-Subscription GPS Trackers 2026",
        "primary_keyword": "no subscription dog gps tracker",
        "mascot_angle": "Nalla wandered off during a trail run once.",
        "internal_link_candidates": [InternalLinkCandidate(title="Real Post", url=_REAL_URL)],
        "faq_questions": ["Do GPS trackers need a subscription?"],
    }
    defaults.update(overrides)
    return ContentBrief(**defaults)


def _written_post(**overrides: Any) -> WrittenPost:
    defaults: dict[str, Any] = {
        "title": "Best No-Subscription GPS Trackers 2026",
        "body_html": f"<p>{_DISCLOSURE}</p><p>Full post body here.</p>",
        "word_count": 2800,
        "faq_pairs": [FaqPair(question="Do GPS trackers need a subscription?", answer="Not all.")],
        "internal_links_used": [InternalLinkCandidate(title="Real Post", url=_REAL_URL)],
        "affiliate_keys_used": [],
    }
    defaults.update(overrides)
    return WrittenPost(**defaults)


# ── assemble_final_html ──────────────────────────────────────────────────────


def test_assemble_final_html_injects_jsonld_and_resolves_with_no_placeholders(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")
    final_html = assemble_final_html(brand_dir, _written_post())
    assert "BlogPosting" in final_html
    assert "FAQPage" in final_html
    assert final_html.count("<script") == 2
    assert _DISCLOSURE in final_html


def test_assemble_final_html_strips_invented_link_embedded_in_body_html(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real finding from this build's live validation
    run: the writer embedded an invented internal link directly in prose
    without declaring it in `internal_links_used` at all. `assemble_final_html`
    must defang it even though it never appears in any structured field."""
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")
    invented_url = "https://dogfoodandfun.com/dog-gps-tracker-comparison/"
    post = _written_post(
        body_html=(
            f'<p>{_DISCLOSURE}</p><p>See our <a href="{invented_url}">full comparison</a> post.</p>'
        )
    )
    final_html = assemble_final_html(brand_dir, post)
    assert invented_url not in final_html
    assert "full comparison" in final_html


def test_assemble_final_html_unwraps_list_nested_in_paragraph(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the reproduced collapsed-layout bug on post 4147: the
    writer emitted `<p>Related reading:<br><ul>...</ul></p>` -- a `<ul>`
    (block content) nested inside a `<p>` (invalid HTML5). WordPress's
    rendering of that invalid nesting produced a stray, unbalanced `</div>`
    that collapsed the theme's page grid. `assemble_final_html` must unwrap
    the list out of the paragraph before it ever reaches WordPress."""
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")
    post = _written_post(
        body_html=(
            f"<p>{_DISCLOSURE}</p>"
            '<p>Related reading:<br>\n<ul>\n<li><a href="'
            f'{_REAL_URL}">Real Post</a></li>\n</ul></p>'
            "<p>Full post body here.</p>"
        )
    )
    final_html = assemble_final_html(brand_dir, post)
    assert "<ul>" in final_html
    assert "<p>Related reading:<br></p><ul>" in final_html
    assert "<ul></p>" not in final_html
    assert "</ul></p>" not in final_html


def test_assemble_final_html_propagates_affiliate_resolver_error_on_missing_disclosure(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")
    post = _written_post(body_html="<p>No disclosure sentence here at all.</p>")
    with pytest.raises(AffiliateResolverError):
        assemble_final_html(brand_dir, post)


def test_assemble_final_html_propagates_affiliate_resolver_error_on_unknown_key(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")
    post = _written_post(
        body_html=f"<p>{_DISCLOSURE}</p><p>[AFFILIATE:not-a-real-product]</p>",
        affiliate_keys_used=["not-a-real-product"],
    )
    with pytest.raises(AffiliateResolverError):
        assemble_final_html(brand_dir, post, catalog={})


def test_assemble_final_html_resolves_a_discovered_key_from_the_default_pool(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drafting path's end-to-end contract, live-broken until 2026-08-14.

    `_run_full_pipeline` writes with `discover=True`, so the body can carry a
    placeholder for a product found live rather than one curated in
    `affiliate_products.json` -- and then calls `assemble_final_html` with NO
    catalog, i.e. the default `load_candidate_pool`. If that pool doesn't
    include the discovery cache, this raises and the whole draft is thrown
    away (it did, on five approved ideas in a row).
    """
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")
    _write_json(
        brand_dir / "data" / "cache" / "discovered_products.json",
        [
            {
                "key": "dog-pulling-harness-b0dzhpyhgs",
                "asin": "B0DZHPYHGS",
                "display": "Dog Pulling Harness",
                "category": None,
                "notes": None,
                "ts": datetime.now(UTC).isoformat(),
            }
        ],
    )
    post = _written_post(
        body_html=f"<p>{_DISCLOSURE}</p><p>[AFFILIATE:dog-pulling-harness-b0dzhpyhgs]</p>",
        affiliate_keys_used=["dog-pulling-harness-b0dzhpyhgs"],
    )
    final_html = assemble_final_html(brand_dir, post)
    assert "[AFFILIATE:" not in final_html
    assert "https://www.amazon.com/dp/B0DZHPYHGS" in final_html
    assert "tag=test-tag-20" in final_html


def test_assemble_final_html_propagates_affiliate_resolver_error_on_missing_tag(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AMAZON_ASSOCIATES_TAG", raising=False)
    with pytest.raises(AffiliateResolverError):
        assemble_final_html(brand_dir, _written_post())


# ── strategist_and_writer (full composition) ─────────────────────────────────


def test_strategist_and_writer_happy_path(brand_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")

    def fake_strategist(agent: Agent, task: Task) -> ContentBrief:
        return _brief()

    def fake_writer(agent: Agent, task: Task) -> WrittenPost:
        return _written_post()

    result = strategist_and_writer(
        brand_dir,
        list_ideas_fn=lambda **_: [_idea_row()],
        strategist_execute_fn=fake_strategist,
        writer_execute_fn=fake_writer,
    )

    assert result is not None
    assert result.idea_id == "idea-1"
    assert result.word_count == 2800
    assert "BlogPosting" in result.final_html
    assert result.affiliate_keys_used == []


def test_strategist_and_writer_returns_none_when_no_idea(brand_dir: Path) -> None:
    result = strategist_and_writer(brand_dir, list_ideas_fn=lambda **_: [])
    assert result is None


def test_strategist_and_writer_returns_none_and_skips_writer_when_strategist_fails(
    brand_dir: Path,
) -> None:
    writer_called = False
    status_calls: list[tuple[str, str]] = []

    def fake_strategist(agent: Agent, task: Task) -> None:
        return None

    def fake_writer(agent: Agent, task: Task) -> WrittenPost:
        nonlocal writer_called
        writer_called = True
        return _written_post()

    def fake_update_status(idea_id: str, status: str) -> bool:
        status_calls.append((idea_id, status))
        return True

    result = strategist_and_writer(
        brand_dir,
        list_ideas_fn=lambda **_: [_idea_row()],
        update_status_fn=fake_update_status,
        strategist_execute_fn=fake_strategist,
        writer_execute_fn=fake_writer,
    )

    assert result is None
    assert writer_called is False
    assert status_calls == [("idea-1", "write_failed")]


def test_strategist_and_writer_returns_none_when_writer_fails(brand_dir: Path) -> None:
    status_calls: list[tuple[str, str]] = []

    def fake_strategist(agent: Agent, task: Task) -> ContentBrief:
        return _brief()

    def fake_writer(agent: Agent, task: Task) -> None:
        return None

    def fake_update_status(idea_id: str, status: str) -> bool:
        status_calls.append((idea_id, status))
        return True

    result = strategist_and_writer(
        brand_dir,
        list_ideas_fn=lambda **_: [_idea_row()],
        update_status_fn=fake_update_status,
        strategist_execute_fn=fake_strategist,
        writer_execute_fn=fake_writer,
    )
    assert result is None
    assert status_calls == [("idea-1", "write_failed")]


def test_strategist_and_writer_propagates_affiliate_resolver_error(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "test-tag-20")

    def fake_strategist(agent: Agent, task: Task) -> ContentBrief:
        return _brief()

    def fake_writer(agent: Agent, task: Task) -> WrittenPost:
        return _written_post(body_html="<p>No disclosure anywhere in this body.</p>")

    with pytest.raises(AffiliateResolverError):
        strategist_and_writer(
            brand_dir,
            list_ideas_fn=lambda **_: [_idea_row()],
            strategist_execute_fn=fake_strategist,
            writer_execute_fn=fake_writer,
        )
