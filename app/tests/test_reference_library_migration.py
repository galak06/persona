"""Tests for the copy-only `mascot_refs/` -> `reference_images/` migration.

The library was renamed once the images stopped being only mascot photos, and
brands provisioned before that carry a populated `data/assets/mascot_refs/`.
`lib.crew.reference_library_store.migrate_legacy_dirname` copies such a tree
into the new location and — this is the whole contract — leaves the old one
byte-identical, so a rollback to an older build finds its library intact.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from lib.crew.reference_library import (
    GENERAL_CATEGORY,
    library_root,
    manifest_path,
    read_manifest,
    resolve_reference,
)
from lib.crew.reference_library_store import (
    PRE_RENAME_LIBRARY_DIRNAME,
    add_image,
    migrate_legacy_dirname,
)

_CARRIED_OVER_ID = f"{GENERAL_CATEGORY}/abc123.png"


def _png(color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (300, 300), color).save(buf, format="PNG")
    return buf.getvalue()


def _pre_rename_tree(brand_dir: Path) -> Path:
    """A populated `data/assets/mascot_refs/`, as pre-rename builds left it."""
    root = brand_dir / "data" / "assets" / PRE_RENAME_LIBRARY_DIRNAME
    (root / GENERAL_CATEGORY).mkdir(parents=True)
    (root / GENERAL_CATEGORY / "abc123.png").write_bytes(_png())
    (root / "library.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"slug": GENERAL_CATEGORY, "label": "General"}],
                "images": [
                    {
                        "id": _CARRIED_OVER_ID,
                        "category": GENERAL_CATEGORY,
                        "filename": "abc123.png",
                        "content_type": "image/png",
                        "source": "upload",
                        "label": "abc123.png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root`, keyed by relative path -- an exact fingerprint."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _add(brand_dir: Path, color: tuple[int, int, int], category: str) -> str:
    entry = add_image(
        brand_dir,
        _png(color),
        category=category,
        content_type="image/png",
        label="new.png",
        source="upload",
    )
    return str(entry["id"])


def test_migration_copies_the_tree_and_leaves_the_original_untouched(tmp_path: Path) -> None:
    old = _pre_rename_tree(tmp_path)
    before = _snapshot(old)
    assert not manifest_path(tmp_path).exists()

    assert migrate_legacy_dirname(tmp_path) is True

    assert _snapshot(library_root(tmp_path)) == before
    assert old.is_dir(), "the pre-rename tree must survive the migration"
    assert _snapshot(old) == before, "the pre-rename tree must be byte-identical afterwards"
    resolved = resolve_reference(tmp_path, GENERAL_CATEGORY)
    assert resolved is not None and resolved.id == _CARRIED_OVER_ID


def test_migration_copies_into_an_empty_provisioned_directory(tmp_path: Path) -> None:
    """Provisioning creates `reference_images/` up front; an empty directory
    is not a migrated one, so the manifest — not the directory — is the guard."""
    _pre_rename_tree(tmp_path)
    library_root(tmp_path).mkdir(parents=True)

    assert migrate_legacy_dirname(tmp_path) is True
    assert manifest_path(tmp_path).is_file()


def test_migration_is_idempotent_and_never_reverts_a_live_library(tmp_path: Path) -> None:
    _pre_rename_tree(tmp_path)
    assert migrate_legacy_dirname(tmp_path) is True
    added = _add(tmp_path, (1, 2, 3), "eating")

    assert migrate_legacy_dirname(tmp_path) is False

    ids = {str(image["id"]) for image in read_manifest(tmp_path)["images"]}
    assert ids == {_CARRIED_OVER_ID, added}


def test_migration_does_nothing_without_a_pre_rename_tree(tmp_path: Path) -> None:
    assert migrate_legacy_dirname(tmp_path) is False
    assert not library_root(tmp_path).exists()


def test_a_write_migrates_before_it_files_anything(tmp_path: Path) -> None:
    """Every store entry point carries the migration, so an upload into a
    carried-over brand lands alongside its existing photos instead of into a
    fresh, empty library that silently orphans them."""
    _pre_rename_tree(tmp_path)

    added = _add(tmp_path, (9, 9, 9), GENERAL_CATEGORY)

    ids = {str(image["id"]) for image in read_manifest(tmp_path)["images"]}
    assert ids == {_CARRIED_OVER_ID, added}
