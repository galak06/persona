"""Tests for lib.crew.writer.orchestrator -- idea selection + brief/draft stages.

`assemble_final_html`/`strategist_and_writer` (final HTML assembly + full
composition) live in test_crew_writer_assemble.py -- split purely for
file-size discipline. The CrewAI `kickoff()` call is never exercised here:
`execute_fn` is always injected as a plain callable returning a canned model
(or `None`, to simulate a failed LLM call) -- same convention as
test_crew_scout.py. No real Postgres, no real CrewAI/DeepSeek network calls,
no DATABASE_URL.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from crewai import Agent, Task

from lib.crew.writer.models import ContentBrief, FaqPair, InternalLinkCandidate, WrittenPost
from lib.crew.writer.orchestrator import build_content_brief, select_idea, write_post_from_brief

_DISCLOSURE = "Affiliate disclosure: Amazon affiliate links, purchases support this site."
_REAL_URL = "https://dogfoodandfun.com/real-post/"
_INVENTED_URL = "https://dogfoodandfun.com/made-up-post/"


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
        "nalla_context": "Reddit threads show owners frustrated by subscription trackers.",
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


# ── select_idea ──────────────────────────────────────────────────────────────


def test_select_idea_by_id_finds_row_in_any_status() -> None:
    rows = [_idea_row(id="a", status="wp_published"), _idea_row(id="b", status="publish")]

    def fake_list_ideas(*, brand_id: str | None = None, **_: Any) -> list[dict[str, Any]]:
        return rows

    result = select_idea(Path("/tmp/brand"), idea_id="a", list_ideas_fn=fake_list_ideas)
    assert result is not None
    assert result["id"] == "a"


def test_select_idea_by_id_not_found_returns_none() -> None:
    def fake_list_ideas(**_: Any) -> list[dict[str, Any]]:
        return [_idea_row(id="a")]

    assert select_idea(Path("/tmp/brand"), idea_id="missing", list_ideas_fn=fake_list_ideas) is None


def test_select_idea_picks_highest_priority_score(brand_dir: Path) -> None:
    low = _idea_row(id="low", input=json.dumps({"priority_score": 40.0}))
    high = _idea_row(id="high", input=json.dumps({"priority_score": 92.0}))
    mid = _idea_row(id="mid", input=json.dumps({"score": 60.0}))  # gsc_scout shape

    def fake_list_ideas(*, status: str | None = None, **_: Any) -> list[dict[str, Any]]:
        assert status == "publish"
        return [low, high, mid]

    result = select_idea(brand_dir, list_ideas_fn=fake_list_ideas)
    assert result is not None
    assert result["id"] == "high"


def test_select_idea_no_rows_returns_none(brand_dir: Path) -> None:
    assert select_idea(brand_dir, list_ideas_fn=lambda **_: []) is None


def test_select_idea_missing_priority_defaults_to_zero_and_uses_oldest_tiebreak(
    brand_dir: Path,
) -> None:
    older = _idea_row(id="older", input="{}", created_at="2026-01-01T00:00:00Z")
    newer = _idea_row(id="newer", input="{}", created_at="2026-06-01T00:00:00Z")

    result = select_idea(brand_dir, list_ideas_fn=lambda **_: [newer, older])
    assert result is not None
    assert result["id"] == "older"


# ── build_content_brief ──────────────────────────────────────────────────────


def test_build_content_brief_sanitizes_invented_links(brand_dir: Path) -> None:
    def fake_execute(agent: Agent, task: Task) -> ContentBrief:
        return _brief(
            internal_link_candidates=[
                InternalLinkCandidate(title="Real Post", url=_REAL_URL),
                InternalLinkCandidate(title="Invented Post", url=_INVENTED_URL),
            ]
        )

    brief = build_content_brief(brand_dir, _idea_row(), execute_fn=fake_execute)
    assert brief is not None
    assert [c.url for c in brief.internal_link_candidates] == [_REAL_URL]


def test_build_content_brief_returns_none_when_execute_fn_returns_none(brand_dir: Path) -> None:
    def fake_execute(agent: Agent, task: Task) -> None:
        return None

    assert build_content_brief(brand_dir, _idea_row(), execute_fn=fake_execute) is None


# ── write_post_from_brief ────────────────────────────────────────────────────


def test_write_post_from_brief_filters_invented_links_used(brand_dir: Path) -> None:
    def fake_execute(agent: Agent, task: Task) -> WrittenPost:
        return _written_post(
            internal_links_used=[
                InternalLinkCandidate(title="Real Post", url=_REAL_URL),
                InternalLinkCandidate(title="Invented Post", url=_INVENTED_URL),
            ]
        )

    post = write_post_from_brief(brand_dir, _brief(), execute_fn=fake_execute)
    assert post is not None
    assert [link.url for link in post.internal_links_used] == [_REAL_URL]


def test_write_post_from_brief_returns_none_when_execute_fn_returns_none(brand_dir: Path) -> None:
    def fake_execute(agent: Agent, task: Task) -> None:
        return None

    assert write_post_from_brief(brand_dir, _brief(), execute_fn=fake_execute) is None
