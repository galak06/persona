"""Tests for which reference tags a content planner may be offered.

Covers `lib.crew.reference_vocabulary.catch_all_clause`, the `with_photos`
filter on `list_category_labels`, and the resolver decision they both lean on:
an unmatched tag stays `None`. Lives outside `test_reference_library.py`, which
is already at this repo's 300-line ceiling.

The defect these pin, measured on a live 5-beat reel: the planner asked for
`general` on 2 of the 5 beats, `resolve_reference` returned `None` for both
(every photo had been re-tagged into specific collections), and those beats
fell back to the post's stock hero. Two causes, both fixed here -- the prompt
hard-coded `"general"` as an escape hatch, AND the brand's manifest still
DECLARED an empty `general`, so the tag list advertised it as a real choice.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

from lib.crew.reference_library import (
    GENERAL_CATEGORY,
    library_root,
    list_category_labels,
    manifest_path,
    resolve_reference,
)
from lib.crew.reference_vocabulary import catch_all_clause


def _write(brand_dir: Path, declared: list[tuple[str, str]], stocked: list[str]) -> None:
    """Seed a manifest declaring `declared` (slug, label) and holding one real
    file for each slug in `stocked` -- the two lists differ on purpose."""
    root = library_root(brand_dir)
    for slug in stocked:
        (root / slug).mkdir(parents=True, exist_ok=True)
        (root / slug / "a.png").write_bytes(b"not-a-real-png-but-a-real-file")
    root.mkdir(parents=True, exist_ok=True)
    manifest_path(brand_dir).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"slug": s, "label": label} for s, label in declared],
                "images": [
                    {
                        "id": f"{slug}/a.png",
                        "category": slug,
                        "filename": "a.png",
                        "content_type": "image/png",
                        "source": "upload",
                        "label": "a.png",
                    }
                    for slug in stocked
                ],
            }
        ),
        encoding="utf-8",
    )


# ── which labels a planner is offered ────────────────────────────────────────


def test_with_photos_drops_a_declared_but_empty_category(tmp_path: Path) -> None:
    """Exactly the live manifest's shape: `general` is still declared, but every
    photo has been re-tagged out of it. Offering it to a planner offers an
    image that cannot be generated."""
    _write(
        tmp_path,
        declared=[(GENERAL_CATEGORY, "General"), ("forest-trail", "forest-trail")],
        stocked=["forest-trail"],
    )
    assert list_category_labels(tmp_path) == ["General", "forest-trail"]
    assert list_category_labels(tmp_path, with_photos=True) == ["forest-trail"]


def test_the_default_still_lists_every_declared_category(tmp_path: Path) -> None:
    """The vision tagger reads the unfiltered list: it is building the tag
    vocabulary, so an empty tag is still a word it may re-use."""
    _write(tmp_path, declared=[("products", "Products")], stocked=[])
    assert list_category_labels(tmp_path) == ["Products"]
    assert list_category_labels(tmp_path, with_photos=True) == []


def test_with_photos_keeps_a_category_that_only_images_declare(tmp_path: Path) -> None:
    """A hand-edited manifest may hold photos under a tag no `categories` entry
    declares; the filter must not lose it."""
    _write(tmp_path, declared=[], stocked=["kitchen"])
    assert list_category_labels(tmp_path, with_photos=True) == ["kitchen"]


def test_with_photos_drops_a_category_whose_files_all_vanished(tmp_path: Path) -> None:
    _write(tmp_path, declared=[("kitchen", "Kitchen")], stocked=["kitchen"])
    (library_root(tmp_path) / "kitchen" / "a.png").unlink()
    assert list_category_labels(tmp_path, with_photos=True) == []


def test_an_absent_library_offers_nothing(tmp_path: Path) -> None:
    assert list_category_labels(tmp_path, with_photos=True) == []


# ── the catch-all clause ─────────────────────────────────────────────────────


def test_no_catch_all_clause_when_the_brand_has_no_general() -> None:
    assert catch_all_clause(("forest-trail", "home-exterior", "studio-mascot")) == ""


def test_catch_all_clause_echoes_the_label_verbatim() -> None:
    """`General` and `general` are different strings to a model told to copy
    the label verbatim, so the clause quotes what it was handed."""
    clause = catch_all_clause(("forest-trail", "General"))
    assert '"General"' in clause
    assert '"general"' not in clause


def test_catch_all_clause_matches_on_the_slug_not_the_spelling() -> None:
    """Matched the way `resolve_reference` matches -- through `slugify` -- so a
    differently-cased label counts and a merely similar one does not."""
    assert catch_all_clause(("General Scenes",)) == ""
    assert '" general "' in catch_all_clause((" general ",))


def test_an_empty_label_list_yields_no_clause() -> None:
    assert catch_all_clause(()) == ""


# ── the resolver stays strict (the decision these prompts rely on) ───────────


def test_a_near_miss_spelling_is_still_not_substituted(tmp_path: Path) -> None:
    """Deliberate: `resolve_reference` does NOT fuzzy-match an unknown tag onto
    the closest existing one. Edit distance tracks SPELLING, not subject --
    `studio-mascot` and `studio-products` are one word apart and show entirely
    different things, while `kitchen` and `feeding-bowl` share no letters and
    mean nearly the same. A "helpful" nearest-tag tier is what shipped a
    portrait in answer to a request for a product shot, and it stays removed.
    The closest-match judgement belongs to the planner, which knows what the
    image depicts; the resolver only knows strings.
    """
    _write(tmp_path, declared=[("forest-trail", "forest-trail")], stocked=["forest-trail"])
    assert resolve_reference(tmp_path, "forest-path") is None
    assert resolve_reference(tmp_path, "forest-trail") is not None


def test_a_declared_but_empty_general_does_not_rescue_a_miss(tmp_path: Path) -> None:
    """Why the prompt fix alone was not enough: even with the escape hatch gone,
    a manifest that merely DECLARES `general` resolves nothing through it."""
    _write(
        tmp_path,
        declared=[(GENERAL_CATEGORY, "General"), ("forest-trail", "forest-trail")],
        stocked=["forest-trail"],
    )
    assert resolve_reference(tmp_path, "snowboarding") is None
