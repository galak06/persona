"""Shared builder for an on-disk reference library.

Four modules need a brand whose `data/assets/reference_images/` actually holds
photos -- `test_reels_images.py`, `test_reels_images_references.py`,
`test_socialpost_compose.py` and `test_reference_uploads_only.py` -- and each
had grown its own near-identical `_write_library`. They are all asserting on
the same contract now that a library upload is the ONLY thing allowed to
anchor a generated image, so the seeding belongs in one place where it cannot
drift out from under one of them.

Real files and a real `library.json`; nothing about the filesystem or the
resolver is mocked, so a test that resolves a photo here has resolved a photo.

A plain function rather than a fixture, matching `_reference_images_fakes.py`:
a pytest fixture imported into a test module shadows the parameter name that
requests it, which reads as a redefinition to any linter.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.crew.reference_library import library_root, manifest_path

CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def write_library(
    brand_dir: Path,
    categories: dict[str, str],
    *,
    ext: str = ".png",
    shows_mascot: bool = False,
) -> None:
    """`{category: marker}` -> one uploaded photo per category.

    The marker is the file's stem AND its bytes, so whichever photo a
    generator picks is identifiable from the reference it uploaded alone --
    `{"eating": "eat-bytes"}` writes `eating/eat-bytes.png` containing
    `b"eat-bytes"`. Every entry is filed as `source="upload"`, since that is
    the only source that may anchor a generation.
    """
    root = library_root(brand_dir)
    entries: list[dict[str, Any]] = []
    for category, marker in categories.items():
        (root / category).mkdir(parents=True, exist_ok=True)
        filename = f"{marker}{ext}"
        (root / category / filename).write_bytes(marker.encode())
        entries.append(
            {
                "id": f"{category}/{filename}",
                "category": category,
                "filename": filename,
                "content_type": CONTENT_TYPES[ext],
                "source": "upload",
                "label": marker,
                "shows_mascot": shows_mascot,
            }
        )
    manifest_path(brand_dir).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"slug": c, "label": c.title()} for c in categories],
                "images": entries,
            }
        ),
        encoding="utf-8",
    )


def write_legacy_asset(brand_dir: Path, suffix: str = ".png") -> Path:
    """The LEGACY `data/assets/persona_mascot_reference.*`.

    Import-only since "uploads only" landed: it is written here precisely so
    tests can prove that having it changes nothing until `import_legacy` files
    a copy into the library.
    """
    assets = brand_dir / "data" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    path = assets / f"persona_mascot_reference{suffix}"
    path.write_bytes(b"\x89PNG\r\n\x1a\nlegacy-asset-bytes")
    return path
