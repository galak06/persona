"""Tests for lib.crew.draft -- the WordPress draft-creation step.

Uses respx to stub the WP REST endpoint (same tool/convention as
`tests/lib/sessions/test_wp_client.py`) so no real network call happens.
`set_wp_result_fn`/`update_status_fn` are always plain injected callables
(same convention as `lib.ideas_db`'s other callers in this pipeline) -- no
real Postgres, no DATABASE_URL.

Hero-image + `dff-idea-body` wrapping coverage lives in
`test_crew_draft_image.py` (split out purely for file-size discipline, same
convention as `test_crew_writer_assemble.py` being split out of
`test_crew_writer_orchestrator.py`).
"""
# ruff: noqa: S101

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lib.crew.draft import DraftCreationError, create_wp_draft

_POSTS_URL = "https://example.com/wp-json/wp/v2/posts"
_CATEGORIES_URL = "https://example.com/wp-json/wp/v2/categories"


@pytest.fixture(autouse=True)
def _wp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WP_URL", "https://example.com")
    monkeypatch.setenv("WP_USER", "test_user")
    monkeypatch.setenv("WP_APP_PASSWORD", "test_pass")


class _FakeIdeasDb:
    def __init__(self) -> None:
        self.set_wp_result_calls: list[tuple[str, str, str]] = []
        self.update_status_calls: list[tuple[str, str]] = []
        self.set_wp_result_return = True
        self.update_status_return = True

    def set_wp_result(self, idea_id: str, wp_post_id: str, wp_url: str) -> bool:
        self.set_wp_result_calls.append((idea_id, wp_post_id, wp_url))
        return self.set_wp_result_return

    def update_status(self, idea_id: str, status: str) -> bool:
        self.update_status_calls.append((idea_id, status))
        return self.update_status_return


@respx.mock
def test_create_wp_draft_success_records_result_on_idea_row() -> None:
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(
            201, json={"id": 4242, "link": "https://example.com/?p=4242&preview=true"}
        )
    )
    fake_db = _FakeIdeasDb()

    result = create_wp_draft(
        idea_id="idea-1",
        title="A Great Post",
        body_html="<p>Full post body.</p>",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
    )

    assert route.called
    assert result.idea_id == "idea-1"
    assert result.post_id == 4242
    assert result.post_url == "https://example.com/?p=4242&preview=true"
    assert fake_db.set_wp_result_calls == [
        ("idea-1", "4242", "https://example.com/?p=4242&preview=true")
    ]
    assert fake_db.update_status_calls == [("idea-1", "wp_draft")]


@respx.mock
def test_create_wp_draft_always_posts_status_draft_hardcoded() -> None:
    """Hard constraint: this pipeline must never POST any status other than
    'draft' -- no parameter on `create_wp_draft` can change it."""
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
    )

    sent_payload = route.calls.last.request.content
    body = json.loads(sent_payload)
    assert body["status"] == "draft"
    assert body["title"] == "t"


@respx.mock
def test_create_wp_draft_raises_on_wp_error_and_never_touches_ideas_db() -> None:
    respx.post(_POSTS_URL).mock(return_value=httpx.Response(500, text="server error"))
    fake_db = _FakeIdeasDb()

    with pytest.raises(DraftCreationError):
        create_wp_draft(
            idea_id="idea-1",
            title="t",
            body_html="<p>b</p>",
            set_wp_result_fn=fake_db.set_wp_result,
            update_status_fn=fake_db.update_status,
        )

    assert fake_db.set_wp_result_calls == []
    assert fake_db.update_status_calls == []


@respx.mock
def test_create_wp_draft_succeeds_even_if_ideas_db_update_fails() -> None:
    """The WP draft is the source of truth -- a DB-write hiccup after a
    successful POST is logged, not raised (the draft already exists)."""
    respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 7, "link": "https://example.com/?p=7"})
    )
    fake_db = _FakeIdeasDb()
    fake_db.set_wp_result_return = False

    result = create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
    )

    assert result.post_id == 7
    assert fake_db.set_wp_result_calls  # was still attempted


# ── category_name resolution ─────────────────────────────────────────────────
# Full coverage (exact match + content-based categorizer fallback) lives in
# test_crew_draft_category.py, split out for file-size discipline and
# because the categorizer fallback needs its own fake-injection convention
# (categorize_fn) that doesn't belong mixed into this file's core tests.


# ── tag_names resolution ─────────────────────────────────────────────────────
# Full coverage of resolve_tag_ids itself (create/reuse/dedupe/best-effort)
# lives in test_crew_draft_tags.py. These tests only cover the wiring: that
# create_wp_draft calls resolve_tags_fn and includes the result in the POST
# payload.


@respx.mock
def test_create_wp_draft_includes_resolved_tag_ids_in_payload() -> None:
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    def fake_resolve_tags(client: httpx.Client, tag_names: list[str], idea_id: str) -> list[int]:
        assert tag_names == ["gps tracker", "no subscription"]
        assert idea_id == "idea-1"
        return [11, 22]

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        tag_names=["gps tracker", "no subscription"],
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        resolve_tags_fn=fake_resolve_tags,
    )

    body = json.loads(route.calls.last.request.content)
    assert body["tags"] == [11, 22]


@respx.mock
def test_create_wp_draft_omits_tags_key_when_no_tags_resolved() -> None:
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        resolve_tags_fn=lambda client, tag_names, idea_id: [],
    )

    body = json.loads(route.calls.last.request.content)
    assert "tags" not in body
