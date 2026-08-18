"""Reference-image library API -- the operator's side of the library.

  GET    /api/v1/reference-images                     -- categories + images
  POST   /api/v1/reference-images/categories          -- declare a tag
  POST   /api/v1/reference-images/images              -- upload a photo (auto-tagged)
  POST   /api/v1/reference-images/import-legacy       -- copy in the legacy asset
  PATCH  /api/v1/reference-images/images/{id}         -- re-tag / re-flag one photo
  DELETE /api/v1/reference-images/images/{id}         -- remove one photo
  GET    /api/v1/reference-images/images/{id}/raw     -- serve the bytes

Storage, validation and resolution all live in `lib.crew.reference_library*`,
and the response shapes in `api.reference_images_schemas`; this module is
transport only -- it owns the brand lookup, the byte cap, the containment
guard, and the mapping from `ImageValidationError.status_code` onto an HTTP
response. Nothing here decides what a valid image is, or what one looks like
on the wire.

**Uploads tag themselves.** `lib.crew.reference_vision.analyze_for_brand` looks
at the bytes and answers with a description, a category (reused from the brand's
tags, or newly proposed and then created by the store) and a per-image
`shows_mascot` flag -- so choosing a tag is an EDIT the operator may make
afterwards, not a decision they must make first. That call is advisory in the
strongest sense: any failure of it degrades to "filed under `general`,
untagged", never to a failed upload. The `category` form field survives as an
override for scripted callers, and wins when it is non-empty.

Three things are load-bearing rather than incidental:

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
    ImageUpdate,
    LibraryImage,
    LibraryResponse,
    categories,
    to_model,
)
from lib.crew.reference_library import library_root, read_manifest
from lib.crew.reference_library_edit import update_image
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
from lib.crew.reference_vision import analyze_for_brand
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
    """File an uploaded photo, tagged by the vision pass.

    `category` is an OPTIONAL override: supply it and it wins, leave it out
    (as the UI does) and the model's choice is used -- a tag it reused from
    the brand's list, or a new one the store declares on the way in. If the
    model could not be asked at all, the photo still lands, under `general`.

    Content-addressed, so re-uploading identical bytes to the same category
    returns the existing entry rather than a duplicate.
    """
    raw = await _read_capped(file)
    result = await _validated(raw, file.filename)
    brand_dir = _brand_dir()
    analysis = await run_in_threadpool(analyze_for_brand, brand_dir, raw, result.content_type)
    entry = await run_in_threadpool(
        add_image,
        brand_dir,
        raw,
        # Empty is not "general" here -- `add_image` slugifies it to `general`
        # itself, so the fallback lives in exactly one place.
        category=category.strip() or (analysis.category if analysis else ""),
        content_type=result.content_type,
        label=label.strip() or result.label,
        source="upload",
        shows_mascot=bool(analysis and analysis.shows_mascot),
        description=analysis.description if analysis else "",
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


@router.patch("/reference-images/images/{image_id:path}", response_model=LibraryImage)
def patch_image(image_id: str, body: ImageUpdate) -> LibraryImage:
    """Re-tag a photo and/or flip its mascot flag. 404 if the id is unknown.

    Re-tagging MOVES the file, so the returned entry carries a different id
    than the one in the URL -- callers must adopt it rather than reuse theirs.
    """
    brand_dir = _brand_dir()
    _safe_target(brand_dir, image_id)
    entry = update_image(
        brand_dir, image_id, category=body.category, shows_mascot=body.shows_mascot
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="image not found")
    return to_model(entry)


@router.delete("/reference-images/images/{image_id:path}", status_code=204)
def remove_image(image_id: str) -> Response:
    """Drop a photo from the manifest and unlink its file. 404 if absent."""
    brand_dir = _brand_dir()
    _safe_target(brand_dir, image_id)
    if not delete_image(brand_dir, image_id):
        raise HTTPException(status_code=404, detail="image not found")
    return Response(status_code=204)
