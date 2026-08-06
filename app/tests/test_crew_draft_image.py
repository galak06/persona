"""Tests for lib.crew.draft's hero-image + dff-idea-body wrapping behavior.

Split out of test_crew_draft.py purely for file-size discipline (same
convention as test_crew_writer_assemble.py being split out of
test_crew_writer_orchestrator.py). Same conventions: respx stubs the WP REST
endpoint, `generate_image_fn`/`upload_media_fn` are plain injected fakes --
no real Imagen/Gemini/WordPress network calls.
"""
# ruff: noqa: S101

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lib.crew.draft import create_wp_draft
from lib.crew.wp_image import GeneratedImage

_POSTS_URL = "https://example.com/wp-json/wp/v2/posts"


@pytest.fixture(autouse=True)
def _wp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WP_URL", "https://example.com")
    monkeypatch.setenv("WP_USER", "test_user")
    monkeypatch.setenv("WP_APP_PASSWORD", "test_pass")


class _FakeIdeasDb:
    def __init__(self) -> None:
        self.set_wp_result_calls: list[tuple[str, str, str]] = []
        self.update_status_calls: list[tuple[str, str]] = []

    def set_wp_result(self, idea_id: str, wp_post_id: str, wp_url: str) -> bool:
        self.set_wp_result_calls.append((idea_id, wp_post_id, wp_url))
        return True

    def update_status(self, idea_id: str, status: str) -> bool:
        self.update_status_calls.append((idea_id, status))
        return True


def _fake_generate_image(**_: object) -> GeneratedImage:
    return GeneratedImage(
        url="imagen://fake",
        alt_text="A great post",
        provider="imagen_fast",
        bytes_=b"fake-png-bytes",
        content_type="image/png",
    )


def _fake_upload_media(client: httpx.Client, image: GeneratedImage, slug: str) -> tuple[int, str]:
    return 555, f"https://example.com/wp-content/uploads/{slug}.png"


@respx.mock
def test_create_wp_draft_wraps_body_in_dff_idea_body_with_no_hero_when_no_brief() -> None:
    """No `image_brief` supplied (the default) -- image step is skipped
    entirely, but the body is still wrapped in the theme's expected
    container, just with no hero `<figure>`."""
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

    body = json.loads(route.calls.last.request.content)
    assert body["content"] == (
        '<div class="dff-idea-body" style="max-width:720px;margin:0 auto;"><p>b</p></div>'
    )
    assert "figure" not in body["content"]
    assert "featured_media" not in body
    assert "meta" not in body


@respx.mock
def test_create_wp_draft_wraps_body_with_hero_figure_and_full_meta_on_image_success() -> None:
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 2, "link": "https://example.com/?p=2"})
    )
    fake_db = _FakeIdeasDb()

    result = create_wp_draft(
        idea_id="idea-1",
        title="A Great Post",
        body_html="<p>Full post body.</p>",
        image_brief="Nalla running through a field, a GPS tracker on her collar.",
        mascot_name="Nalla",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        generate_image_fn=_fake_generate_image,
        upload_media_fn=_fake_upload_media,
    )

    assert result.post_id == 2
    body = json.loads(route.calls.last.request.content)
    expected_hero = (
        '<figure><img src="https://example.com/wp-content/uploads/a-great-post.png" '
        'alt="A great post" class="wp-post-image"></figure>'
    )
    assert body["content"] == (
        '<div class="dff-idea-body" style="max-width:720px;margin:0 auto;">'
        f"{expected_hero}<p>Full post body.</p></div>"
    )
    assert body["featured_media"] == 555
    assert body["meta"] == {
        "fifu_image_url": "https://example.com/wp-content/uploads/a-great-post.png",
        "fifu_image_alt": "A great post",
        "_elementor_edit_mode": "",
    }


@respx.mock
def test_create_wp_draft_survives_image_generation_failure_best_effort() -> None:
    """A blown-up image-generation call must not prevent the WP draft from
    being created -- best-effort, logged, draft still gets made without an
    image."""
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 3, "link": "https://example.com/?p=3"})
    )
    fake_db = _FakeIdeasDb()

    def _raising_generate_image(**_: object) -> GeneratedImage:
        raise RuntimeError("imagen is on fire")

    result = create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        image_brief="some brief",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        generate_image_fn=_raising_generate_image,
        upload_media_fn=_fake_upload_media,
    )

    assert result.post_id == 3
    body = json.loads(route.calls.last.request.content)
    assert "figure" not in body["content"]
    assert "featured_media" not in body
    assert fake_db.set_wp_result_calls  # draft creation still recorded


@respx.mock
def test_create_wp_draft_survives_placeholder_image_best_effort() -> None:
    """`generate_wp_image` itself never raises on total provider failure --
    it returns a placeholder (`bytes_=b""`). That must also be treated as
    "no image" rather than attempting an upload of empty bytes."""
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 4, "link": "https://example.com/?p=4"})
    )
    fake_db = _FakeIdeasDb()
    upload_called = False

    def _placeholder_generate_image(**_: object) -> GeneratedImage:
        return GeneratedImage(url="placeholder", alt_text="t", provider="none", bytes_=b"")

    def _upload_should_not_be_called(
        client: httpx.Client, image: GeneratedImage, slug: str
    ) -> tuple[int, str]:
        nonlocal upload_called
        upload_called = True
        return 999, "https://example.com/should-not-happen.png"

    result = create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        image_brief="some brief",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        generate_image_fn=_placeholder_generate_image,
        upload_media_fn=_upload_should_not_be_called,
    )

    assert result.post_id == 4
    assert upload_called is False
    body = json.loads(route.calls.last.request.content)
    assert "featured_media" not in body


@respx.mock
def test_create_wp_draft_keeps_jsonld_outside_the_wrapper_div() -> None:
    """Regression for the reproduced collapsed-layout bug on post 4147: a
    JSON-LD `<script>` nested inside `dff-idea-body` trips up `wpautop` into
    emitting stray/unbalanced HTML that breaks the theme's page grid. The
    JSON-LD tail (as `assemble_final_html` appends it) must land AFTER the
    wrapper's closing `</div>`, never inside it -- matching the proven
    working pattern in `recipe-publisher/publishers/wordpress.py::_compose_body`.
    """
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 6, "link": "https://example.com/?p=6"})
    )
    fake_db = _FakeIdeasDb()
    jsonld = '<script type="application/ld+json">{"@type": "BlogPosting"}</script>'
    body_with_schema = f"<p>Full post body.</p>\n\n{jsonld}\n"

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html=body_with_schema,
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
    )

    content = json.loads(route.calls.last.request.content)["content"]
    assert content == (
        '<div class="dff-idea-body" style="max-width:720px;margin:0 auto;">'
        f"<p>Full post body.</p></div>\n\n{jsonld}"
    )
    div_close = content.index("</div>")
    script_open = content.index("<script")
    assert div_close < script_open, "JSON-LD script must come after the wrapper's closing </div>"


@respx.mock
def test_create_wp_draft_no_jsonld_present_wraps_body_unchanged() -> None:
    """No JSON-LD marker anywhere in body_html -- the split is a no-op and
    behavior matches the pre-fix wrapping exactly."""
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 7, "link": "https://example.com/?p=7"})
    )
    fake_db = _FakeIdeasDb()

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>No schema here.</p>",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
    )

    content = json.loads(route.calls.last.request.content)["content"]
    assert content == (
        '<div class="dff-idea-body" style="max-width:720px;margin:0 auto;">'
        "<p>No schema here.</p></div>"
    )


@respx.mock
def test_create_wp_draft_survives_media_upload_failure_best_effort() -> None:
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 5, "link": "https://example.com/?p=5"})
    )
    fake_db = _FakeIdeasDb()

    def _failing_upload(client: httpx.Client, image: GeneratedImage, slug: str) -> tuple[int, str]:
        raise RuntimeError("media upload failed: 500 server error")

    result = create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        image_brief="some brief",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        generate_image_fn=_fake_generate_image,
        upload_media_fn=_failing_upload,
    )

    assert result.post_id == 5
    body = json.loads(route.calls.last.request.content)
    assert "figure" not in body["content"]
    assert "featured_media" not in body
