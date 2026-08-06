"""Tests for lib.crew.draft's optional per-brand reference-image threading.

Split out of test_crew_draft_image.py purely for file-size discipline (that
file is already at 275 lines). Same conventions: respx stubs the WP REST
endpoint, `generate_image_fn`/`upload_media_fn` are plain injected fakes --
no real Imagen/Gemini/WordPress network calls.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path

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
    def set_wp_result(self, idea_id: str, wp_post_id: str, wp_url: str) -> bool:
        return True

    def update_status(self, idea_id: str, status: str) -> bool:
        return True


def _fake_upload_media(client: httpx.Client, image: GeneratedImage, slug: str) -> tuple[int, str]:
    return 555, f"https://example.com/wp-content/uploads/{slug}.png"


@respx.mock
def test_create_wp_draft_passes_reference_bytes_when_brand_defines_one(tmp_path: Path) -> None:
    """The 'if brand defines it' case: a real file at the given path gets
    read and forwarded to the image-generation step as bytes + mime."""
    respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()
    ref_path = tmp_path / "persona_mascot_reference.png"
    ref_path.write_bytes(b"real-persona-mascot-photo-bytes")
    captured: dict[str, object] = {}

    def _capturing_generate_image(**kwargs: object) -> GeneratedImage:
        captured.update(kwargs)
        return GeneratedImage(
            url="nano_pro://fake",
            alt_text="t",
            provider="nano_pro",
            bytes_=b"png",
            content_type="image/png",
        )

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        image_brief="some brief",
        reference_image_path=ref_path,
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        generate_image_fn=_capturing_generate_image,
        upload_media_fn=_fake_upload_media,
    )

    assert captured["reference_image_bytes"] == b"real-persona-mascot-photo-bytes"
    assert captured["reference_image_mime"] == "image/png"


@respx.mock
def test_create_wp_draft_no_reference_path_generates_without_one() -> None:
    """The 'else use default' case: no `reference_image_path` supplied (the
    default) -- image generation runs exactly as it did before this feature,
    with no reference bytes at all."""
    respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 2, "link": "https://example.com/?p=2"})
    )
    fake_db = _FakeIdeasDb()
    captured: dict[str, object] = {}

    def _capturing_generate_image(**kwargs: object) -> GeneratedImage:
        captured.update(kwargs)
        return GeneratedImage(
            url="imagen://fake",
            alt_text="t",
            provider="imagen_fast",
            bytes_=b"png",
            content_type="image/png",
        )

    create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        image_brief="some brief",
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        generate_image_fn=_capturing_generate_image,
        upload_media_fn=_fake_upload_media,
    )

    assert captured["reference_image_bytes"] is None


@respx.mock
def test_create_wp_draft_missing_reference_file_soft_skips(tmp_path: Path) -> None:
    """A brand can define `reference_image_path` (e.g. via
    `resolve_reference_image_path`) that turns out not to exist on disk --
    must not crash draft creation, just generate without a reference."""
    respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 3, "link": "https://example.com/?p=3"})
    )
    fake_db = _FakeIdeasDb()
    missing_path = tmp_path / "does-not-exist.png"
    captured: dict[str, object] = {}

    def _capturing_generate_image(**kwargs: object) -> GeneratedImage:
        captured.update(kwargs)
        return GeneratedImage(
            url="imagen://fake",
            alt_text="t",
            provider="imagen_fast",
            bytes_=b"png",
            content_type="image/png",
        )

    result = create_wp_draft(
        idea_id="idea-1",
        title="t",
        body_html="<p>b</p>",
        image_brief="some brief",
        reference_image_path=missing_path,
        set_wp_result_fn=fake_db.set_wp_result,
        update_status_fn=fake_db.update_status,
        generate_image_fn=_capturing_generate_image,
        upload_media_fn=_fake_upload_media,
    )

    assert result.post_id == 3
    assert captured["reference_image_bytes"] is None
