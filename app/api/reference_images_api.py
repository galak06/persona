"""Reference-image library API -- the operator's side of the library.

  GET    /api/v1/reference-images                     -- categories + images
  POST   /api/v1/reference-images/categories          -- declare a tag
  POST   /api/v1/reference-images/images              -- upload a photo
  POST   /api/v1/reference-images/import-legacy       -- copy in the legacy asset
  DELETE /api/v1/reference-images/images/{id}         -- remove one photo
  GET    /api/v1/reference-images/images/{id}/raw     -- serve the bytes
  POST   /api/v1/reference-images/suggest-category    -- which tag fits? (advisory)

Storage, validation and resolution all live in `lib.crew.reference_library*`,
and the response shapes in `api.reference_images_schemas`; this module is
transport only -- it owns the brand lookup, the byte cap, the containment
guard, and the mapping from `ImageValidationError.status_code` onto an HTTP
response. Nothing here decides what a valid image is, or what one looks like
on the wire.

Two things are load-bearing rather than incidental:

* **`image_id` is `"<category>/<filename>"`**, so its routes need the `:path`
  converter, which means a raw `library_root / image_id` join could climb out
  of the brand directory. Every id-taking route therefore resolves the target
  and requires it to stay under `library_root` (same guard as
  `api.ideas_api.get_idea_slide`) -- 400 otherwise, before the id ever reaches
  the store.
* **Uploads are read in bounded chunks.** `await file.read()` with no argument
  would buffer an arbitrarily large body into memory before the size check
  could reject it, so the cap is enforced *while* reading and a body past
  `MAX_UPLOAD_BYTES` is abandoned mid-stream with a 413.
* **Every blocking call is pushed off the event loop.** The upload routes must
  be `async def` (they `await file.read(...)` for that bounded read), which
  means their bodies run ON the loop -- so a Pillow decode, an flock'd
  read-modify-write, or a 20-second vision-model round trip would stall every
  other request in the process. Each of those goes through
  `run_in_threadpool`, which is exactly what Starlette already does for the
  plain `def` routes below (they are correct as-is and need no wrapping).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from api.brand_context import resolve_api_brand
from api.reference_images_schemas import (
    CategoryCreate,
    CategoryCreated,
    CategorySuggestion,
    LibraryImage,
    LibraryResponse,
    categories,
    to_model,
)
from lib.crew.reference_library import library_root, list_category_labels, read_manifest
from lib.crew.reference_library_store import (
    add_image,
    create_category,
    delete_image,
    import_legacy,
    migrate_legacy_dirname,
)
from lib.crew.reference_validate import (
    MAX_UPLOAD_BYTES,
    ImageValidationError,
    ValidationResult,
    validate_upload,
)
from lib.crew.reference_vision import suggest_category
from lib.observability import get_logger

logger = get_logger(__name__)
router = APIRouter()

_CHUNK_BYTES = 1024 * 1024


def _brand_dir() -> Path:
    _brand_id, brand_dir = resolve_api_brand()
    return Path(brand_dir)


def _safe_target(brand_dir: Path, image_id: str) -> Path:
    """Resolve `image_id` under the library, or 400.

    `image_id` arrives through a `:path` converter and is therefore
    attacker-shaped: `../../etc/passwd` is a syntactically valid value. The
    containment check is what makes it inert.
    """
    root = library_root(brand_dir).resolve()
    target = (library_root(brand_dir) / image_id).resolve()
    if not target.is_relative_to(root):
        logger.warning("reference_library_path_escape_blocked", image_id=image_id[:200])
        raise HTTPException(status_code=400, detail="invalid image id")
    return target


async def _read_capped(file: UploadFile) -> bytes:
    """Buffer the upload, aborting with 413 the moment it exceeds the cap."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Image exceeds the {MAX_UPLOAD_BYTES // 1048576} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _validated(raw: bytes, filename: str | None) -> ValidationResult:
    """`validate_upload`, with its status code carried onto the response.

    Off the loop: validation decodes the image twice with Pillow, which is
    CPU-bound work on bytes an anonymous caller chose the size of.
    """
    try:
        return await run_in_threadpool(validate_upload, raw, filename or "")
    except ImageValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc


@router.get("/reference-images", response_model=LibraryResponse)
def get_library() -> LibraryResponse:
    """Every declared tag and every filed photo for the active brand.

    The only READ that runs the pre-rename directory migration: an operator
    opening this page is the first thing that touches a brand carried over
    from before the rename, and it must show them their existing photos.
    """
    brand_dir = _brand_dir()
    migrate_legacy_dirname(brand_dir)
    manifest = read_manifest(brand_dir)
    return LibraryResponse(
        categories=categories(manifest),
        images=[to_model(entry) for entry in manifest["images"]],
    )


@router.post("/reference-images/categories", response_model=CategoryCreated)
def post_category(body: CategoryCreate) -> CategoryCreated:
    """Declare a tag. Re-declaring an existing slug returns it unchanged."""
    try:
        category = create_category(_brand_dir(), body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CategoryCreated(slug=str(category["slug"]), label=str(category["label"]))


@router.post("/reference-images/images", response_model=LibraryImage)
async def post_image(
    file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency marker
    category: str = Form(""),
    label: str = Form(""),
) -> LibraryImage:
    """File an uploaded photo under `category` (defaults to `general`).

    Content-addressed, so re-uploading identical bytes to the same category
    returns the existing entry rather than a duplicate.
    """
    raw = await _read_capped(file)
    result = await _validated(raw, file.filename)
    entry = await run_in_threadpool(
        add_image,
        _brand_dir(),
        raw,
        category=category,
        content_type=result.content_type,
        label=label.strip() or result.label,
        source="upload",
    )
    return to_model(entry)


@router.post("/reference-images/import-legacy", response_model=LibraryImage)
def post_import_legacy() -> LibraryImage:
    """Copy `data/assets/persona_mascot_reference.*` into `general`.

    The original is only read -- it stays on disk as the last-resort fallback.
    404 when the brand has no legacy asset to import.
    """
    entry = import_legacy(_brand_dir())
    if entry is None:
        raise HTTPException(status_code=404, detail="this brand has no legacy mascot reference")
    return to_model(entry)


@router.get("/reference-images/images/{image_id:path}/raw")
def get_image_raw(image_id: str) -> FileResponse:
    """Serve one photo's bytes, uncached (the library is edited live)."""
    brand_dir = _brand_dir()
    target = _safe_target(brand_dir, image_id)
    entry = next(
        (i for i in read_manifest(brand_dir)["images"] if str(i.get("id")) == image_id), None
    )
    if entry is None or not target.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(
        target,
        media_type=str(entry.get("content_type") or "application/octet-stream"),
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/reference-images/images/{image_id:path}", status_code=204)
def remove_image(image_id: str) -> Response:
    """Drop a photo from the manifest and unlink its file. 404 if absent."""
    brand_dir = _brand_dir()
    _safe_target(brand_dir, image_id)
    if not delete_image(brand_dir, image_id):
        raise HTTPException(status_code=404, detail="image not found")
    return Response(status_code=204)


@router.post("/reference-images/suggest-category", response_model=CategorySuggestion)
async def post_suggest_category(
    file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency marker
) -> CategorySuggestion:
    """Which existing tag fits this photo? Advisory -- never an error.

    A failing model call is a 200 with `suggested_category: null`, because the
    UI's fallback is simply an unset selector. The bytes are still validated,
    so the same file that would be rejected on upload is rejected here too.
    """
    raw = await _read_capped(file)
    result = await _validated(raw, file.filename)
    brand_dir = _brand_dir()
    try:
        suggestion = await run_in_threadpool(
            suggest_category, raw, result.content_type, list_category_labels(brand_dir)
        )
    except Exception as exc:
        logger.warning("reference_library_suggest_failed", error=str(exc))
        suggestion = None
    return CategorySuggestion(suggested_category=suggestion)
