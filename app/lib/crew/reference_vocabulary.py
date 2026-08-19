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

from collections.abc import Sequence

from lib.crew.reference_library import GENERAL_CATEGORY, slugify


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
