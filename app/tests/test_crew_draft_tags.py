"""Tests for lib.crew.draft_tags.resolve_tag_ids.

Uses respx to stub the WP REST tags endpoint (same convention as
test_crew_draft.py) -- no real network call happens.
"""
# ruff: noqa: S101

from __future__ import annotations

import httpx
import pytest
import respx

from lib.crew.draft_tags import resolve_tag_ids

_TAGS_URL = "https://example.com/wp-json/wp/v2/tags"


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url="https://example.com")


@respx.mock
def test_resolve_tag_ids_reuses_existing_tag(client: httpx.Client) -> None:
    respx.get(_TAGS_URL, params={"slug": "dog-gps-tracker"}).mock(
        return_value=httpx.Response(200, json=[{"id": 7, "slug": "dog-gps-tracker"}])
    )
    create_route = respx.post(_TAGS_URL)

    ids = resolve_tag_ids(client, ["Dog GPS Tracker"], "idea-1")

    assert ids == [7]
    assert not create_route.called


@respx.mock
def test_resolve_tag_ids_creates_missing_tag(client: httpx.Client) -> None:
    respx.get(_TAGS_URL, params={"slug": "canicross"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    create_route = respx.post(_TAGS_URL).mock(
        return_value=httpx.Response(201, json={"id": 42, "slug": "canicross"})
    )

    ids = resolve_tag_ids(client, ["Canicross"], "idea-1")

    assert ids == [42]
    assert create_route.called
    sent = create_route.calls.last.request
    assert b'"name": "Canicross"' in sent.content or b'"name":"Canicross"' in sent.content


@respx.mock
def test_resolve_tag_ids_skips_blank_names(client: httpx.Client) -> None:
    ids = resolve_tag_ids(client, ["", "   "], "idea-1")
    assert ids == []


@respx.mock
def test_resolve_tag_ids_dedupes_by_normalized_slug(client: httpx.Client) -> None:
    route = respx.get(_TAGS_URL, params={"slug": "gps-tracker"}).mock(
        return_value=httpx.Response(200, json=[{"id": 9, "slug": "gps-tracker"}])
    )

    ids = resolve_tag_ids(client, ["GPS Tracker", "gps tracker", " GPS   Tracker"], "idea-1")

    assert ids == [9]
    assert route.call_count == 1


@respx.mock
def test_resolve_tag_ids_is_best_effort_on_create_failure(client: httpx.Client) -> None:
    respx.get(_TAGS_URL, params={"slug": "broken-tag"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(_TAGS_URL).mock(return_value=httpx.Response(500, text="server error"))

    ids = resolve_tag_ids(client, ["Broken Tag"], "idea-1")

    assert ids == []  # logged, not raised


@respx.mock
def test_resolve_tag_ids_continues_past_one_failure(client: httpx.Client) -> None:
    respx.get(_TAGS_URL, params={"slug": "broken-tag"}).mock(return_value=httpx.Response(500))
    respx.get(_TAGS_URL, params={"slug": "good-tag"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(_TAGS_URL).mock(return_value=httpx.Response(201, json={"id": 3, "slug": "good-tag"}))

    ids = resolve_tag_ids(client, ["Broken Tag", "Good Tag"], "idea-1")

    assert ids == [3]
