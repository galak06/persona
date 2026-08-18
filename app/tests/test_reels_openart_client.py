"""Tests for the pure, network-free helpers in lib.crew.reels.openart_client:
form-defaults extraction, argument mapping, and asset-URL/history-id
extraction. No MCP connection, no OAuth -- `generate_image` (the actual
multi-step round trip) isn't covered here, matching this repo's "never fake
the network call itself, only the pure logic around it" posture. Live
verification of `generate_image` needs a real OAuth-authorized session (see
the plan's verification section).
"""
# ruff: noqa: S101

from __future__ import annotations

import json

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from lib.crew.reels.openart_client import (
    OpenArtToolError,
    _build_image_params,
    _ensure_tools_available,
    _extract_asset_url,
    _extract_form_defaults,
    _extract_history_id,
    _extract_poll_after_seconds,
    _unwrap_single,
)


def _tool(name: str) -> Tool:
    return Tool(name=name, description="", inputSchema={"type": "object", "properties": {}})


def _result(payload: dict[str, object] | None = None, *, is_error: bool = False) -> CallToolResult:
    if payload is None:
        return CallToolResult(content=[], isError=is_error)
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))], isError=is_error)


# ── _ensure_tools_available ──────────────────────────────────────────────


def test_ensure_tools_available_passes_when_all_present() -> None:
    tools = [
        _tool("openart_model_form_get"),
        _tool("openart_generate_image"),
        _tool("openart_creation_wait"),
        _tool("openart_upload_sign"),
        _tool("openart_upload_list"),
        _tool("openart_account_get"),
    ]
    _ensure_tools_available(tools)  # must not raise


def test_ensure_tools_available_raises_when_missing() -> None:
    tools = [_tool("openart_model_form_get")]
    with pytest.raises(OpenArtToolError, match="missing expected tool"):
        _ensure_tools_available(tools)


# ── _extract_form_defaults ───────────────────────────────────────────────


def test_extract_form_defaults_from_text_json() -> None:
    result = _result({"model": "nano-banana-2-lite", "defaults": {"prompt": "", "aspectRatio": "1:1"}})
    assert _extract_form_defaults(result) == {"prompt": "", "aspectRatio": "1:1"}


def test_extract_form_defaults_raises_when_missing() -> None:
    result = _result({"model": "nano-banana-2-lite"})
    with pytest.raises(OpenArtToolError, match="no 'defaults' object"):
        _extract_form_defaults(result)


def test_extract_form_defaults_raises_on_tool_error() -> None:
    result = _result(is_error=True)
    with pytest.raises(OpenArtToolError, match="returned an error"):
        _extract_form_defaults(result)


# ── _build_image_params ───────────────────────────────────────────────────


@pytest.mark.parametrize("field_name", ["prompt", "instructions", "text", "description"])
def test_build_image_params_maps_onto_first_recognized_field(field_name: str) -> None:
    defaults = {field_name: "", "seed": -1}
    params = _build_image_params(defaults, "a cinematic dog portrait")
    assert params[field_name] == "a cinematic dog portrait"


def test_build_image_params_inserts_prompt_when_absent_from_defaults() -> None:
    """Regression test mirroring the real behavior confirmed live for the
    video path: `defaults` may omit the prompt-shaped field entirely since
    its only "default" would be an unusable empty string. Must insert the
    primary candidate field directly rather than raising."""
    defaults: dict[str, object] = {"videoCount": 1, "seed": -1}
    params = _build_image_params(defaults, "a cinematic dog portrait")
    assert params["prompt"] == "a cinematic dog portrait"
    assert params["seed"] == -1


def test_build_image_params_forces_vertical_aspect_ratio() -> None:
    defaults: dict[str, object] = {"prompt": "", "aspectRatio": "1:1"}
    params = _build_image_params(defaults, "instructions")
    assert params["aspectRatio"] == "9:16"


def test_build_image_params_leaves_unrelated_defaults_untouched() -> None:
    defaults = {"prompt": "", "seed": -1, "steps": 30}
    params = _build_image_params(defaults, "instructions")
    assert params["seed"] == -1
    assert params["steps"] == 30


def test_build_image_params_omits_visual_references_by_default() -> None:
    """text2image mode: confirmed live, its real `defaults` never contains
    a `visualReferences` key at all (that's an image2image-only field) --
    with no reference passed in, none should appear in params."""
    defaults: dict[str, object] = {"prompt": "", "resolution": "480p"}
    params = _build_image_params(defaults, "instructions")
    assert "visualReferences" not in params


def test_build_image_params_inserts_visual_references_when_given() -> None:
    """image2image mode: confirmed live, `visualReferences` is a required
    field typically *absent* from `defaults` (empty list is an unusable
    default) -- must insert it directly, same as `prompt`."""
    defaults: dict[str, object] = {"prompt": "", "imageCount": 1}
    reference: dict[str, object] = {
        "type": "image", "id": "abc123", "url": "https://cdn.openart.ai/a.jpg", "label": "hero.jpg"
    }
    params = _build_image_params(defaults, "instructions", visual_references=[reference])
    assert params["visualReferences"] == [reference]


def test_build_image_params_uses_existing_reference_field_name() -> None:
    defaults: dict[str, object] = {"prompt": "", "referenceImages": []}
    reference: dict[str, object] = {
        "type": "image", "id": "abc123", "url": "https://cdn.openart.ai/a.jpg", "label": "hero.jpg"
    }
    params = _build_image_params(defaults, "instructions", visual_references=[reference])
    assert params["referenceImages"] == [reference]
    assert "visualReferences" not in params


def test_build_image_params_passes_every_reference_through_verbatim() -> None:
    """The wire field has always been a *list*; the old single-element list
    was a caller limitation, not a format one. Several references must ride
    through in order, untouched."""
    defaults: dict[str, object] = {"prompt": "", "imageCount": 1}
    first: dict[str, object] = {"type": "image", "id": "r1", "url": "https://cdn/1.jpg"}
    second: dict[str, object] = {"type": "image", "id": "r2", "url": "https://cdn/2.jpg"}
    params = _build_image_params(defaults, "instructions", visual_references=[first, second])
    assert params["visualReferences"] == [first, second]


# ── _build_image_params: defaults-gated seed ──────────────────────────────


def test_build_image_params_sets_seed_when_defaults_declare_one() -> None:
    defaults: dict[str, object] = {"prompt": "", "seed": -1}
    params = _build_image_params(defaults, "instructions", seed=1234)
    assert params["seed"] == 1234


def test_build_image_params_never_injects_seed_absent_from_defaults() -> None:
    """A model that doesn't declare `seed` would reject the field, so an
    explicit seed must be dropped rather than added -- the gate is the key
    already existing in the model's own `defaults`."""
    defaults: dict[str, object] = {"prompt": "", "imageCount": 1}
    params = _build_image_params(defaults, "instructions", seed=1234)
    assert "seed" not in params


def test_build_image_params_leaves_declared_seed_default_alone_when_unset() -> None:
    defaults: dict[str, object] = {"prompt": "", "seed": -1}
    params = _build_image_params(defaults, "instructions")
    assert params["seed"] == -1


# ── _extract_history_id ───────────────────────────────────────────────────


def test_extract_history_id_from_text_json() -> None:
    result = _result({"historyId": "hist_abc123"})
    assert _extract_history_id(result) == "hist_abc123"


def test_extract_history_id_none_when_missing() -> None:
    result = _result({"image_url": "https://cdn.example/a.png"})
    assert _extract_history_id(result) is None


def test_extract_history_id_raises_on_tool_error() -> None:
    result = _result(is_error=True)
    with pytest.raises(OpenArtToolError, match="returned an error"):
        _extract_history_id(result)


# ── _extract_asset_url ─────────────────────────────────────────────────────


def test_extract_asset_url_from_structured_content() -> None:
    result = CallToolResult(content=[], structuredContent={"image_url": "https://cdn.example/a.png"})
    assert _extract_asset_url(result) == "https://cdn.example/a.png"


def test_extract_asset_url_from_text_json() -> None:
    result = _result({"url": "https://cdn.example/a.png"})
    assert _extract_asset_url(result) == "https://cdn.example/a.png"


def test_extract_asset_url_falls_back_to_bare_url_text() -> None:
    result = CallToolResult(content=[TextContent(type="text", text="https://cdn.example/b.png")])
    assert _extract_asset_url(result) == "https://cdn.example/b.png"


def test_extract_asset_url_returns_none_when_still_processing() -> None:
    result = _result({"status": "processing", "historyId": "hist_abc123"})
    assert _extract_asset_url(result) is None


def test_extract_asset_url_from_nested_resources_array() -> None:
    """Regression test for a real completed generation: the URL isn't at a
    flat top-level key at all -- it's nested inside `resources[0].url`."""
    result = _result(
        {
            "status": "COMPLETED",
            "historyId": "hist_abc123",
            "resources": [
                {"id": "r1", "mediaType": "image", "url": "https://cdn.openart.ai/a.jpg"}
            ],
        }
    )
    assert _extract_asset_url(result) == "https://cdn.openart.ai/a.jpg"


def test_extract_asset_url_raises_on_tool_error() -> None:
    result = CallToolResult(content=[], isError=True)
    with pytest.raises(OpenArtToolError, match="returned an error"):
        _extract_asset_url(result)


# ── _extract_poll_after_seconds ─────────────────────────────────────────────


def test_extract_poll_after_seconds_from_real_response_shape() -> None:
    """Regression test for a real run: `openart_creation_wait` returns
    almost immediately with this hint rather than blocking for the full
    `timeoutSeconds` -- a poll loop that ignores it burns its whole attempt
    budget in seconds instead of giving generation real time to finish."""
    result = _result({"status": "STILL_RUNNING", "historyId": "hist_abc123", "pollAfterSeconds": 10})
    assert _extract_poll_after_seconds(result) == 10.0


def test_extract_poll_after_seconds_defaults_when_absent() -> None:
    result = _result({"status": "STILL_RUNNING", "historyId": "hist_abc123"})
    assert _extract_poll_after_seconds(result) == 5.0


# ── _unwrap_single ───────────────────────────────────────────────────────────


def test_unwrap_single_drills_through_nested_single_exception_groups() -> None:
    inner = OpenArtToolError("the real error")
    wrapped = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [inner])])
    assert _unwrap_single(wrapped) is inner


def test_unwrap_single_leaves_multi_exception_groups_alone() -> None:
    """Can't pick one of several concurrent failures as "the" cause -- only
    unwraps when there's exactly one exception to drill into."""
    group = BaseExceptionGroup("outer", [OpenArtToolError("a"), OpenArtToolError("b")])
    assert _unwrap_single(group) is group


def test_unwrap_single_passes_through_non_group_exceptions() -> None:
    exc = OpenArtToolError("plain error")
    assert _unwrap_single(exc) is exc
