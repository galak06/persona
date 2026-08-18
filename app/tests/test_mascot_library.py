"""Tests for the tagged mascot reference-image library.

Covers `lib.crew.mascot_library` (resolution), `mascot_library_store` (writes),
and the provisioning constant they share. Follows `tests/test_crew_wp_image.py
::test_resolve_reference_image_path_finds_png`: real files under a `tmp_path`
brand dir, nothing about the filesystem mocked.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.brand_provisioning import MASCOT_LIBRARY_DIRNAME
from lib.crew.mascot_library import (
    GENERAL_CATEGORY,
    LIBRARY_DIRNAME,
    library_root,
    list_category_labels,
    manifest_path,
    read_manifest,
    resolve_reference,
    resolve_reference_image_path,
    source_rank,
)
from lib.crew.mascot_library_store import add_image, create_category, delete_image, import_legacy

# ── helpers ──────────────────────────────────────────────────────────────────


def _png(color: tuple[int, int, int] = (10, 20, 30), size: int = 300) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def _entry(category: str, filename: str, *, source: str = "upload") -> dict[str, Any]:
    return {
        "id": f"{category}/{filename}",
        "category": category,
        "filename": filename,
        "content_type": "image/png",
        "source": source,
        "label": filename,
    }


def _write_library(brand_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Seed a manifest and the files it names."""
    root = library_root(brand_dir)
    root.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (root / str(entry["category"])).mkdir(parents=True, exist_ok=True)
        (root / str(entry["category"]) / str(entry["filename"])).write_bytes(_png())
    slugs = sorted({str(e["category"]) for e in entries})
    manifest_path(brand_dir).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"slug": s, "label": s.title()} for s in slugs],
                "images": entries,
            }
        ),
        encoding="utf-8",
    )


def _legacy(brand_dir: Path, suffix: str = ".png") -> Path:
    assets = brand_dir / "data" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    path = assets / f"persona_mascot_reference{suffix}"
    path.write_bytes(_png((99, 99, 99)))
    return path


def _add(brand_dir: Path, data: bytes, category: str, source: str, label: str) -> dict[str, Any]:
    return add_image(
        brand_dir, data, category=category, content_type="image/png", label=label, source=source
    )


def _id(brand_dir: Path, category: str | None, seed: str = "") -> str:
    resolved = resolve_reference(brand_dir, category, seed=seed)
    assert resolved is not None
    return resolved.id


# ── resolution order ─────────────────────────────────────────────────────────


def test_exact_category_wins(tmp_path: Path) -> None:
    _write_library(tmp_path, [_entry("eating", "a.png"), _entry(GENERAL_CATEGORY, "b.png")])
    resolved = resolve_reference(tmp_path, "Eating")
    assert resolved is not None
    assert (resolved.id, resolved.category) == ("eating/a.png", "eating")
    assert resolved.path.is_file()


def test_unknown_category_falls_back_to_general(tmp_path: Path) -> None:
    _write_library(tmp_path, [_entry("eating", "a.png"), _entry(GENERAL_CATEGORY, "b.png")])
    assert _id(tmp_path, "snowboarding") == f"{GENERAL_CATEGORY}/b.png"


def test_empty_general_falls_back_to_any_non_empty_category(tmp_path: Path) -> None:
    _write_library(tmp_path, [_entry("walking", "w.png")])
    assert _id(tmp_path, "snowboarding") == "walking/w.png"


def test_empty_library_falls_back_to_legacy_png(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    resolved = resolve_reference(tmp_path, "eating")
    assert resolved is not None
    assert (resolved.path, resolved.category) == (legacy, "legacy")
    # Pins the PNG-mislabel fix: a .png asset is never announced as anything
    # but image/png.
    assert resolved.content_type == "image/png"


def test_legacy_jpg_reports_jpeg_content_type(tmp_path: Path) -> None:
    _legacy(tmp_path, ".jpg")
    resolved = resolve_reference(tmp_path, None)
    assert resolved is not None
    assert resolved.content_type == "image/jpeg"


def test_library_wins_over_legacy(tmp_path: Path) -> None:
    _legacy(tmp_path)
    _write_library(tmp_path, [_entry("eating", "a.png")])
    assert _id(tmp_path, "eating") == "eating/a.png"


def test_no_library_and_no_legacy_returns_none(tmp_path: Path) -> None:
    assert resolve_reference(tmp_path, "eating") is None
    assert resolve_reference_image_path(tmp_path) is None


# ── deterministic pick ───────────────────────────────────────────────────────


def _three(category: str = "eating") -> list[dict[str, Any]]:
    return [_entry(category, f"{n}.png") for n in ("a", "b", "c")]


def test_same_seed_picks_the_same_image(tmp_path: Path) -> None:
    _write_library(tmp_path, _three())
    first = resolve_reference(tmp_path, "eating", seed="idea-7:2")
    assert first is not None
    assert first == resolve_reference(tmp_path, "eating", seed="idea-7:2")


def test_different_seeds_spread_across_images(tmp_path: Path) -> None:
    """One reel's beats must not all condition on the same photo.

    Eight beats rather than three: `sha256("idea-7:0..3") % 3` genuinely
    lands on the same index four times running -- the hash doing its job, not
    a bug. Across eight, all three images are reached.
    """
    _write_library(tmp_path, _three())
    picked = {_id(tmp_path, "eating", f"idea-7:{beat}") for beat in range(8)}
    assert picked == {"eating/a.png", "eating/b.png", "eating/c.png"}


def test_empty_seed_takes_the_first_candidate(tmp_path: Path) -> None:
    _write_library(tmp_path, _three())
    assert _id(tmp_path, "eating") == "eating/a.png"


# ── uploads outrank harvested images ─────────────────────────────────────────


def test_source_rank_orders_upload_first() -> None:
    assert source_rank("upload") < source_rank("wp_media") < source_rank("")


def test_upload_always_beats_wp_media(tmp_path: Path) -> None:
    """One upload among five harvests still wins on every seed."""
    _write_library(
        tmp_path,
        [
            _entry("eating", "upload.png"),
            *[_entry("eating", f"h{n}.png", source="wp_media") for n in range(5)],
        ],
    )
    assert {_id(tmp_path, "eating", f"idea:{b}") for b in range(25)} == {"eating/upload.png"}


def test_wp_media_only_category_still_resolves(tmp_path: Path) -> None:
    _write_library(tmp_path, [_entry("eating", "h.png", source="wp_media")])
    assert _id(tmp_path, "eating", "idea:1") == "eating/h.png"


def test_general_fallback_tier_also_prefers_uploads(tmp_path: Path) -> None:
    _write_library(
        tmp_path,
        [
            _entry(GENERAL_CATEGORY, "harvest.png", source="wp_media"),
            _entry(GENERAL_CATEGORY, "mine.png"),
        ],
    )
    assert _id(tmp_path, "unknown-tag", "idea:3") == f"{GENERAL_CATEGORY}/mine.png"


def test_deleting_the_last_upload_falls_back_to_harvested(tmp_path: Path) -> None:
    upload = _add(tmp_path, _png((1, 2, 3)), "eating", "upload", "mine.png")
    harvest = _add(tmp_path, _png((4, 5, 6)), "eating", "wp_media", "from-site.png")
    assert _id(tmp_path, "eating", "s") == upload["id"]

    assert delete_image(tmp_path, upload["id"]) is True
    assert not (library_root(tmp_path) / upload["category"] / upload["filename"]).exists()
    assert _id(tmp_path, "eating", "s") == harvest["id"]
    assert delete_image(tmp_path, upload["id"]) is False


# ── manifest tolerance ───────────────────────────────────────────────────────


def test_malformed_manifest_reads_as_empty(tmp_path: Path) -> None:
    library_root(tmp_path).mkdir(parents=True)
    manifest_path(tmp_path).write_text("{not json at all", encoding="utf-8")
    assert read_manifest(tmp_path) == {"version": 1, "categories": [], "images": []}
    assert resolve_reference(tmp_path, "eating") is None


def test_entry_whose_file_vanished_is_skipped(tmp_path: Path) -> None:
    _write_library(tmp_path, [_entry("eating", "gone.png"), _entry("eating", "here.png")])
    (library_root(tmp_path) / "eating" / "gone.png").unlink()
    assert {_id(tmp_path, "eating", str(b)) for b in range(10)} == {"eating/here.png"}


def test_stray_file_without_a_manifest_entry_is_ignored(tmp_path: Path) -> None:
    _write_library(tmp_path, [_entry("eating", "a.png")])
    (library_root(tmp_path) / "eating" / "stray.png").write_bytes(_png())
    assert {_id(tmp_path, "eating", str(b)) for b in range(10)} == {"eating/a.png"}


# ── store ────────────────────────────────────────────────────────────────────


def test_add_image_is_idempotent_on_identical_bytes(tmp_path: Path) -> None:
    data = _png((7, 7, 7))
    first = _add(tmp_path, data, "Eating", "upload", "a.png")
    second = _add(tmp_path, data, "eating", "upload", "renamed.png")

    assert first["id"] == second["id"] == f"eating/{first['filename']}"
    assert (first["width"], first["height"], first["bytes"]) == (300, 300, len(data))
    assert second["label"] == "a.png", "the second add must not overwrite the first entry"
    assert len(read_manifest(tmp_path)["images"]) == 1

    # The same photo may legitimately live in two categories.
    assert _add(tmp_path, data, "walking", "upload", "a.png")["id"] != first["id"]
    assert len(read_manifest(tmp_path)["images"]) == 2


def test_add_image_rejects_an_unsupported_content_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported content type"):
        add_image(
            tmp_path,
            b"%PDF-1.4",
            category="eating",
            content_type="application/pdf",
            label="x.pdf",
            source="upload",
        )


def test_create_category_is_declared_once_and_listed(tmp_path: Path) -> None:
    created = create_category(tmp_path, "Eating Kibble")
    assert created["slug"] == "eating-kibble"
    assert create_category(tmp_path, "eating kibble")["label"] == "Eating Kibble"
    assert (library_root(tmp_path) / "eating-kibble").is_dir()
    assert list_category_labels(tmp_path) == ["Eating Kibble"]


def test_import_legacy_copies_and_leaves_the_original_untouched(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    before = legacy.read_bytes()

    entry = import_legacy(tmp_path)
    assert entry is not None
    assert (entry["category"], entry["content_type"]) == (GENERAL_CATEGORY, "image/png")
    assert legacy.is_file(), "the legacy asset must survive the import"
    assert legacy.read_bytes() == before
    assert (library_root(tmp_path) / entry["category"] / entry["filename"]).read_bytes() == before

    repeated = import_legacy(tmp_path)
    assert repeated is not None and repeated["id"] == entry["id"]


def test_import_legacy_without_an_asset_returns_none(tmp_path: Path) -> None:
    assert import_legacy(tmp_path) is None


def test_provisioning_uses_the_same_library_dirname() -> None:
    assert MASCOT_LIBRARY_DIRNAME == LIBRARY_DIRNAME
