"""Re-analysing a library that was already filed.

`lib.crew.reference_retag` exists because the tagger's prompt changed under a
library that had already been filled, and re-uploading cannot fix that: the
store is content-addressed, so identical bytes land on the same id under the
same tag and nothing moves. Re-tagging has to re-ask the question about photos
already on disk, then MOVE them.

Three properties earn tests, and they are the three that could quietly not
hold:

* the move actually happens -- bytes on disk under the new tag, a new id, and
  both flags plus the description carried over from the fresh answer;
* one bad photo does not end the run. A bulk re-tag costs one vision call per
  image, so aborting at image two would spend two calls and apply none of
  them. Every per-image failure has to become a row, not an exception;
* the route reports per-image outcomes with counts, because "12 photos, 9
  re-tagged, 1 skipped, 2 failed" is the only honest thing to show an operator
  who just paid for twelve model calls.

Real files under a `tmp_path` brand, same posture as the rest of the library
suite. The vision model is stubbed at `analyze_image`, exactly as
`tests/_reference_images_fakes.py` does it, so the brand seam -- its tag list,
its identity -- stays inside what is tested.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import lib.crew.reference_vision as vision_module
from lib.crew.reference_library import library_root, read_manifest
from lib.crew.reference_library_store import add_image
from lib.crew.reference_retag import FAILED, RETAGGED, SKIPPED, retag_library
from lib.crew.reference_vision import ImageAnalysis
from tests._reference_images_fakes import PREFIX, bind_brand, make_client, png


@pytest.fixture
def brand_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return bind_brand(tmp_path, monkeypatch)


@pytest.fixture
def client(brand_dir: Path) -> TestClient:
    return make_client()


def _file(brand_dir: Path, data: bytes, category: str = "general") -> dict[str, Any]:
    return add_image(
        brand_dir,
        data,
        category=category,
        content_type="image/png",
        label="a.png",
        source="upload",
    )


def _answers(monkeypatch: pytest.MonkeyPatch, answers: list[ImageAnalysis | None]) -> list[bytes]:
    """Hand out `answers` in order, recording the bytes each was asked about."""
    seen: list[bytes] = []
    queue = list(answers)

    def _fake(image: bytes, _content_type: str, **_kwargs: Any) -> ImageAnalysis | None:
        seen.append(image)
        return queue.pop(0) if queue else None

    monkeypatch.setattr(vision_module, "analyze_image", _fake)
    return seen


# ── the move ─────────────────────────────────────────────────────────────────


def test_a_retag_moves_the_file_and_updates_both_flags(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: ten photos in `general` become ten specific tags, and
    the bytes follow the tag because an id is `"<category>/<filename>"`."""
    entry = _file(brand_dir, png())
    _answers(
        monkeypatch,
        [
            ImageAnalysis(
                "A dirt path through pine forest.", "forest-trail", False, True, shows_persona=False
            )
        ],
    )

    [outcome] = retag_library(brand_dir)

    assert outcome.status == RETAGGED
    assert outcome.image_id == entry["id"]
    assert outcome.new_id.startswith("forest-trail/")
    assert (library_root(brand_dir) / outcome.new_id).is_file()
    assert not (library_root(brand_dir) / entry["id"]).exists()

    [stored] = read_manifest(brand_dir)["images"]
    assert stored["category"] == "forest-trail"
    assert stored["description"] == "A dirt path through pine forest."
    assert stored["shows_mascot"] is False
    assert stored["shows_persona"] is False
    # The tag is declared too, or the UI would list a photo under nothing.
    assert any(c["slug"] == "forest-trail" for c in read_manifest(brand_dir)["categories"])


def test_a_retag_can_set_both_flags_on_one_photo(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The photo of the person and the mascot together -- the case the single
    `shows_mascot` flag could not express."""
    _file(brand_dir, png())
    _answers(
        monkeypatch,
        [
            ImageAnalysis(
                "Both of them on the grass.", "outdoor-rest", True, True, shows_persona=True
            )
        ],
    )

    [outcome] = retag_library(brand_dir)

    assert (outcome.shows_mascot, outcome.shows_persona) == (True, True)
    [stored] = read_manifest(brand_dir)["images"]
    assert (stored["shows_mascot"], stored["shows_persona"]) == (True, True)


def test_an_unchanged_answer_is_reported_as_such(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that confirms what is already there is a success, not a
    failure -- and not something to hide, or the counts stop adding up."""
    _file(brand_dir, png(), category="forest-trail")
    _answers(monkeypatch, [ImageAnalysis("", "forest-trail", False, False)])

    [outcome] = retag_library(brand_dir)

    assert outcome.status == "unchanged"
    assert outcome.new_id == outcome.image_id


# ── resilience ───────────────────────────────────────────────────────────────


def test_one_failed_image_does_not_end_the_run(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expensive property. Three photos, the middle one's file deleted out
    from under the manifest: the other two must still be re-tagged, because
    their vision calls were paid for either way."""
    first = _file(brand_dir, png((1, 2, 3)))
    broken = _file(brand_dir, png((4, 5, 6)))
    last = _file(brand_dir, png((7, 8, 9)))
    (library_root(brand_dir) / broken["id"]).unlink()
    _answers(
        monkeypatch,
        [
            ImageAnalysis("one", "forest-trail", False, True),
            ImageAnalysis("three", "products", False, True),
        ],
    )

    outcomes = retag_library(brand_dir)

    by_id = {o.image_id: o for o in outcomes}
    assert len(outcomes) == 3
    assert by_id[broken["id"]].status == FAILED
    assert by_id[broken["id"]].detail  # the reason travels with the row
    assert by_id[first["id"]].new_id.startswith("forest-trail/")
    assert by_id[last["id"]].new_id.startswith("products/")


def test_an_exploding_vision_call_is_a_skip_not_a_crash(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`analyze_for_brand` swallows everything and answers `None`; the photo
    keeps the tag and flags it already had."""
    entry = _file(brand_dir, png())

    def _boom(*_args: object, **_kwargs: object) -> ImageAnalysis:
        raise RuntimeError("gemini exploded")

    monkeypatch.setattr(vision_module, "analyze_image", _boom)

    [outcome] = retag_library(brand_dir)

    assert outcome.status == SKIPPED
    assert outcome.new_id == entry["id"]
    assert read_manifest(brand_dir)["images"][0]["category"] == "general"


def test_a_failing_move_is_a_row_not_a_crash(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The apply step can fail on its own (a filesystem error during the
    move), and it runs after the vision call has already been paid for -- so
    it has to become a row like every other per-image failure."""
    import lib.crew.reference_retag as retag_module

    _file(brand_dir, png())
    _answers(monkeypatch, [ImageAnalysis("a", "forest-trail", False, True)])

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(retag_module, "update_image", _boom)

    [outcome] = retag_library(brand_dir)

    assert outcome.status == FAILED
    assert "read-only file system" in outcome.detail


def test_an_empty_library_is_a_no_op(brand_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, [])

    assert retag_library(brand_dir) == []


def test_each_photo_is_asked_about_its_own_bytes(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-tag reads the STORED bytes back; sending the same image twice
    would tag every photo identically and look like it worked."""
    first, second = png((1, 2, 3)), png((9, 9, 9))
    _file(brand_dir, first)
    _file(brand_dir, second)
    seen = _answers(
        monkeypatch,
        [
            ImageAnalysis("a", "forest-trail", False, True),
            ImageAnalysis("b", "products", False, True),
        ],
    )

    retag_library(brand_dir)

    assert seen == [first, second]


# ── the route ────────────────────────────────────────────────────────────────


def test_the_route_reports_counts_and_one_row_per_photo(
    client: TestClient, brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _file(brand_dir, png((1, 2, 3)))
    _file(brand_dir, png((4, 5, 6)))
    _answers(monkeypatch, [ImageAnalysis("a", "forest-trail", False, True), None])

    body = client.post(f"{PREFIX}/retag").json()

    assert (body["total"], body["retagged"], body["skipped"]) == (2, 1, 1)
    assert (body["unchanged"], body["failed"]) == (0, 0)
    assert [r["status"] for r in body["results"]] == [RETAGGED, SKIPPED]
    assert body["results"][0]["new_id"].startswith("forest-trail/")
    assert body["results"][1]["detail"] == "the vision pass had no answer"


def test_the_route_leaves_the_listing_consistent(
    client: TestClient, brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ids the UI is holding are stale after a re-tag, so it refetches --
    which has to show the photo under its new tag, servable at its new id."""
    _file(brand_dir, png())
    _answers(monkeypatch, [ImageAnalysis("A porch.", "home-exterior", False, True)])

    client.post(f"{PREFIX}/retag")
    listing = client.get(PREFIX).json()

    [image] = listing["images"]
    assert image["category"] == "home-exterior"
    assert image["description"] == "A porch."
    assert client.get(f"{PREFIX}/images/{image['id']}/raw").status_code == 200
