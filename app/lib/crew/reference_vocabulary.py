"""What a content planner is allowed to name as a `reference_category`.

The three planners that pick a reference collection -- the reels beats, the WP
strategist, the social-post writer -- each render their own "Reference-photo
collection" section in their own voice (`lib.crew.*.prompts`). The one thing
they must never do independently is decide which tags exist, because getting
that wrong is silent and expensive.

It already happened. Every section used to close with *If none of them fits,
use "general"* -- a hard-coded escape hatch naming a collection the brand may
not keep. On a live 5-beat reel the planner took that exit twice; both beats
resolved to nothing (`resolve_reference` -> `None`) and fell back to a stock
hero, because every photo had long since been re-tagged out of `general`.
Two of five beats generated no image at all, and the only trace was a pair of
`reference_library_no_match` log lines.

So the rule is now: name a collection ONLY if it is in the list the caller
supplied (see `list_category_labels(..., with_photos=True)`, which is exactly
the set that resolves to a real photo), and tell the model to pick the closest
one rather than offering it an exit. A near-miss reference still anchors the
image on the brand's real subject; no reference means no generated image.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from lib.crew.reference_library import (
    GENERAL_CATEGORY,
    descriptions_by_category,
    list_category_labels,
    slugify,
)


def catch_all_clause(categories: Sequence[str]) -> str:
    """A sentence pointing at the brand's own catch-all collection, or `""`.

    Rendered only when `categories` actually contains one, so a brand that
    keeps a stocked `general` still gets its deliberate fallback offered and a
    brand that does not is never told about a collection it hasn't got. The
    label is echoed exactly as supplied -- the model is told to copy these
    verbatim, and `General` and `general` are not the same string to it.
    """
    catch_all = next((c for c in categories if slugify(c) == GENERAL_CATEGORY), "")
    if not catch_all:
        return ""
    return (
        f' The one general-purpose collection is "{catch_all}" -- reach for it only when no '
        "specific collection is anywhere near the scene."
    )


def described_categories(
    labelled: Sequence[str],
    examples: Mapping[str, Sequence[str]],
    *,
    max_examples: int = 2,
    max_chars: int = 110,
) -> list[tuple[str, str]]:
    """`(label, "<what its photos actually show>")` per category.

    The label is returned SEPARATELY from its description, never pre-joined
    into one string: the planner must copy the label verbatim into a field, and
    a line reading `home-exterior -- e.g. a porch` invites it to copy the whole
    line. The caller renders them on separate lines.

    The planner is asked to name the collection whose scenes match the brief
    it just wrote, but it has only ever been shown SLUGS. `home-exterior` does
    not say "a weathered cottage porch, picket fence, gravel path" to a model
    any more than it does to a person, so the match is a guess about a word.

    Live on 2026-08-31: a brief describing a dog's teeth being checked over a
    plate of kibble -- unambiguously indoors -- was tagged `home-exterior`,
    and the porch photo that resolved contributed nothing to the finished
    image. The pick was not careless; the label was all there was to go on.

    The descriptions come from the vision tagger that already ran at upload
    time, so this costs nothing and stays true to what the brand actually
    holds. A category whose photos carry no description degrades to its bare
    label -- the old behaviour, for that category only.
    """
    described: list[tuple[str, str]] = []
    for label in labelled:
        shown = [d.strip() for d in examples.get(slugify(label), ()) if d and d.strip()]
        joined = " / ".join(shown[:max_examples])
        if len(joined) > max_chars:
            joined = joined[: max_chars - 1].rstrip() + "…"
        described.append((label, joined))
    return described


def category_menu(brand_dir: Path) -> tuple[list[str], dict[str, str]]:
    """The exact pair a planner prompt needs: nameable labels + what each shows.

    One helper rather than three lines repeated at every call site, because the
    two halves MUST stay in step: the labels are the only strings a planner may
    emit (see this module's docstring on why a stray name costs the whole
    image), and the descriptions must key off those same labels or they silently
    render nothing.
    """
    labels = list_category_labels(brand_dir, with_photos=True)
    described = described_categories(labels, descriptions_by_category(brand_dir))
    return labels, {label: text for label, text in described if text}
