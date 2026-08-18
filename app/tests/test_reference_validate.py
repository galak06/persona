"""Tests for `lib.crew.reference_validate` -- reference-upload byte validation.

Each rejection asserts the HTTP status the upload route will map onto, so
the API phase can rely on 413/415/422 meaning size / type / content.
"""
# ruff: noqa: S101

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lib.crew.reference_validate import (
    MAX_UPLOAD_BYTES,
    ImageValidationError,
    probe_dimensions,
    sanitize_label,
    sniff_mime,
    validate_upload,
)


def _image(width: int = 400, height: int = 400, fmt: str = "PNG") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format=fmt)
    return buf.getvalue()


def _reject(raw: bytes, name: str = "photo.png") -> ImageValidationError:
    with pytest.raises(ImageValidationError) as excinfo:
        validate_upload(raw, name)
    return excinfo.value


def test_accepts_a_png_and_reports_the_sniffed_type() -> None:
    result = validate_upload(_image(), "Nalla eating kibble.png")
    assert result.content_type == "image/png"
    assert result.extension == ".png"
    assert (result.width, result.height) == (400, 400)
    assert result.size_bytes > 0
    assert result.label == "Nalla eating kibble.png"


def test_a_jpeg_named_png_is_typed_from_its_bytes() -> None:
    """The declared filename never decides anything -- the magic bytes do."""
    result = validate_upload(_image(fmt="JPEG"), "actually-a-jpeg.png")
    assert result.content_type == "image/jpeg"
    assert result.extension == ".jpg"


def test_empty_upload_is_422() -> None:
    assert _reject(b"").status_code == 422


def test_oversized_upload_is_413() -> None:
    error = _reject(b"\x89PNG" + b"\x00" * MAX_UPLOAD_BYTES)
    assert error.status_code == 413
    assert "limit" in error.reason


def test_unrecognized_magic_bytes_are_415() -> None:
    error = _reject(b"GIF89a" + b"\x00" * 64, "photo.gif")
    assert error.status_code == 415


def test_a_pdf_renamed_png_is_415() -> None:
    assert _reject(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 64).status_code == 415


def test_png_magic_over_garbage_bytes_is_422() -> None:
    """Passes the magic-byte gate, fails to decode -- exactly why `verify()`
    runs after sniffing rather than instead of it."""
    error = _reject(b"\x89PNG\r\n\x1a\n" + b"\x00" * 512)
    assert error.status_code == 422
    assert "not a readable image" in error.reason


def test_truncated_png_is_422() -> None:
    assert _reject(_image()[:120]).status_code == 422


def test_too_small_is_422() -> None:
    error = _reject(_image(128, 128))
    assert error.status_code == 422
    assert "at least" in error.reason


def test_too_large_is_422() -> None:
    error = _reject(_image(9000, 300))
    assert error.status_code == 422
    assert "exceed" in error.reason


def test_sniff_mime_recognizes_webp() -> None:
    assert sniff_mime(_image(fmt="WEBP")) == "image/webp"
    assert sniff_mime(b"nope") is None


def test_sanitize_label_strips_paths_and_caps_length() -> None:
    assert sanitize_label("../../etc/passwd") == "....etcpasswd"
    assert sanitize_label("a" * 300) == "a" * 120
    assert sanitize_label("") == ""


def test_probe_dimensions_never_raises() -> None:
    assert probe_dimensions(_image(320, 240)) == (320, 240)
    assert probe_dimensions(b"not an image") == (0, 0)
