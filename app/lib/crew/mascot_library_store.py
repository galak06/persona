"""Tagged mascot reference-image library -- WRITE side.

Every writer goes through here -- today the upload route and the explicit
`import_legacy` call behind it -- so there is one manifest shape and one
on-disk layout no matter who is filing the image. Reads live in
`lib.crew.mascot_library`; byte validation in `lib.crew.mascot_validate`.

Two invariants hold every write together:

* **Content-addressed storage.** A file is named `sha256(bytes)[:16]` plus
  the canonical extension for its SNIFFED content type, under its category
  directory. The uploader's filename never reaches the filesystem -- it
  survives only as the manifest entry's `label`. Re-adding identical bytes
  to the same category is therefore an idempotent no-op that returns the
  existing entry; the same photo may legitimately live in two categories
  (two ids, two files, one hash).
* **Nothing here is provisioned.** `data/assets/` is not created by brand
  provisioning for existing brands, so every write `mkdir(parents=True,
  exist_ok=True)` first, writes bytes atomically (temp + `os.replace`), and
  only then updates `library.json` inside `lib.io.jsonio.locked_json` --
  an flock'd read-modify-write, so two concurrent uploads cannot lose each
  other's entry.

The LEGACY `data/assets/persona_mascot_reference.png` is never touched and
never copied automatically: `import_legacy` is an explicit, operator-driven
call that COPIES it into `general` and leaves the original exactly where it
is, because `resolve_reference_image_path` still falls back to it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.crew.mascot_library import (
    CONTENT_TYPE_BY_SUFFIX,
    GENERAL_CATEGORY,
    Manifest,
    empty_manifest,
    library_root,
    manifest_path,
    resolve_reference_image_path,
    slugify,
)
from lib.crew.mascot_validate import EXTENSION_BY_CONTENT_TYPE, probe_dimensions, sniff_mime
from lib.io.jsonio import locked_json
from lib.observability import get_logger

logger = get_logger(__name__)

LEGACY_IMPORT_LABEL = "persona_mascot_reference (legacy)"


def add_image(
    brand_dir: Path,
    data: bytes,
    *,
    category: str,
    content_type: str,
    label: str,
    source: str,
) -> dict[str, Any]:
    """File `data` under `category` and return its manifest entry.

    Idempotent on content: identical bytes in the same category return the
    existing entry untouched (and re-write the file if it went missing).

    Args:
        brand_dir: The brand root; the library lives under `data/assets`.
        data: Raw image bytes, already validated by `mascot_validate`.
        category: Free-text tag; slugified. Empty falls back to `general`.
        content_type: The SNIFFED type -- decides the stored extension.
        label: Display name (the sanitized original filename, or a caption).
        source: How the image got here. Everything written today is
            `"upload"` (user-curated), which outranks any other origin at
            resolve time -- see `mascot_library.SOURCE_PRIORITY`.

    Raises:
        ValueError: `content_type` is not one the library stores.
        OSError: The bytes could not be written.
    """
    extension = EXTENSION_BY_CONTENT_TYPE.get(content_type)
    if extension is None:
        raise ValueError(f"unsupported content type for the mascot library: {content_type!r}")

    slug = slugify(category) or GENERAL_CATEGORY
    filename = f"{hashlib.sha256(data).hexdigest()[:16]}{extension}"
    image_id = f"{slug}/{filename}"
    destination = library_root(brand_dir) / slug / filename

    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes_atomic(destination, data)

    width, height = probe_dimensions(data)
    now = _utc_now()
    entry: dict[str, Any] = {
        "id": image_id,
        "category": slug,
        "filename": filename,
        "content_type": content_type,
        "bytes": len(data),
        "width": width,
        "height": height,
        "source": source,
        "label": label,
        "approved_at": now,
        "added_at": now,
    }

    with locked_json(manifest_path(brand_dir), empty_manifest()) as manifest:
        _normalize(manifest)
        existing = next((i for i in manifest["images"] if i.get("id") == image_id), None)
        if existing is not None:
            logger.info("mascot_library_add_noop", image_id=image_id, brand_dir=str(brand_dir))
            return dict(existing)
        _ensure_category(manifest, slug, category or slug, now)
        manifest["images"].append(entry)
    logger.info("mascot_library_image_added", image_id=image_id, source=source, bytes_len=len(data))
    return entry


def delete_image(brand_dir: Path, image_id: str) -> bool:
    """Drop `image_id` from the manifest and unlink its file.

    Returns `True` if an entry was removed, `False` if the library never had
    one (already-deleted is not an error -- the UI may double-fire).
    """
    removed: dict[str, Any] | None = None
    with locked_json(manifest_path(brand_dir), empty_manifest()) as manifest:
        _normalize(manifest)
        kept = []
        for image in manifest["images"]:
            if removed is None and image.get("id") == image_id:
                removed = image
            else:
                kept.append(image)
        manifest["images"] = kept
        still_referenced = any(i.get("id") == image_id for i in kept)

    if removed is None:
        return False
    if not still_referenced:
        path = library_root(brand_dir) / str(removed.get("category")) / str(removed.get("filename"))
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - filesystem-level failure
            logger.warning("mascot_library_unlink_failed", path=str(path), error=str(exc))
    logger.info("mascot_library_image_deleted", image_id=image_id)
    return True


def create_category(brand_dir: Path, label: str) -> dict[str, Any]:
    """Declare a category (and its directory). Existing slug -> returned as is.

    Raises:
        ValueError: `label` slugifies to nothing.
    """
    slug = slugify(label)
    if not slug:
        raise ValueError(f"category label {label!r} has no usable slug")
    (library_root(brand_dir) / slug).mkdir(parents=True, exist_ok=True)
    with locked_json(manifest_path(brand_dir), empty_manifest()) as manifest:
        _normalize(manifest)
        category = _ensure_category(manifest, slug, label, _utc_now())
    return dict(category)


def import_legacy(brand_dir: Path) -> dict[str, Any] | None:
    """COPY the legacy `persona_mascot_reference.*` into `general`.

    Returns the new (or already-present) manifest entry, or `None` if the
    brand has no legacy asset. The original file is only read: it stays on
    disk, unmodified, because it remains the last-resort fallback for brands
    that never populate the library.
    """
    legacy = resolve_reference_image_path(brand_dir)
    if legacy is None:
        return None
    data = legacy.read_bytes()
    content_type = sniff_mime(data) or CONTENT_TYPE_BY_SUFFIX.get(legacy.suffix.lower())
    if content_type is None:
        logger.warning("mascot_library_legacy_unrecognized", path=str(legacy))
        return None
    return add_image(
        brand_dir,
        data,
        category=GENERAL_CATEGORY,
        content_type=content_type,
        label=LEGACY_IMPORT_LABEL,
        source="upload",
    )


def _ensure_category(manifest: Manifest, slug: str, label: str, created_at: str) -> dict[str, Any]:
    """Append the category if the manifest has not declared it yet."""
    for category in manifest["categories"]:
        if isinstance(category, dict) and category.get("slug") == slug:
            return dict(category)
    category = {"slug": slug, "label": label or slug, "created_at": created_at}
    manifest["categories"].append(category)
    return category


def _normalize(manifest: Manifest) -> None:
    """Make a manifest read off disk safe to mutate.

    `locked_json` hands back whatever the file held (it falls back to the
    default only for missing/empty/unparseable files), so a hand-edited or
    partially-written manifest can still be the wrong shape here.
    """
    manifest.setdefault("version", empty_manifest()["version"])
    for key in ("categories", "images"):
        values = manifest.get(key)
        manifest[key] = (
            [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []
        )


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Temp file in the same directory + `os.replace` -- readers never see a
    half-written image (same contract as `lib.io.jsonio.write_json`)."""
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
