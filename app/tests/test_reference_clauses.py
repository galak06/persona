"""Which instruction rides along with the attached reference photo.

The library holds ANY reference image -- a product, a location, a person, a
setting -- so the old unconditional "use the EXACT SAME person and dog shown
in that photo" was wrong for most of them: it tells the model to reproduce a
subject that is not in the picture. `lib.crew.reference_clauses` splits that
into a mascot-identity clause and a neutral scene-grounding clause, chosen
from the manifest's `shows_mascot` flag.

That old wording was wrong a second way, fixed here too: it hardcoded a
SPECIES into a multi-brand engine's prompts. A brand describes its own mascot
via `site.mascot_name` and the optional `site.mascot_kind`, and the clauses
say nothing about what it is beyond what the brand said -- hence the
delivery-van cases below, which are not a joke but the actual contract.

Three halves now: the flag surviving the trip from `library.json` to a
resolved `ReferenceImage`, the clause chosen from it, and the clause naming
nothing the brand did not. Real files under a `tmp_path` brand dir, same
posture as `tests/test_reference_library.py` (which is at its line ceiling,
hence the separate file). "Nalla"/"dog" here is one concrete FIXTURE brand,
never an engine assumption.
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


def _image(*, shows_mascot: bool, shows_persona: bool = False) -> ReferenceImage:
    return ReferenceImage(
        id="x",
        category="eating",
        path=Path("/nowhere/x.png"),
        content_type="image/png",
        label="x",
        shows_mascot=shows_mascot,
        shows_persona=shows_persona,
    )


# ── the manifest flags reach the resolved entry ──────────────────────────────


def test_missing_keys_default_to_neither_subject_and_no_description(tmp_path: Path) -> None:
    """Every entry written before the vision tagger lacks these keys, and
    every entry written before the persona flag lacks that one. The default
    has to be the SAFE reading -- assume the photo shows neither subject --
    because the opposite default is exactly the bug being fixed."""
    _write_library(tmp_path, {})

    reference = _resolved(tmp_path)

    assert reference.shows_mascot is False
    assert reference.shows_persona is False
    assert reference.description == ""


def test_the_persona_flag_round_trips_independently(tmp_path: Path) -> None:
    """An entry may carry the persona flag without the mascot one -- a photo
    of the person alone -- and the pair has to survive the manifest read
    unmixed."""
    _write_library(tmp_path, {"shows_persona": True, "shows_mascot": False})

    reference = _resolved(tmp_path)

    assert reference.shows_persona is True
    assert reference.shows_mascot is False


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


def test_the_legacy_asset_never_becomes_a_resolved_reference(tmp_path: Path) -> None:
    """It used to arrive as a synthetic `category="legacy"`, `shows_mascot=True`
    entry -- an anchor nobody uploaded. `resolve_reference` cannot produce that
    shape any more, so no clause is ever built for it."""
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    (assets / "persona_mascot_reference.png").write_bytes(b"legacy")

    assert resolve_reference(tmp_path, "eating") is None


# ── the clause follows the flag ──────────────────────────────────────────────


def test_a_mascot_reference_gets_the_identity_clause() -> None:
    clause = reference_clause(_image(shows_mascot=True), "Nalla", "dog")

    assert clause == identity_clause("Nalla", "dog")
    assert "EXACT SAME subject" in clause
    assert "the mascot is Nalla, the brand's dog" in clause


def test_a_non_mascot_reference_gets_the_grounding_clause() -> None:
    """THE fix. Telling the model to reuse "the mascot in that photo" when the
    photo is a shelf of products makes it hallucinate one."""
    clause = reference_clause(_image(shows_mascot=False), "Nalla", "dog")

    assert clause == grounding_clause()
    assert "EXACT SAME" not in clause
    assert "Nalla" not in clause


# ── four flag combinations, four clauses ─────────────────────────────────────


def test_a_persona_only_photo_names_the_person_and_not_the_mascot() -> None:
    """The gap the persona flag closes. A photo of the person behind the brand
    used to carry the grounding clause -- "this is not the brand's persona" --
    which is precisely wrong, and the generated person drifted post to post."""
    clause = reference_clause(
        _image(shows_mascot=False, shows_persona=True), "Nalla", "dog", "Nalla's Dad"
    )

    assert "A reference photo of the brand's own persona is attached" in clause
    assert "reproduce the EXACT SAME subject" in clause
    assert "the persona is Nalla's Dad" in clause
    assert "the mascot is" not in clause
    assert "Nalla," not in clause, "the mascot is not in this picture to reproduce"
    assert clause.endswith(" ")


def test_a_mascot_only_photo_names_the_mascot_and_not_the_person() -> None:
    clause = reference_clause(
        _image(shows_mascot=True, shows_persona=False), "Nalla", "dog", "Nalla's Dad"
    )

    assert "A reference photo of the brand's own mascot is attached" in clause
    assert "the mascot is Nalla, the brand's dog" in clause
    assert "the persona is" not in clause
    assert "Nalla's Dad" not in clause


def test_a_photo_of_both_names_both_in_one_clause() -> None:
    """One of the operator's ten photos is the person and the mascot together;
    a clause naming only one of them under-constrains the other."""
    clause = reference_clause(
        _image(shows_mascot=True, shows_persona=True), "Nalla", "dog", "Nalla's Dad"
    )

    assert "A reference photo of the brand's own persona and mascot is attached" in clause
    assert "reproduce the EXACT SAME subjects" in clause
    assert "the persona is Nalla's Dad, and the mascot is Nalla, the brand's dog" in clause
    assert "Do not substitute different ones." in clause


def test_a_photo_of_neither_still_grounds() -> None:
    clause = reference_clause(
        _image(shows_mascot=False, shows_persona=False), "Nalla", "dog", "Nalla's Dad"
    )

    assert clause == grounding_clause()
    assert "Nalla" not in clause


def test_the_clause_stays_generic_when_the_brand_configured_no_names() -> None:
    """A brand with neither field set still gets a usable instruction: the
    attached photo is the description, and the engine may not invent one."""
    clause = reference_clause(_image(shows_mascot=True, shows_persona=True), "", "", "")

    assert "the brand's own persona and mascot" in clause
    assert "distinguishing features)" in clause, "no dangling aside where names would be"
    for word in ("dog", "animal", "pet", " he ", " she ", " man ", " woman "):
        assert word not in clause.lower()


def test_no_reference_at_all_stays_total_and_grounding() -> None:
    """`None` is vestigial now -- the reels and social generators stop before
    building a clause when nothing matches -- but the function must stay TOTAL:
    a missing reference may never become a raise, and if one ever does reach a
    prompt, grounding is the only thing it can honestly support."""
    assert reference_clause(None, "Nalla", "dog") == grounding_clause()


def test_the_grounding_clause_denies_the_identity_reading() -> None:
    """Not merely "no identity instruction" -- an explicit denial, because
    without it the model reads a stranger's pet in a stock photo as the
    brand's own and carries it into every later frame."""
    clause = grounding_clause()

    assert "NOT a photo of the brand's persona or mascot" in clause
    assert "do not carry their identity into the scene" in clause
    assert clause.endswith(" "), "prefixed straight onto a prompt, so it needs the separator"


# ── no brand may be assumed to have a dog ────────────────────────────────────


def test_a_mascot_that_is_not_an_animal_reads_correctly() -> None:
    """The whole point of `mascot_kind`. This engine is multi-brand, and the
    clause used to say "person and dog" to every one of them -- which told a
    brand whose mascot is a delivery van that it owned a dog."""
    clause = identity_clause("Rusty", "delivery van")

    assert "dog" not in clause.lower()
    assert "the mascot is Rusty, the brand's delivery van" in clause
    assert "EXACT SAME subject" in clause
    assert clause.endswith(" ")


def test_no_clause_ever_names_a_species_the_brand_did_not() -> None:
    """Belt and braces across every shape a brand's config can take: the only
    route a species has into a prompt is the brand writing it itself."""
    for name, kind in [("Nalla", ""), ("", ""), ("", "cartoon fox"), ("Rusty", "delivery van")]:
        clause = identity_clause(name, kind)

        assert "dog" not in clause.lower()
        assert "animal" not in clause.lower()
        assert "pet" not in clause.lower()
    assert "dog" not in grounding_clause().lower()


def test_the_identity_clause_survives_a_brand_with_no_mascot_name() -> None:
    """A cosmetic missing config field must not break the constraint."""
    clause = identity_clause("")

    assert "EXACT SAME subject" in clause
    assert "the mascot is" not in clause
    assert "distinguishing features)" in clause, "no dangling aside where the name would be"


def test_a_kind_without_a_name_still_describes_the_mascot() -> None:
    """A brand may configure what its mascot IS without naming it."""
    clause = identity_clause("", "cat")

    assert "the mascot is the brand's cat" in clause
