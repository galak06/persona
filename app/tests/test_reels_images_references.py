"""Which photo grounds which beat -- `scripts.reels_images` x the tagged library.

The sibling `test_reels_images.py` pins the fallback contract (OpenArt off,
unauthorized, a beat failing). This file pins the other half: a reel's five
beats are no longer conditioned on one photo passed five times.

  * each beat resolves the reference matching ITS OWN `reference_category`
  * `""` and unrecognised labels reach the resolver verbatim and land on
    `general` -- callers never second-guess it
  * the upload carries the file's REAL name and content type (the old single-
    reference call left OpenArt's `image/jpeg` default on every PNG) -- for
    the hero fallback too, which has no filename and must be sniffed
  * one identity clause, one seed and ONE `ReferenceCache` per run

Real files under a `tmp_path` brand dir, same posture as
`tests/test_reference_library.py`; only OpenArt itself is stubbed.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts import reels_images

from lib.crew.reels.models import ReelBeat, ReelPlan
from lib.crew.reference_library import identity_clause, library_root, manifest_path

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


def _write_library(brand_dir: Path, files: dict[str, str], *, ext: str = ".png") -> None:
    """`{category: distinctive-bytes}` -> a one-image-per-category library.

    The bytes double as the assertion handle: whichever photo a beat picks is
    identifiable from the `ReferenceUpload` alone.
    """
    root = library_root(brand_dir)
    entries: list[dict[str, Any]] = []
    for category, marker in files.items():
        (root / category).mkdir(parents=True, exist_ok=True)
        filename = f"{marker}{ext}"
        (root / category / filename).write_bytes(marker.encode())
        entries.append(
            {
                "id": f"{category}-1",
                "category": category,
                "filename": filename,
                "content_type": "image/png" if ext == ".png" else "image/jpeg",
                "source": "upload",
                "label": marker,
            }
        )
    manifest_path(brand_dir).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"slug": c, "label": c.title()} for c in files],
                "images": entries,
            }
        ),
        encoding="utf-8",
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
    """The whole point: beats showing different scenes get different photos of
    the same real dog, instead of one asset conditioning all five frames."""
    _write_library(tmp_path, {"eating": "eat-bytes", "walking": "walk-bytes", "general": "gen"})

    resolved = _resolve(_plan(["eating", "walking", "general"]), tmp_path)

    used = [call["references"][0].data for call in calls]
    assert used == [b"eat-bytes", b"walk-bytes", b"gen"]
    assert len(set(used)) == 3  # three DIFFERENT files, not one repeated
    assert resolved.source == "openart"


def test_an_empty_category_lands_on_general(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """`""` is what the model emits when it declines to tag a beat. It goes to
    the resolver verbatim; the resolver -- not this caller -- picks `general`."""
    _write_library(tmp_path, {"eating": "eat-bytes", "general": "gen-bytes"})

    _resolve(_plan(["", ""]), tmp_path)

    assert [call["references"][0].data for call in calls] == [b"gen-bytes"] * 2


def test_an_unrecognised_category_is_passed_through_not_rewritten(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """A hallucinated label must behave exactly like `""` -- the caller does no
    validation of its own, so there is only one fallback rule to reason about."""
    _write_library(tmp_path, {"general": "gen-bytes"})

    _resolve(_plan(["surfing-on-mars"]), tmp_path)

    assert calls[0]["references"][0].data == b"gen-bytes"


def test_the_upload_carries_the_real_filename_and_content_type(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """THE PNG mislabel fix. The old single-reference call left OpenArt's
    `reference.jpg` / `image/jpeg` defaults in place, so every PNG reference --
    including the legacy `persona_mascot_reference.png` -- was uploaded
    declared as a JPEG."""
    _write_library(tmp_path, {"general": "gen-bytes"})

    _resolve(_plan(["general"]), tmp_path)

    upload = calls[0]["references"][0]
    assert upload.filename == "gen-bytes.png"
    assert upload.content_type == "image/png"


def test_the_legacy_asset_is_uploaded_as_a_png_too(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Brands that never migrate get the content-type fix as well."""
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    (assets / "persona_mascot_reference.png").write_bytes(b"legacy-bytes")

    _resolve(_plan(["eating"]), tmp_path)

    upload = calls[0]["references"][0]
    assert (upload.data, upload.filename) == (b"legacy-bytes", "persona_mascot_reference.png")
    assert upload.content_type == "image/png"


def test_a_png_hero_fallback_is_uploaded_as_a_png(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The SAME mislabel, on the last fallback. A brand with no library and no
    legacy asset grounds every beat on the WP hero -- which is a PNG -- and the
    hero arrives as bare bytes, so its type has to be sniffed rather than read
    off a filename."""
    png = b"\x89PNG\r\n\x1a\n" + b"hero-image-bytes"

    reels_images.resolve_images(_plan(["general"]), png, brand_dir=tmp_path, idea_id="idea-1")

    upload = calls[0]["references"][0]
    assert upload.data == png
    assert (upload.filename, upload.content_type) == ("reference.png", "image/png")


def test_an_unsniffable_hero_keeps_the_dataclass_defaults(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Sniffing is best-effort: bytes that match no known magic fall back to
    `ReferenceUpload`'s own defaults, i.e. the pre-existing behaviour, rather
    than failing a beat over a label."""
    _resolve(_plan(["general"]), tmp_path)

    upload = calls[0]["references"][0]
    assert (upload.filename, upload.content_type) == ("reference.jpg", "image/jpeg")


# ── prompt, seed and cache are run-wide ───────────────────────────────────────


def test_the_identity_clause_prefixes_every_prompt(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """A reference photo is attached on every call, so every prompt must carry
    the 'use the person and dog in the attached photo' instruction -- the same
    wording `lib.crew.wp_image` already proved out for the WP hero."""
    _write_library(tmp_path, {"general": "gen-bytes"})
    (tmp_path / "config.json").write_text(json.dumps({"site": {"mascot_name": "Nalla"}}))

    _resolve(_plan(["general", "eating", ""]), tmp_path)

    clause = identity_clause("Nalla")
    assert "Nalla" in clause
    for index, call in enumerate(calls):
        assert call["prompt"] == f"{clause}prompt {index}"


def test_a_brand_with_no_mascot_name_still_gets_a_clause(
    authorized: None, calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """No config, or no `site.mascot_name`: the clause degrades to the unnamed
    wording rather than the pipeline failing over a cosmetic field."""
    _write_library(tmp_path, {"general": "gen-bytes"})

    _resolve(_plan(["general"]), tmp_path)

    assert calls[0]["prompt"] == f"{identity_clause('')}prompt 0"


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
    """Without a run-scoped cache, N categories x 5 beats means N x 5 uploads.
    Every beat must be handed the SAME cache object so a repeated photo is
    uploaded once."""
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
