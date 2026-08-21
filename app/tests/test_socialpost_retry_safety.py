"""What a retry may and may not touch -- `lib.crew.socialpost.retry`.

Replacing an image is a destructive act aimed at a post a human is already
reviewing, so the interesting cases are all the ones where something goes
wrong: the operator must end up with the post they had, not a half-updated one.
Additive throughout -- the replacement is written to its own file, the row is
re-pointed only once that file exists, and the superseded image is unlinked
only once the row points at the new one.

Which photo it anchors on is `test_socialpost_retry.py`.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from lib.crew.socialpost import retry
from tests._reference_library_fakes import write_library
from tests._socialpost_retry_fakes import (
    IDEA_ID,
    NEW_IMAGE,
    OLD_IMAGE,
    OLD_IMAGE_BYTES,
    build_world,
    queued_idea,
    run_retry,
)


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return build_world(monkeypatch, tmp_path)


# ── what a retry may and may not touch ──────────────────────────────────────


def test_the_captions_survive_verbatim_and_only_the_image_changes(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """The operator may already be happy with the copy. A retry writes the
    stored captions and flags straight back; only the image and the alt text
    (which describes the image) are new."""
    write_library(tmp_path, {"studio-mascot": "mascot-bytes"}, shows_mascot=True)

    run_retry(world)

    written = world["written"][0]
    assert written["fb_caption"] == "the caption the operator already read"
    assert written["ig_caption"] == "the ig caption the operator already read"
    assert written["validation_flags"] == ["a-flag"]
    assert written["image_path"] == NEW_IMAGE
    assert written["image_alt"] == "a new alt text"
    assert written["source"] == "gemini"
    # The fresh plan's own captions were drafted and thrown away.
    assert written["fb_caption"] != world["plan"].fb_caption


def test_the_old_image_survives_until_the_row_points_at_the_new_one(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """Additive: the replacement is written to its own file, and the superseded
    one is only unlinked after the DB has accepted the new path."""
    write_library(tmp_path, {"studio-mascot": "mascot-bytes"}, shows_mascot=True)

    result = run_retry(world)

    assert result.image_path == NEW_IMAGE
    assert (tmp_path / NEW_IMAGE).read_bytes() == b"composed:a-generated-image"
    assert not (tmp_path / OLD_IMAGE).exists()  # retired only after the write


def test_a_refused_write_leaves_the_post_exactly_as_it_was(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """If the row moved under the run, the generated image is discarded rather
    than left orphaned -- and the image being reviewed is untouched."""
    write_library(tmp_path, {"studio-mascot": "mascot-bytes"}, shows_mascot=True)
    world["write_ok"] = False

    result = run_retry(world)

    assert (result.ok, result.reason) == (False, "write_refused")
    assert not (tmp_path / NEW_IMAGE).exists()
    assert (tmp_path / OLD_IMAGE).read_bytes() == OLD_IMAGE_BYTES
    assert world["restored"] == [IDEA_ID]


def test_a_failed_generation_keeps_the_post_reviewable(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """The image model falling over must cost the operator nothing: same image,
    same captions, row back in the review queue."""
    write_library(tmp_path, {"studio-mascot": "mascot-bytes"}, shows_mascot=True)
    world["image"] = None

    result = run_retry(world)

    assert (result.ok, result.reason) == (False, "generation_failed")
    assert world["written"] == []
    assert world["restored"] == [IDEA_ID]
    assert (tmp_path / OLD_IMAGE).read_bytes() == OLD_IMAGE_BYTES


def test_a_post_that_is_not_queued_is_never_claimed_or_written(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """Approved, rejected, or already being retried: the claim is the guard, and
    nothing after it runs."""
    write_library(tmp_path, {"studio-mascot": "mascot-bytes"}, shows_mascot=True)
    world["idea"] = queued_idea(social_post_status="scheduled")

    result = run_retry(world)

    assert (result.ok, result.reason) == (False, "not_queued")
    assert world["written"] == []
    assert world["restored"] == []  # nothing was claimed, so nothing to restore
    assert world["generate_calls"] == []


def test_an_unexpected_failure_still_puts_the_row_back(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crash mid-run must not leave the post wedged at 'composing', where it
    is invisible to review and cannot be approved or rejected."""
    write_library(tmp_path, {"studio-mascot": "mascot-bytes"}, shows_mascot=True)

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("the worker fell over")

    monkeypatch.setattr(retry, "_replan", _boom)

    with pytest.raises(RuntimeError):
        run_retry(world)

    assert world["restored"] == [IDEA_ID]


def _imported_modules(module: Any) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _attributes_used(module: Any, obj: str) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == obj
    }


def test_the_retry_module_cannot_publish() -> None:
    """Structural, not behavioural: nothing that posts to a platform is reachable
    from here, so no argument and no row state can make a retry publish.

    Asserted over the parsed imports rather than the text, because the module's
    own docstring says the words "publish" and "release" out loud.
    """
    imported = _imported_modules(retry)
    assert not any("worker_wp_ideas" in name for name in imported)
    assert not any(name.endswith("crewai_social_posts_pipeline") for name in imported)
    assert not any("publish" in name or "notifier" in name for name in imported)


def test_the_retry_module_only_writes_the_review_payload() -> None:
    """The only `social_post_db` write it may reach is the one that lands a row
    back at 'queued' with an image. `schedule_fb` (approve) and `set_fb_result`
    (publish) live in the same module and must stay unreachable from here."""
    assert _attributes_used(retry, "social_post_db") == {"set_pending_review"}
