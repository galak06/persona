"""The one pre-library reference photo a brand may still have on disk.

Before the tagged library existed, a brand had exactly one reference image:
`$BRAND_DIR/data/assets/persona_mascot_reference.{png,jpg,jpeg}`. Finding it is
the whole of this module, and it lives apart from `lib.crew.reference_library`
because it is not part of the library at all -- it is a migration affordance
with one caller, kept out of the read path so nothing there can drift back into
consulting it.

IMPORT-ONLY, and that is the point. `resolve_reference` does not look here, so
the existence of this file does NOT mean an image will be grounded on it. The
only caller that acts on it is `reference_library_store.import_legacy`, behind
the operator's "Import legacy reference" button, which COPIES the bytes into
`general` and leaves the original exactly where it is, forever. Until someone
clicks that button, the asset anchors nothing.

`lib.crew.wp_image` re-exports `resolve_reference_image_path` as a delegating
alias, which is the name most callers still reach for.
"""

from __future__ import annotations

from pathlib import Path

LEGACY_REFERENCE_STEM = "persona_mascot_reference"
LEGACY_REFERENCE_EXTENSIONS = (".png", ".jpg", ".jpeg")


def legacy_assets_dir(brand_dir: Path) -> Path:
    """`$BRAND_DIR/data/assets` -- where the legacy asset would be.

    The same directory `lib.crew.reference_library.assets_dir` returns; spelled
    again here so this module depends on nothing in the library.
    """
    return brand_dir / "data" / "assets"


def resolve_reference_image_path(brand_dir: Path) -> Path | None:
    """The brand's optional legacy reference photo, or `None`."""
    directory = legacy_assets_dir(brand_dir)
    for ext in LEGACY_REFERENCE_EXTENSIONS:
        candidate = directory / f"{LEGACY_REFERENCE_STEM}{ext}"
        if candidate.is_file():
            return candidate
    return None
