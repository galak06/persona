"""Site content cache builder — `lib.site_cache`.

The cache decides what a post is allowed to link to, so the cases that matter
most are the ones that could silently shrink it: a term ID that resolves to
nothing, a malformed post, pagination stopping a page early, and the cap.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lib.site_cache import (
    build_cache,
    cache_path,
    fetch_recent_posts,
    write_cache,
)


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Minimal stand-in for `httpx.Client` over the three WP endpoints used."""

    def __init__(
        self,
        posts: list[dict[str, Any]],
        categories: list[dict[str, Any]] | None = None,
        tags: list[dict[str, Any]] | None = None,
        *,
        posts_status: int = 200,
    ) -> None:
        self._posts = posts
        self._categories = categories or []
        self._tags = tags or []
        self._posts_status = posts_status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        params = params or {}
        self.calls.append((url, params))
        if url.endswith("/categories"):
            return _FakeResponse(self._categories if params.get("page", 1) == 1 else [])
        if url.endswith("/tags"):
            return _FakeResponse(self._tags if params.get("page", 1) == 1 else [])
        if self._posts_status != 200:
            return _FakeResponse([], status_code=self._posts_status)
        page = int(params.get("page", 1))
        per_page = int(params.get("per_page", 100))
        start = (page - 1) * per_page
        return _FakeResponse(self._posts[start : start + per_page])


def _post(pid: int, title: str, *, categories: list[int] | None = None) -> dict[str, Any]:
    return {
        "id": pid,
        "link": f"https://x.example/{pid}/",
        "title": {"rendered": title},
        "excerpt": {"rendered": f"<p>About {title}&#8217;s topic.</p>"},
        "date_gmt": "2026-08-01T00:00:00",
        "categories": categories if categories is not None else [7],
        "tags": [3],
    }


_CATS = [{"id": 7, "name": "Dog Food"}, {"id": 9, "name": "Gear"}]
_TAGS = [{"id": 3, "name": "homemade"}]


def test_term_ids_are_resolved_to_names() -> None:
    """Posts reference terms by ID, but every reader -- and focus matching --
    works in names."""
    client = _FakeClient([_post(1, "Raw vs Kibble")], _CATS, _TAGS)
    (entry,) = fetch_recent_posts(client, max_posts=10)  # type: ignore[arg-type]
    assert entry["categories"] == ["Dog Food"]
    assert entry["tags"] == ["homemade"]


def test_excerpt_and_title_are_plain_text() -> None:
    client = _FakeClient([_post(1, "Raw &amp; Kibble")], _CATS, _TAGS)
    (entry,) = fetch_recent_posts(client, max_posts=10)  # type: ignore[arg-type]
    assert entry["title"] == "Raw & Kibble"
    assert "<p>" not in entry["excerpt"]
    assert "’" in entry["excerpt"]  # &#8217; decoded, not left as an entity


def test_unknown_term_ids_are_dropped_not_rendered_as_numbers() -> None:
    """An ID with no matching term must vanish rather than become a category
    name no focus setting could ever match."""
    client = _FakeClient([_post(1, "T", categories=[7, 999])], _CATS, _TAGS)
    (entry,) = fetch_recent_posts(client, max_posts=10)  # type: ignore[arg-type]
    assert entry["categories"] == ["Dog Food"]


def test_posts_without_title_or_url_are_skipped_not_fatal() -> None:
    bad = _post(2, "")
    client = _FakeClient([_post(1, "Good"), bad], _CATS, _TAGS)
    entries = fetch_recent_posts(client, max_posts=10)  # type: ignore[arg-type]
    assert [e["title"] for e in entries] == ["Good"]


def test_cap_is_respected_across_pages() -> None:
    client = _FakeClient([_post(i, f"P{i}") for i in range(1, 30)], _CATS, _TAGS)
    entries = fetch_recent_posts(client, max_posts=5)  # type: ignore[arg-type]
    assert len(entries) == 5


def test_zero_cap_fetches_nothing() -> None:
    client = _FakeClient([_post(1, "T")], _CATS, _TAGS)
    assert fetch_recent_posts(client, max_posts=0) == []  # type: ignore[arg-type]
    assert client.calls == []


def test_failed_posts_request_returns_empty_rather_than_partial_garbage() -> None:
    client = _FakeClient([_post(1, "T")], _CATS, _TAGS, posts_status=500)
    assert fetch_recent_posts(client, max_posts=10) == []  # type: ignore[arg-type]


def test_recency_order_from_wp_is_preserved() -> None:
    """`rank_link_candidates` sorts stably, so this order survives as the
    tiebreak inside each category group."""
    client = _FakeClient([_post(i, f"P{i}") for i in (1, 2, 3)], _CATS, _TAGS)
    entries = fetch_recent_posts(client, max_posts=10)  # type: ignore[arg-type]
    assert [e["title"] for e in entries] == ["P1", "P2", "P3"]


# ───────────────────────────────────────────────── document assembly


def test_existing_content_summary_is_preserved() -> None:
    """`lib.keyword_research` reads `content_summary.site.content_gaps`, and a
    crawl cannot regenerate it -- dropping it would break that reader."""
    existing = {"content_summary": {"site": {"content_gaps": ["puppy food"]}}}
    cache = build_cache(existing, [], site_url="https://x", site_name="X")
    assert cache["content_summary"] == existing["content_summary"]


def test_absent_or_empty_content_summary_is_not_invented() -> None:
    for existing in ({}, {"content_summary": {}}, {"content_summary": "junk"}):
        assert "content_summary" not in build_cache(
            existing, [], site_url="https://x", site_name="X"
        )


def test_cache_document_carries_identity_and_timestamp() -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    cache = build_cache({}, [{"title": "T"}], site_url="https://x", site_name="X", now=now)
    assert cache["cached_at"] == now.isoformat()
    assert cache["site_url"] == "https://x"
    assert cache["recent_posts"] == [{"title": "T"}]


def test_write_creates_the_cache_dir_for_a_fresh_brand(tmp_path: Path) -> None:
    written = write_cache(tmp_path, {"recent_posts": []})
    assert written == cache_path(tmp_path)
    assert json.loads(written.read_text(encoding="utf-8")) == {"recent_posts": []}


@pytest.mark.parametrize("missing", ["title", "link"])
def test_entry_requires_both_title_and_url(missing: str) -> None:
    post = _post(1, "T")
    post[missing] = "" if missing == "link" else {"rendered": ""}
    client = _FakeClient([post], _CATS, _TAGS)
    assert fetch_recent_posts(client, max_posts=10) == []  # type: ignore[arg-type]


def test_html_escaped_term_names_are_decoded() -> None:
    """WP returns "Food &amp; Diet". Left escaped, a focus category typed as
    "Food & Diet" would never match it and ranking would silently no-op."""
    client = _FakeClient(
        [_post(1, "T", categories=[11])],
        [{"id": 11, "name": "Food &amp; Diet"}],
        _TAGS,
    )
    (entry,) = fetch_recent_posts(client, max_posts=10)  # type: ignore[arg-type]
    assert entry["categories"] == ["Food & Diet"]
