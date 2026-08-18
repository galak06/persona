"""Which instruction rides along with the attached reference photo.

The library holds ANY reference image -- a dish, a kitchen, a product, a
place -- so the old unconditional "use the EXACT SAME person and dog shown in
that photo" was wrong for most of them: it tells the model to reproduce a
subject that is not in the picture. `lib.crew.reference_clauses` splits that
into a mascot-identity clause and a neutral scene-grounding clause, chosen
from the manifest's `shows_mascot` flag.

Two halves, both pinned here: the flag surviving the trip from `library.json`
to a resolved `ReferenceImage`, and the clause chosen from it. Real files
under a `tmp_path` brand dir, same posture as `tests/test_reference_library.py`
(which is at its line ceiling, hence the separate file).
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.crew.reference_clauses import grounding_clause, identity_clause, reference_clause
from lib.crew.reference_library import (
    ReferenceImage,
    library_root,
    manifest_path,
    resolve_reference,
)


def _write_library(brand_dir: Path, entry_extra: dict[str, Any]) -> None:
    """A one-image library whose entry carries `entry_extra` verbatim, so a
    test can seed the new keys, or deliberately omit them."""
    root = library_root(brand_dir)
    (root / "eating").mkdir(parents=True, exist_ok=True)
    (root / "eating" / "a.png").write_bytes(b"png-bytes")
    manifest_path(brand_dir).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"slug": "eating", "label": "Eating"}],
                "images": [
                    {
                        "id": "eating/a.png",
                        "category": "eating",
                        "filename": "a.png",
                        "content_type": "image/png",
                        "source": "upload",
                        "label": "a.png",
                        **entry_extra,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _resolved(brand_dir: Path) -> ReferenceImage:
    reference = resolve_reference(brand_dir, "eating")
    assert reference is not None
    return reference


def _image(*, shows_mascot: bool) -> ReferenceImage:
    return ReferenceImage(
        id="x",
        category="eating",
        path=Path("/nowhere/x.png"),
        content_type="image/png",
        label="x",
        shows_mascot=shows_mascot,
    )


# ── the manifest flags reach the resolved entry ──────────────────────────────


def test_missing_keys_default_to_not_a_mascot_and_no_description(tmp_path: Path) -> None:
    """Every entry written before the vision tagger lacks both keys. The
    default has to be the SAFE reading -- assume it is not a mascot portrait --
    because the opposite default is exactly the bug being fixed."""
    _write_library(tmp_path, {})

    reference = _resolved(tmp_path)

    assert reference.shows_mascot is False
    assert reference.description == ""


def test_the_flags_round_trip_from_the_manifest(tmp_path: Path) -> None:
    _write_library(tmp_path, {"shows_mascot": True, "description": "the dog eating from a bowl"})

    reference = _resolved(tmp_path)

    assert reference.shows_mascot is True
    assert reference.description == "the dog eating from a bowl"


def test_a_false_flag_stays_false(tmp_path: Path) -> None:
    """An explicitly tagged non-mascot photo must not be confused with an
    untagged one by, say, a truthiness slip on the string 'false'."""
    _write_library(tmp_path, {"shows_mascot": False, "description": "a bowl of kibble"})

    reference = _resolved(tmp_path)

    assert reference.shows_mascot is False
    assert reference.description == "a bowl of kibble"


def test_the_legacy_asset_is_a_mascot_photo_by_definition(tmp_path: Path) -> None:
    """`persona_mascot_reference.png` has no manifest entry to tag, but the
    filename IS the tag -- brands that never migrate keep the identity clause."""
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    (assets / "persona_mascot_reference.png").write_bytes(b"legacy")

    reference = resolve_reference(tmp_path, "eating")

    assert reference is not None
    assert (reference.category, reference.shows_mascot) == ("legacy", True)


# ── the clause follows the flag ──────────────────────────────────────────────


def test_a_mascot_reference_gets_the_identity_clause() -> None:
    clause = reference_clause(_image(shows_mascot=True), "Nalla")

    assert clause == identity_clause("Nalla")
    assert "EXACT SAME person and dog" in clause
    assert "Nalla" in clause


def test_a_non_mascot_reference_gets_the_grounding_clause() -> None:
    """THE fix. Telling the model to reuse "the dog in that photo" when the
    photo is a bowl of food makes it hallucinate one."""
    clause = reference_clause(_image(shows_mascot=False), "Nalla")

    assert clause == grounding_clause()
    assert "EXACT SAME" not in clause
    assert "Nalla" not in clause


def test_no_reference_at_all_also_gets_the_grounding_clause() -> None:
    """A deliberate behaviour change: `None` means the caller falls back to the
    WP hero, whose content nobody has looked at and which is routinely stock.
    Grounding on it is all it can honestly support."""
    assert reference_clause(None, "Nalla") == grounding_clause()


def test_the_grounding_clause_denies_the_identity_reading() -> None:
    """Not merely "no identity instruction" -- an explicit denial, because
    without it the model reads a stray dog in a stock kitchen shot as the
    brand's own and carries it into every later frame."""
    clause = grounding_clause()

    assert "NOT a photo of the brand's persona or mascot" in clause
    assert "do not carry their identity into the scene" in clause
    assert clause.endswith(" "), "prefixed straight onto a prompt, so it needs the separator"


def test_the_identity_clause_survives_a_brand_with_no_mascot_name() -> None:
    """A cosmetic missing config field must not break the constraint."""
    clause = identity_clause("")

    assert "EXACT SAME person and dog" in clause
    assert "the dog is" not in clause
