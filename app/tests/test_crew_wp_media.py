"""Tests for lib.crew.wp_media -- WP media-library upload for hero images.

Uses respx to stub the WP media endpoint (and, for the URL-fallback case, a
stand-in image host) -- no real network call happens.
"""
# ruff: noqa: S101

from __future__ import annotations

import httpx
import pytest
import respx

from lib.crew.wp_image import GeneratedImage
from lib.crew.wp_media import MediaUploadError, upload_wp_media

_MEDIA_URL = "https://example.com/wp-json/wp/v2/media"


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url="https://example.com")


@respx.mock
def test_upload_wp_media_uses_bytes_directly_when_present(client: httpx.Client) -> None:
    route = respx.post(_MEDIA_URL).mock(
        return_value=httpx.Response(
            201, json={"id": 42, "source_url": "https://example.com/wp-content/uploads/x.png"}
        )
    )
    image = GeneratedImage(
        url="imagen://fake", alt_text="alt", provider="imagen_fast", bytes_=b"raw-png-bytes"
    )

    media_id, source_url = upload_wp_media(client, image, "my-post-slug")

    assert route.called
    assert route.calls.last.request.content == b"raw-png-bytes"
    assert media_id == 42
    assert source_url == "https://example.com/wp-content/uploads/x.png"


@respx.mock
def test_upload_wp_media_downloads_url_when_bytes_missing(client: httpx.Client) -> None:
    respx.get("https://images.example.com/pic.jpg").mock(
        return_value=httpx.Response(
            200, content=b"downloaded-bytes", headers={"Content-Type": "image/jpeg"}
        )
    )
    route = respx.post(_MEDIA_URL).mock(
        return_value=httpx.Response(
            201, json={"id": 7, "source_url": "https://example.com/wp-content/uploads/y.jpg"}
        )
    )
    image = GeneratedImage(
        url="https://images.example.com/pic.jpg", alt_text="alt", provider="pexels", bytes_=None
    )

    media_id, _source_url = upload_wp_media(client, image, "my-post-slug")

    assert route.called
    assert route.calls.last.request.content == b"downloaded-bytes"
    assert media_id == 7


@respx.mock
def test_upload_wp_media_raises_on_wp_error(client: httpx.Client) -> None:
    respx.post(_MEDIA_URL).mock(return_value=httpx.Response(500, text="server error"))
    image = GeneratedImage(
        url="imagen://fake", alt_text="alt", provider="imagen_fast", bytes_=b"raw-png-bytes"
    )

    with pytest.raises(MediaUploadError):
        upload_wp_media(client, image, "my-post-slug")
