"""Tests for lib.crew.draft_category -- WP category resolution.

Split out of test_crew_draft.py for file-size discipline and because this
module's fallback path needs its own fake-injection convention
(`categorize_fn`) that doesn't belong mixed into that file's core tests.
Same respx/injected-fake conventions -- no real WP/DeepSeek network calls.
"""
# ruff: noqa: S101

from __future__ import annotations

import json

import httpx
import pytest
import respx
from crewai import Agent, Task

from lib.crew.categorizer.models import CategoryChoice
from lib.crew.draft import create_wp_draft

_POSTS_URL = "https://example.com/wp-json/wp/v2/posts"
_CATEGORIES_URL = "https://example.com/wp-json/wp/v2/categories"

_REAL_CATEGORIES = [
    {"id": 36, "name": "Food &amp; Diet"},
    {"id": 38, "name": "Lifestyle &amp; Gear"},
    {"id": 41, "name": "Recipes"},
]


@pytest.fixture(autouse=True)
def _wp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WP_URL", "https://example.com")
    monkeypatch.setenv("WP_USER", "test_user")
    monkeypatch.setenv("WP_APP_PASSWORD", "test_pass")


class _FakeIdeasDb:
    def set_wp_result(self, idea_id: str, wp_post_id: str, wp_url: str) -> bool:
        return True

    def update_status(self, idea_id: str, status: str) -> bool:
        return True


def _never_called_categorizer(agent: Agent, task: Task) -> CategoryChoice:
    """Fails the test loudly if the categorizer fallback fires when it
    shouldn't (e.g. an exact match should short-circuit before reaching it)."""
    raise AssertionError("categorize_fn should not have been called")


# ── exact-match pass (free, no LLM call) ─────────────────────────────────────


@respx.mock
def test_resolves_exact_matching_category() -> None:
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(200, json=_REAL_CATEGORIES))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        category_name="Recipes",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=_never_called_categorizer,
    )

    body = json.loads(route.calls.last.request.content)
    assert body["categories"] == [41]


@respx.mock
def test_exact_match_is_case_insensitive_and_unescapes_entities() -> None:
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(200, json=_REAL_CATEGORIES))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        category_name="food & diet",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=_never_called_categorizer,
    )

    body = json.loads(route.calls.last.request.content)
    assert body["categories"] == [36]


@respx.mock
def test_category_lookup_failure_soft_skips_without_reaching_categorizer() -> None:
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(500, text="server error"))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    result = create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        category_name="Recipes",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=_never_called_categorizer,
    )

    assert result.post_id == 1
    body = json.loads(route.calls.last.request.content)
    assert "categories" not in body


@respx.mock
def test_no_category_name_skips_lookup_entirely_even_with_a_real_title() -> None:
    """`category_name` (not `title`) gates the whole resolution -- a draft
    with no idea-category at all (e.g. `--idea-id` targeting a row that
    never had one set) never pays for a categories fetch it has no
    category-name signal to match against, regardless of title."""
    categories_route = respx.get(_CATEGORIES_URL).mock(
        return_value=httpx.Response(200, json=_REAL_CATEGORIES)
    )
    respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    create_wp_draft(
        idea_id="idea-1",
        title="A real, non-empty title",
        body_html="<p>b</p>",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=_never_called_categorizer,
    )

    assert not categories_route.called


# ── content-based categorizer fallback ───────────────────────────────────────


@respx.mock
def test_no_exact_match_falls_back_to_content_based_categorizer() -> None:
    """'Nutrition' has no exact match against the real categories -- shares
    zero literal words with 'Food & Diet' -- but the categorizer, reading
    the post's actual content, can still resolve it correctly."""
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(200, json=_REAL_CATEGORIES))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    def fake_categorize(agent: Agent, task: Task) -> CategoryChoice:
        return CategoryChoice(category_name="Food & Diet")

    create_wp_draft(
        idea_id="idea-1",
        title="Calcium Balance in Homemade Dog Food",
        body_html="<p>b</p>",
        category_name="Nutrition",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=fake_categorize,
    )

    body = json.loads(route.calls.last.request.content)
    assert body["categories"] == [36]


@respx.mock
def test_category_name_given_but_no_title_never_reaches_categorizer() -> None:
    """The categorizer needs actual content to judge -- a bare category
    label with no post title to go on can't be resolved by it either."""
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(200, json=_REAL_CATEGORIES))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    create_wp_draft(
        idea_id="idea-1",
        title="",
        body_html="<p>b</p>",
        category_name="Nutrition",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=_never_called_categorizer,
    )

    body = json.loads(route.calls.last.request.content)
    assert "categories" not in body


@respx.mock
def test_categorizer_says_no_good_fit_leaves_post_uncategorized() -> None:
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(200, json=_REAL_CATEGORIES))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    def fake_categorize(agent: Agent, task: Task) -> CategoryChoice:
        return CategoryChoice(category_name="")

    create_wp_draft(
        idea_id="idea-1",
        title="Running",
        body_html="<p>b</p>",
        category_name="Running",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=fake_categorize,
    )

    body = json.loads(route.calls.last.request.content)
    assert "categories" not in body


@respx.mock
def test_categorizer_hallucinated_category_is_rejected_not_trusted() -> None:
    """Defensive validation: the categorizer's pick must be one of the REAL
    category names -- an invented one (never in the provided list) is
    rejected, same posture as internal-link/affiliate-key validation
    elsewhere in this pipeline. Never trust the model blindly."""
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(200, json=_REAL_CATEGORIES))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    def fake_categorize(agent: Agent, task: Task) -> CategoryChoice:
        return CategoryChoice(category_name="Dog Training Tips")  # not a real category

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        category_name="Training",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=fake_categorize,
    )

    body = json.loads(route.calls.last.request.content)
    assert "categories" not in body


@respx.mock
def test_categorizer_kickoff_failure_leaves_post_uncategorized() -> None:
    respx.get(_CATEGORIES_URL).mock(return_value=httpx.Response(200, json=_REAL_CATEGORIES))
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    def failing_categorize(agent: Agent, task: Task) -> None:
        return None

    result = create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        category_name="Running",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        categorize_fn=failing_categorize,
    )

    assert result.post_id == 1
    body = json.loads(route.calls.last.request.content)
    assert "categories" not in body
