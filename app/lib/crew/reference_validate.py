"""Byte validation for reference-image uploads.

The ONLY Pillow importer in the reference-library slice: the read side
(`lib.crew.reference_library`) runs inside the worker on every generated beat
and must stay dependency-light, so anything that has to actually decode
pixels lives here and is imported by the write side
(`lib.crew.reference_library_store`) and, later, the upload route.

Checks run in order, cheapest and most-certain first, and each raises
`ImageValidationError` carrying an HTTP status the API phase maps straight
onto its response:

1. size cap, 12 MiB                              -> 413
2. magic bytes (PNG / JPEG / WEBP only)          -> 415
3. Pillow `verify()` + re-open for `.size`       -> 422
4. dimensions 256..8000 on both axes             -> 422

Step 2 generalizes the JPEG-vs-PNG check that used to live in
`lib.crew.socialpost.compose` to the three formats the library accepts. Step 3
is what catches a PDF renamed `.png` -- and Pillow's own
`DecompressionBombError`, since these bytes arrive from a browser upload.
The declared filename is never trusted for anything: it is sanitized into a
display `label` and never touches the filesystem (files are stored
content-addressed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MIN_DIMENSION = 256
MAX_DIMENSION = 8000
MAX_LABEL_CHARS = 120

_PNG_MAGIC = b"\x89PNG"
_JPEG_MAGIC = b"\xff\xd8"

#: Content type -> the single canonical extension the store writes.
EXTENSION_BY_CONTENT_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

_DECODE_ERRORS = (OSError, ValueError, Image.DecompressionBombError)


class ImageValidationError(ValueError):
    """A rejected upload, with the HTTP status the API should return."""

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


@dataclass(frozen=True)
class ValidationResult:
    """Everything the store needs to file an accepted upload."""

    content_type: str
    extension: str
    width: int
    height: int
    size_bytes: int
    label: str


def sniff_mime(raw: bytes) -> str | None:
    """Content type from magic bytes, or `None` if it is not an accepted image.

    Never trusts a declared filename or client-supplied content type -- a
    `.png` extension on a PDF is exactly the case this exists for.
    """
    if raw[:4] == _PNG_MAGIC:
        return "image/png"
    if raw[:2] == _JPEG_MAGIC:
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def sanitize_label(name: str) -> str:
    """Strip a filename down to a safe display label (never a path)."""
    return re.sub(r"[^\w .\-]", "", name or "")[:MAX_LABEL_CHARS].strip()


def probe_dimensions(raw: bytes) -> tuple[int, int]:
    """`(width, height)`, or `(0, 0)` if the bytes will not decode.

    Non-raising on purpose: the store records dimensions as metadata, and
    unreadable metadata must not block filing an image whose bytes already
    passed `validate_upload` (or that came from WP media, which Pillow may
    decode differently than the source did).
    """
    try:
        with Image.open(BytesIO(raw)) as img:
            width, height = img.size
    except _DECODE_ERRORS:
        return (0, 0)
    return (int(width), int(height))


def validate_upload(raw: bytes, declared_name: str) -> ValidationResult:
    """Validate uploaded bytes, or raise `ImageValidationError`.

    Args:
        raw: The uploaded bytes, exactly as received.
        declared_name: The client's filename. Used only to derive `label`.

    Returns:
        A `ValidationResult` carrying the SNIFFED content type, its canonical
        extension, decoded dimensions, byte count, and sanitized label.
    """
    size = len(raw)
    if size == 0:
        raise ImageValidationError(422, "The uploaded file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise ImageValidationError(
            413, f"Image is {size / 1048576:.1f} MB; the limit is {MAX_UPLOAD_BYTES // 1048576} MB."
        )

    content_type = sniff_mime(raw)
    if content_type is None:
        raise ImageValidationError(415, "Only PNG, JPEG, and WEBP images are accepted.")

    try:
        # verify() consumes the file object, so the re-open below is
        # required rather than merely tidy -- it is also what turns a
        # truncated/malformed body into a rejection instead of a half image.
        with Image.open(BytesIO(raw)) as probe:
            probe.verify()
        with Image.open(BytesIO(raw)) as img:
            width, height = img.size
    except _DECODE_ERRORS as exc:
        raise ImageValidationError(422, f"The file is not a readable image: {exc}") from exc

    if min(width, height) < MIN_DIMENSION:
        raise ImageValidationError(
            422, f"Image is {width}x{height}; each side must be at least {MIN_DIMENSION}px."
        )
    if max(width, height) > MAX_DIMENSION:
        raise ImageValidationError(
            422, f"Image is {width}x{height}; no side may exceed {MAX_DIMENSION}px."
        )

    return ValidationResult(
        content_type=content_type,
        extension=EXTENSION_BY_CONTENT_TYPE[content_type],
        width=int(width),
        height=int(height),
        size_bytes=size,
        label=sanitize_label(declared_name),
    )
