"""Instagram Graph API publisher tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import respx

from generators.recipe import Recipe
from publishers import instagram


@pytest.fixture(autouse=True)
def ig_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IG_USER_ID", "17841400000000000")
    monkeypatch.setenv("IG_GRAPH_ACCESS_TOKEN", "EAAG_short_token")


@pytest.fixture(autouse=True)
def _fast_reel_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reel polling in tests should be instantaneous."""
    monkeypatch.setattr(instagram, "_REEL_POLL_INTERVAL_SEC", 0.0)
    monkeypatch.setattr(instagram, "_POLL_INTERVAL_SEC", 0.0)


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(
        title="Beef Liver Training Treats",
        slug="beef-liver-training-treats",
        meta_description="...",
        body_markdown="...",
        ingredients=["liver"],
        steps=["bake"],
        prep_minutes=10,
        cook_minutes=15,
        yield_servings="~60",
        tags=["treats"],
        image_brief="overhead",
        ig_caption="A hook that comfortably fits under 125 chars and earns the scroll-stop.\n\n#doglife #dogrecipes",
    )


@respx.mock
def test_publish_happy_path(recipe: Recipe) -> None:
    ig_uid = "17841400000000000"
    base = "https://graph.facebook.com/v23.0"

    respx.post(f"{base}/{ig_uid}/media").respond(200, json={"id": "ctr_1"})
    respx.get(f"{base}/ctr_1").respond(200, json={"status_code": "FINISHED"})
    respx.post(f"{base}/{ig_uid}/media_publish").respond(200, json={"id": "media_1"})
    respx.get(f"{base}/media_1").respond(
        200, json={"permalink": "https://www.instagram.com/p/XXXX/"}
    )

    result = instagram.publish_to_instagram(recipe, image_url="https://cdn/x.png")

    assert result.media_id == "media_1"
    assert result.permalink.endswith("/p/XXXX/")
    assert result.warnings == []


@respx.mock
def test_container_error_raises(recipe: Recipe) -> None:
    ig_uid = "17841400000000000"
    base = "https://graph.facebook.com/v23.0"

    respx.post(f"{base}/{ig_uid}/media").respond(200, json={"id": "ctr_err"})
    respx.get(f"{base}/ctr_err").respond(200, json={"status_code": "ERROR"})

    with pytest.raises(instagram.InstagramError):
        instagram.publish_to_instagram(recipe, image_url="https://cdn/x.png")


@respx.mock
def test_publish_reel_happy_path(
    recipe: Recipe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WP_URL", "https://example.test")
    monkeypatch.setenv("WP_USER", "nallasdad")
    monkeypatch.setenv("WP_APP_PASSWORD", "pw pw pw pw pw pw")

    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00\x00\x00 ftypisom fake mp4 bytes")

    video_url = "https://example.test/wp-content/uploads/reel.mp4"
    respx.post("https://example.test/wp-json/wp/v2/media").respond(
        201, json={"id": 999, "source_url": video_url}
    )

    ig_uid = "17841400000000000"
    base = "https://graph.facebook.com/v23.0"
    respx.post(f"{base}/{ig_uid}/media").respond(200, json={"id": "reel_ctr_1"})
    respx.get(f"{base}/reel_ctr_1").respond(200, json={"status_code": "FINISHED"})
    respx.post(f"{base}/{ig_uid}/media_publish").respond(
        200, json={"id": "reel_media_1"}
    )
    respx.get(f"{base}/reel_media_1").respond(
        200, json={"permalink": "https://www.instagram.com/reel/ZZZZ/"}
    )

    result = instagram.publish_reel_to_instagram(recipe, video_path=video)

    assert result.media_id == "reel_media_1"
    assert result.permalink.endswith("/reel/ZZZZ/")

    create_call = next(
        c for c in respx.calls if c.request.url.path.endswith(f"/{ig_uid}/media")
    )
    assert "media_type=REELS" in str(create_call.request.url)
    assert video_url.replace(":", "%3A").replace("/", "%2F") in str(
        create_call.request.url
    ) or video_url in str(create_call.request.url)


def test_publish_reel_missing_video_file_raises(
    recipe: Recipe, tmp_path: Path
) -> None:
    with pytest.raises(instagram.InstagramError, match="video file not found"):
        instagram.publish_reel_to_instagram(
            recipe, video_path=tmp_path / "does-not-exist.mp4"
        )


@respx.mock
def test_token_expired_attempts_refresh(
    recipe: Recipe, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FB_APP_ID", "123")
    monkeypatch.setenv("FB_APP_SECRET", "shh")
    ig_uid = "17841400000000000"
    base = "https://graph.facebook.com/v23.0"

    respx.post(f"{base}/{ig_uid}/media").mock(
        side_effect=[
            respx.MockResponse(
                400, json={"error": {"code": 190, "type": "OAuthException"}}
            ),
            respx.MockResponse(200, json={"id": "ctr_2"}),
        ]
    )
    respx.get(f"{base}/oauth/access_token").respond(
        200, json={"access_token": "EAAG_long_refreshed"}
    )
    respx.get(f"{base}/ctr_2").respond(200, json={"status_code": "FINISHED"})
    respx.post(f"{base}/{ig_uid}/media_publish").respond(200, json={"id": "media_2"})
    respx.get(f"{base}/media_2").respond(
        200, json={"permalink": "https://www.instagram.com/p/YYYY/"}
    )

    result = instagram.publish_to_instagram(recipe, image_url="https://cdn/x.png")

    assert result.media_id == "media_2"
    assert any("refreshed" in w for w in result.warnings)


@respx.mock
def test_list_media_comments_returns_data() -> None:
    base = "https://graph.facebook.com/v23.0"
    respx.get(f"{base}/media_X/comments").respond(
        200,
        json={
            "data": [
                {"id": "c1", "text": "yum!", "username": "alice", "timestamp": "2026-01-01T00:00:00+0000"},
                {"id": "c2", "text": "my dog loves these", "username": "bob", "timestamp": "2026-01-02T00:00:00+0000"},
            ]
        },
    )
    out = instagram.list_media_comments("media_X")
    assert [c["id"] for c in out] == ["c1", "c2"]
    assert out[1]["username"] == "bob"


@respx.mock
def test_list_media_comments_propagates_error() -> None:
    base = "https://graph.facebook.com/v23.0"
    respx.get(f"{base}/media_X/comments").respond(403, text="forbidden")
    with pytest.raises(instagram.InstagramError, match="list_media_comments failed"):
        instagram.list_media_comments("media_X")


@respx.mock
def test_reply_to_instagram_comment_posts_message() -> None:
    base = "https://graph.facebook.com/v23.0"
    route = respx.post(f"{base}/c1/replies").respond(200, json={"id": "reply_99"})
    rid = instagram.reply_to_instagram_comment("c1", "thanks for the kind words!")
    assert rid == "reply_99"
    call = route.calls.last
    assert "message=thanks" in str(call.request.url)


@respx.mock
def test_reply_to_instagram_comment_raises_on_error() -> None:
    base = "https://graph.facebook.com/v23.0"
    respx.post(f"{base}/c1/replies").respond(500, text="oops")
    with pytest.raises(instagram.InstagramError, match="reply_to_instagram_comment failed"):
        instagram.reply_to_instagram_comment("c1", "hi")
