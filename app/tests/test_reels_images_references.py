"""Which photo grounds which beat, and what the model is told it is.

The sibling `test_reels_images.py` pins the fallback contract (OpenArt off,
unauthorized, a beat failing); this file pins the other half:

  * each beat resolves the reference matching ITS OWN `reference_category`;
    `""` and unrecognised labels reach the resolver verbatim and land on
    `general` -- callers never second-guess it
  * the upload carries the file's REAL name and content type (the old single-
    reference call left OpenArt's `image/jpeg` default on every PNG)
  * the prompt clause matches the photo: mascot identity only for a mascot
    photo, grounding otherwise; one seed and ONE `ReferenceCache` per run

Every reference here is a library upload, because those are the only images
allowed to anchor a generation at all -- what happens when there is no such
photo is `test_reference_uploads_only.py`'s subject.

Real files under a `tmp_path` brand dir; only OpenArt itself is stubbed.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts import reels_images

from lib.crew.reels.models import ReelBeat, ReelPlan
from lib.crew.reference_clauses import grounding_clause, identity_clause
from tests._reference_library_fakes import write_library as _write_library

_HERO = b"hero-image-bytes"


def _plan(categories: list[str]) -> ReelPlan:
    return ReelPlan(
        beats=[
            ReelBeat(
                headline=f"h{i}",
                subcopy=f"s{i}",
                image_prompt=f"prompt {i}",
                reference_category=category,
            )
            for i, category in enumerate(categories)
        ],
        ig_caption="ig",
        fb_caption="fb",
    )


@pytest.fixture()
def authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reels_images, "openart_enabled", lambda _d: True)
    monkeypatch.setattr(reels_images, "stored_auth_state", lambda: "ok")


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every `generate_image` call, recorded whole."""
    recorded: list[dict[str, Any]] = []

    async def _generate(prompt: str, **kwargs: Any) -> bytes:
        recorded.append({"prompt": prompt, **kwargs})
        return f"ai-{len(recorded)}".encode()

    monkeypatch.setattr(reels_images, "generate_image", _generate)
    return recorded


def _resolve(plan: ReelPlan, brand_dir: Path, idea_id: str = "idea-1") -> Any:
    return reels_images.resolve_images(plan, _HERO, brand_dir=brand_dir, idea_id=idea_id)


# ── per-beat category resolution ──────────────────────────────────────────────


def test_three_categories_resolve_to_three_different_photos(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The whole point: different scenes get different photos, not one asset
    conditioning all five frames."""
    _write_library(tmp_path, {"eating": "eat-bytes", "walking": "walk-bytes", "general": "gen"})

    resolved = _resolve(_plan(["eating", "walking", "general"]), tmp_path)

    used = [call["references"][0].data for call in calls]
    assert used == [b"eat-bytes", b"walk-bytes", b"gen"]
    assert len(set(used)) == 3  # three DIFFERENT files, not one repeated
    assert resolved.source == "openart"


def test_an_empty_category_lands_on_general(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """`""` is what the model emits when it declines to tag a beat; the
    resolver -- not this caller -- turns it into `general`."""
    _write_library(tmp_path, {"eating": "eat-bytes", "general": "gen-bytes"})

    _resolve(_plan(["", ""]), tmp_path)

    assert [call["references"][0].data for call in calls] == [b"gen-bytes"] * 2


def test_an_unrecognised_category_is_passed_through_not_rewritten(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """A hallucinated label behaves exactly like `""`: the caller validates
    nothing, so there is only one fallback rule to reason about."""
    _write_library(tmp_path, {"general": "gen-bytes"})

    _resolve(_plan(["surfing-on-mars"]), tmp_path)

    assert calls[0]["references"][0].data == b"gen-bytes"


def test_the_upload_carries_the_real_filename_and_content_type(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """THE PNG mislabel fix. The old single-reference call left OpenArt's
    `reference.jpg` / `image/jpeg` defaults in place, so every PNG reference --
    the legacy `persona_mascot_reference.png` included -- went up as a JPEG."""
    _write_library(tmp_path, {"general": "gen-bytes"})

    _resolve(_plan(["general"]), tmp_path)

    upload = calls[0]["references"][0]
    assert upload.filename == "gen-bytes.png"
    assert upload.content_type == "image/png"


def test_a_jpeg_upload_is_announced_as_a_jpeg(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The type follows the FILE, so the fix is not PNG-specific."""
    _write_library(tmp_path, {"general": "gen-bytes"}, ext=".jpg")

    _resolve(_plan(["general"]), tmp_path)

    upload = calls[0]["references"][0]
    assert (upload.filename, upload.content_type) == ("gen-bytes.jpg", "image/jpeg")


# ── prompt, seed and cache are run-wide ───────────────────────────────────────


def test_a_mascot_photo_prefixes_the_identity_clause(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """A photo tagged `shows_mascot` IS the brand's dog, so every prompt built
    on it carries the 'use the person and dog in that photo' wording."""
    _write_library(tmp_path, {"general": "gen-bytes"}, shows_mascot=True)
    (tmp_path / "config.json").write_text(json.dumps({"site": {"mascot_name": "Nalla"}}))

    _resolve(_plan(["general", "eating", ""]), tmp_path)

    clause = identity_clause("Nalla")
    assert "Nalla" in clause
    for index, call in enumerate(calls):
        assert call["prompt"] == f"{clause}prompt {index}"


def test_a_non_mascot_photo_prefixes_the_grounding_clause(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """THE bug. Most library photos are dishes, kitchens and products; naming a
    dog that isn't in the frame makes the model hallucinate one."""
    _write_library(tmp_path, {"general": "gen-bytes"})
    (tmp_path / "config.json").write_text(json.dumps({"site": {"mascot_name": "Nalla"}}))

    _resolve(_plan(["general"]), tmp_path)

    assert calls[0]["prompt"] == f"{grounding_clause()}prompt 0"
    assert "EXACT SAME" not in calls[0]["prompt"]
    assert "Nalla" not in calls[0]["prompt"]


def test_one_seed_reaches_every_beat(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Five beats, one seed: one reel should look like one shoot. Derived from
    the idea id, so a different reel differs and a re-run reproduces."""
    _write_library(tmp_path, {"general": "gen-bytes"})
    plan = _plan(["general"] * 5)

    _resolve(plan, tmp_path, idea_id="idea-A")
    first = {call["seed"] for call in calls}
    calls.clear()
    _resolve(plan, tmp_path, idea_id="idea-A")
    rerun = {call["seed"] for call in calls}
    calls.clear()
    _resolve(plan, tmp_path, idea_id="idea-B")
    other = {call["seed"] for call in calls}

    assert len(first) == 1  # ONE seed across all five beats
    assert first == rerun  # reproducible
    assert first != other  # but not shared between reels
    assert 0 <= next(iter(first)) < 2**31


def test_one_reference_cache_is_shared_across_beats(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Without a run-scoped cache, N categories x 5 beats means N x 5 uploads:
    every beat gets the SAME cache object so a repeated photo uploads once."""
    _write_library(tmp_path, {"eating": "eat-bytes", "general": "gen-bytes"})

    _resolve(_plan(["eating", "general", "eating", "", "general"]), tmp_path)

    caches = [call["reference_cache"] for call in calls]
    assert len(caches) == 5
    assert all(cache is caches[0] for cache in caches)
    assert caches[0] is not None


def test_a_second_reel_gets_its_own_cache(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Run-scoped, not process-scoped: a stale cache would outlive the signed
    upload ids it memoizes."""
    _write_library(tmp_path, {"general": "gen-bytes"})

    _resolve(_plan(["general"]), tmp_path, idea_id="idea-A")
    _resolve(_plan(["general"]), tmp_path, idea_id="idea-B")

    assert calls[0]["reference_cache"] is not calls[1]["reference_cache"]
