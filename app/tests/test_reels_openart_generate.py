"""Round-trip tests for `lib.crew.reels.openart_client.generate_image`.

Everything below the MCP transport is faked (see
`tests/_reels_openart_fakes.py`). No claim is made here about OpenArt's
real network behavior -- what IS pinned is the CALL SHAPE this module
builds, which no pure-function test can reach:

  * several references ride into `params.visualReferences` as ONE list,
  * a shared `ReferenceCache` makes a repeated photo cost ZERO uploads,
  * the deprecated single-reference kwargs still produce a one-element
    list carrying the caller's filename / content type,
  * no references still asks for `text2image`,
  * capability logging stays silent unless it is asked for.

Without this, the `resolve_visual_references` call site inside
`_generate_image_impl` has no coverage at all -- a rename there is a
`NameError` that surfaces only in production.
"""
# ruff: noqa: S101

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from lib.crew.reels import openart_session
from lib.crew.reels.openart_client import generate_image
from lib.crew.reels.openart_session import ReferenceCache, ReferenceUpload
from tests import _reels_openart_fakes as fakes

_IMAGE_BYTES = fakes.IMAGE_BYTES
_PROMPT = "a cinematic dog portrait"


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> fakes.FakeSession:
    return fakes.install(monkeypatch)


def _run(**kwargs: Any) -> bytes:
    return asyncio.run(generate_image(_PROMPT, **kwargs))


# ── list-shaped references ────────────────────────────────────────────────


def test_two_references_ride_through_as_one_visual_references_list(
    harness: fakes.FakeSession,
) -> None:
    references = [
        ReferenceUpload(b"photo-one", "one.jpg", "image/jpeg"),
        ReferenceUpload(b"photo-two", "two.png", "image/png"),
    ]
    assert _run(references=references) == _IMAGE_BYTES

    signs = harness.tool_calls("openart_upload_sign")
    assert [sign["filename"] for sign in signs] == ["one.jpg", "two.png"]
    assert [sign["contentType"] for sign in signs] == ["image/jpeg", "image/png"]
    assert harness.generate_mode == "image2image"
    assert [ref["id"] for ref in harness.generate_params["visualReferences"]] == [
        "vr-up1",
        "vr-up2",
    ]


def test_no_references_stays_text2image(harness: fakes.FakeSession) -> None:
    assert _run() == _IMAGE_BYTES
    assert harness.tool_calls("openart_upload_sign") == []
    assert harness.generate_mode == "text2image"
    assert "visualReferences" not in harness.generate_params


def test_seed_reaches_params_when_the_model_declares_one(harness: fakes.FakeSession) -> None:
    _run(seed=99)
    assert harness.generate_params["seed"] == 99


# ── ReferenceCache: uploads scale with distinct photos, not beats ─────────


def test_reference_cache_makes_a_repeated_photo_cost_zero_uploads(
    harness: fakes.FakeSession,
) -> None:
    cache = ReferenceCache()
    _run(
        references=[ReferenceUpload(b"same-photo", "mascot.jpg", "image/jpeg")],
        reference_cache=cache,
    )
    _run(
        references=[ReferenceUpload(b"same-photo", "mascot.jpg", "image/jpeg")],
        reference_cache=cache,
    )

    assert len(harness.tool_calls("openart_upload_sign")) == 1
    assert len(harness.puts) == 1
    assert len(cache) == 1
    # Both generations still carried the reference -- cached, not dropped.
    generations = harness.tool_calls("openart_generate_image")
    sent = [gen["params"]["visualReferences"] for gen in generations]
    assert len(sent) == 2
    assert sent[0] == sent[1]
    assert [ref["id"] for ref in sent[0]] == ["vr-up1"]
    assert all(gen["mode"] == "image2image" for gen in generations)


def test_without_a_cache_every_call_re_uploads(harness: fakes.FakeSession) -> None:
    """Why the cache exists: the same photo across five beats is five
    uploads (each with a 2s readiness sleep) when nothing memoizes it."""
    reference = ReferenceUpload(b"same-photo", "mascot.jpg", "image/jpeg")
    _run(references=[reference])
    _run(references=[reference])
    assert len(harness.tool_calls("openart_upload_sign")) == 2


def test_distinct_photos_are_each_uploaded_once(harness: fakes.FakeSession) -> None:
    cache = ReferenceCache()
    _run(
        references=[
            ReferenceUpload(b"photo-one", "one.jpg", "image/jpeg"),
            ReferenceUpload(b"photo-two", "two.jpg", "image/jpeg"),
        ],
        reference_cache=cache,
    )
    _run(references=[ReferenceUpload(b"photo-one", "one.jpg", "image/jpeg")], reference_cache=cache)
    assert len(harness.tool_calls("openart_upload_sign")) == 2
    assert len(cache) == 2


def test_upload_list_window_is_wide_enough_for_several_uploads() -> None:
    """A just-signed id must not fall outside the "recent uploads" window
    while several references are in flight."""
    assert openart_session._UPLOAD_LIST_LIMIT >= 25


# ── deprecated single-reference shim ──────────────────────────────────────


def test_deprecated_single_reference_kwargs_still_work(harness: fakes.FakeSession) -> None:
    """`scripts/reels_images.py` still calls this form -- it must keep
    producing a one-element list with the caller's filename/content type."""
    assert (
        _run(
            reference_image=b"hero-bytes",
            reference_filename="mascot.png",
            reference_content_type="image/png",
        )
        == _IMAGE_BYTES
    )
    signs = harness.tool_calls("openart_upload_sign")
    assert len(signs) == 1
    assert signs[0]["filename"] == "mascot.png"
    assert signs[0]["contentType"] == "image/png"
    assert harness.generate_mode == "image2image"
    assert len(harness.generate_params["visualReferences"]) == 1


def test_deprecated_kwarg_appends_to_an_explicit_references_list(
    harness: fakes.FakeSession,
) -> None:
    _run(
        references=[ReferenceUpload(b"photo-one", "one.jpg", "image/jpeg")], reference_image=b"hero"
    )
    assert len(harness.generate_params["visualReferences"]) == 2


# ── capability logging (discovery only) ───────────────────────────────────


def _capture_debug(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    class _Recorder:
        def debug(self, event: str, **kwargs: Any) -> None:
            events.append((event, kwargs))

        def info(self, event: str, **kwargs: Any) -> None:
            return None

    monkeypatch.setattr(openart_session, "logger", _Recorder())
    return events


def test_capability_logging_emits_schema_and_full_form_payload(
    harness: fakes.FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _capture_debug(monkeypatch)
    monkeypatch.setenv("OPENART_LOG_SCHEMA", "1")
    _run()

    assert [event for event, _ in events] == [
        "openart_tool_input_schema",
        "openart_model_form_payload",
    ]
    fields = dict(events)
    assert "marker_openart_generate_image" in fields["openart_tool_input_schema"]["input_schema"]
    # The FULL form response, not just the `defaults` slice the caller keeps.
    payload = json.loads(fields["openart_model_form_payload"]["payload"])
    assert payload["model"] == "nano-banana-2-lite"
    assert payload["defaults"]["aspectRatio"] == "1:1"


def test_capability_logging_is_silent_by_default(
    harness: fakes.FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _capture_debug(monkeypatch)
    monkeypatch.delenv("OPENART_LOG_SCHEMA", raising=False)
    monkeypatch.setattr(logging.getLogger(), "level", logging.INFO)
    _run()
    assert events == []
