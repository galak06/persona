"""FB Reels publisher tests — respx-mocked 3-phase upload."""

from __future__ import annotations

from pathlib import Path

import pytest
import respx

from generators.recipe import Recipe
from publishers import facebook as fb


@pytest.fixture(autouse=True)
def fb_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FB_PAGE_ID", "PAGEID")
    monkeypatch.setenv("FB_PAGE_TOKEN", "EAAG_test")


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fb, "_FINISH_POLL_INTERVAL", 0.0)


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(
        title="PB & Banana Dog Biscuits",
        slug="pb-banana-biscuits",
        meta_description="...",
        body_markdown="...",
        ingredients=["2 lb beef liver"],
        steps=["step 1", "step 2", "step 3"],
        prep_minutes=10,
        cook_minutes=15,
        yield_servings="~60",
        tags=["treats"],
        image_brief="overhead",
        ig_caption=(
            "Hook that fits in 125 chars.\n\n"
            "\u2022 25 min, 3 ingredients\n"
            "\u2022 Oven at 300\u00b0F for two hours\n"
            "\u2022 Freezer friendly\n\n"
            "Comment RECIPE and I'll DM you the link.\n\n"
            "Question?\n\n"
            "#nallasdad #persona"
        ),
    )


@respx.mock
def test_publish_reel_happy_path(recipe: Recipe, tmp_path: Path) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00\x00\x00 ftypisom fake mp4")

    base = "https://graph.facebook.com/v23.0"
    page = "PAGEID"
    upload_url = "https://rupload.facebook.com/video-upload/v23.0/VID1"

    respx.post(f"{base}/{page}/video_reels").mock(
        side_effect=[
            # phase=start
            respx.MockResponse(200, json={"video_id": "VID1", "upload_url": upload_url}),
            # phase=finish
            respx.MockResponse(200, json={"success": True}),
        ]
    )
    respx.post(upload_url).respond(200, json={"success": True})
    respx.get(f"{base}/VID1").respond(
        200,
        json={"status": {"video_status": "ready"}, "post_id": "POST_1"},
    )
    respx.get(f"{base}/POST_1").respond(
        200, json={"permalink_url": "https://www.facebook.com/reel/POST_1"}
    )

    result = fb.publish_reel_to_facebook(recipe, video_path=video)

    assert result.video_id == "VID1"
    assert result.post_id == "POST_1"
    assert result.permalink and "/reel/POST_1" in result.permalink
    assert result.warnings == []


@respx.mock
def test_start_phase_error_raises(recipe: Recipe, tmp_path: Path) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"x")

    respx.post("https://graph.facebook.com/v23.0/PAGEID/video_reels").respond(
        400, json={"error": {"message": "bad request"}}
    )

    with pytest.raises(fb.FacebookError, match="reel start failed"):
        fb.publish_reel_to_facebook(recipe, video_path=video)


@respx.mock
def test_transfer_error_raises(recipe: Recipe, tmp_path: Path) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"x")

    base = "https://graph.facebook.com/v23.0"
    upload_url = "https://rupload.facebook.com/video-upload/v23.0/VID1"

    respx.post(f"{base}/PAGEID/video_reels").respond(
        200, json={"video_id": "VID1", "upload_url": upload_url}
    )
    respx.post(upload_url).respond(500, json={"error": "server"})

    with pytest.raises(fb.FacebookError, match="reel transfer failed"):
        fb.publish_reel_to_facebook(recipe, video_path=video)


def test_missing_video_raises(recipe: Recipe, tmp_path: Path) -> None:
    with pytest.raises(fb.FacebookError, match="video file not found"):
        fb.publish_reel_to_facebook(recipe, video_path=tmp_path / "nope.mp4")


def test_missing_page_id_raises(
    recipe: Recipe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FB_PAGE_ID", raising=False)
    v = tmp_path / "reel.mp4"
    v.write_bytes(b"x")
    with pytest.raises(fb.FacebookError, match="FB_PAGE_ID"):
        fb.publish_reel_to_facebook(recipe, video_path=v)


@respx.mock
def test_processing_never_ready_adds_warning(
    recipe: Recipe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fb, "_MAX_FINISH_POLLS", 2)
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"x")

    base = "https://graph.facebook.com/v23.0"
    upload_url = "https://rupload.facebook.com/video-upload/v23.0/VID1"
    respx.post(f"{base}/PAGEID/video_reels").mock(
        side_effect=[
            respx.MockResponse(200, json={"video_id": "VID1", "upload_url": upload_url}),
            respx.MockResponse(200, json={"success": True}),
        ]
    )
    respx.post(upload_url).respond(200, json={"success": True})
    respx.get(f"{base}/VID1").respond(
        200, json={"status": {"video_status": "processing"}}
    )

    result = fb.publish_reel_to_facebook(recipe, video_path=video)

    assert result.video_id == "VID1"
    assert result.post_id is None
    assert any("never reported ready" in w for w in result.warnings)


# ── publish_photo_post_to_facebook (social-post pipeline) ─────────────────


@respx.mock
def test_photo_post_happy_path(tmp_path: Path) -> None:
    image = tmp_path / "hook.jpg"
    image.write_bytes(b"\xff\xd8\xff fake jpeg")

    base = "https://graph.facebook.com/v23.0"
    photos_route = respx.post(f"{base}/PAGEID/photos").respond(
        200, json={"id": "PHOTO1", "post_id": "PAGEID_POST1"}
    )
    respx.get(f"{base}/PAGEID_POST1").respond(
        200, json={"permalink_url": "https://facebook.com/PAGEID/posts/POST1"}
    )

    result = fb.publish_photo_post_to_facebook(
        image_path=image,
        message="Bone broth is safe. https://x.com/p Follow us.",
        alt_text="A dog watching broth simmer.",
    )

    assert result.post_id == "PAGEID_POST1"
    assert result.permalink == "https://facebook.com/PAGEID/posts/POST1"
    assert result.warnings == []
    sent = photos_route.calls[0].request
    assert b"alt_text_custom" in sent.content
    assert b"fake jpeg" in sent.content


@respx.mock
def test_photo_post_retries_without_alt_text_on_rejection(tmp_path: Path) -> None:
    """alt_text_custom writability is undocumented — a rejection must degrade
    to a plain photo post, never lose the publish."""
    image = tmp_path / "hook.jpg"
    image.write_bytes(b"jpeg")

    base = "https://graph.facebook.com/v23.0"
    photos_route = respx.post(f"{base}/PAGEID/photos").mock(
        side_effect=[
            respx.MockResponse(400, json={"error": {"message": "unknown param"}}),
            respx.MockResponse(200, json={"id": "PHOTO1", "post_id": "P1"}),
        ]
    )
    respx.get(f"{base}/P1").respond(200, json={"permalink_url": "https://fb/p"})

    result = fb.publish_photo_post_to_facebook(
        image_path=image, message="msg", alt_text="alt"
    )

    assert result.post_id == "P1"
    assert photos_route.call_count == 2
    assert b"alt_text_custom" not in photos_route.calls[1].request.content
    assert any("retrying without" in w for w in result.warnings)


@respx.mock
def test_photo_post_hard_failure_raises(tmp_path: Path) -> None:
    image = tmp_path / "hook.jpg"
    image.write_bytes(b"jpeg")
    respx.post("https://graph.facebook.com/v23.0/PAGEID/photos").respond(
        500, json={"error": {"message": "server error"}}
    )
    with pytest.raises(fb.FacebookError, match="photo post failed"):
        fb.publish_photo_post_to_facebook(image_path=image, message="msg")


def test_photo_post_missing_image_raises(tmp_path: Path) -> None:
    with pytest.raises(fb.FacebookError, match="image not found"):
        fb.publish_photo_post_to_facebook(
            image_path=tmp_path / "nope.jpg", message="msg"
        )


def test_photo_post_dry_run_no_network(tmp_path: Path) -> None:
    image = tmp_path / "hook.jpg"
    image.write_bytes(b"jpeg")
    result = fb.publish_photo_post_to_facebook(
        image_path=image, message="msg", dry_run=True
    )
    assert result.post_id == "dryrun_post_id"
