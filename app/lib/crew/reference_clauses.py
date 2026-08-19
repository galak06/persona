"""What to tell the image model about the reference photo it was handed.

Split out of `lib.crew.reference_library` (which was at the 300-line ceiling)
because this is no longer one constant string: it is a decision.

The library holds ANY reference image -- a product, a location, a person, a
setting, a style plate -- not only portraits of the brand's persona and
mascot. Two different instructions follow from that, and sending the wrong one
actively degrades the generation:

  * `identity_clause` -- "reuse the EXACT SAME subject in the attached photo".
    Right for a mascot portrait, and the only thing that keeps the brand's own
    mascot recognisable across generated scenes.
  * `grounding_clause` -- "match this scene's subject and styling; it is not
    the mascot". Right for everything else.

Handing the identity clause to a photo of a shelf of products tells the model
to reproduce a subject that is not in the picture: it either hallucinates one
or corrupts the frame trying. `reference_clause` picks between the two from
the manifest's `shows_mascot` flag, in ONE place, so the three generators that
condition on the library (reels beats, the WP hero, the social hook image) can
never drift apart.

Nothing here may assume WHAT the mascot is. This is a multi-brand engine, and
these strings go straight into the prompt: an earlier version said "person and
dog" throughout, which told every brand without a dog that it had one. The
species now comes from the brand's own `site.mascot_kind` (see
`lib.crew.mascot`) or is simply not mentioned.
"""

from __future__ import annotations

from lib.crew.reference_library import ReferenceImage


def _mascot_phrase(mascot_name: str, mascot_kind: str) -> str:
    """The parenthetical that names the mascot, or `""` when nothing is known.

    Four cases, all grammatical, none assuming a species -- "Nalla, the
    brand's dog", "Rusty, the brand's delivery van", "the brand's cartoon
    fox", or nothing at all when the brand configured neither field.
    """
    name, kind = mascot_name.strip(), mascot_kind.strip()
    if name and kind:
        return f" -- the mascot is {name}, the brand's {kind}"
    if name:
        return f" -- the mascot is {name}"
    if kind:
        return f" -- the mascot is the brand's {kind}"
    return ""


def identity_clause(mascot_name: str, mascot_kind: str = "") -> str:
    """Subject-consistency instruction for a reference that DOES show the
    brand's persona and mascot.

    Moved verbatim out of `lib.crew.wp_image._style_suffix`, then out of
    `lib.crew.reference_library`, so every generator conditioning on a mascot
    photo phrases the constraint identically -- then rewritten to stop
    hardcoding a species into it.

    `mascot_kind` is the brand's own `site.mascot_kind` and is optional: with
    it the model is told what kind of thing to keep consistent, without it the
    attached photo is the only description, which is enough.
    """
    return (
        "A reference photo of the brand's own persona and mascot is attached -- "
        "reproduce the EXACT SAME subject shown in that photo (same appearance, same "
        f"distinguishing features{_mascot_phrase(mascot_name, mascot_kind)}), placed "
        "into this new scene. Do not substitute a different one. "
    )


def grounding_clause() -> str:
    """Scene-consistency instruction for a reference that does NOT show the
    brand's persona or mascot.

    Everything the attached image can honestly be used for -- subject matter,
    styling, lighting, composition -- with an explicit denial of the identity
    reading, because that is the failure mode: without it the model treats a
    stranger's pet in a stock photo as the brand's own and carries it into
    every later frame.

    Takes no mascot, by construction: this clause must not describe the mascot
    at all, only deny that the attached picture shows it.
    """
    return (
        "An image is attached as a visual reference for this scene -- match its subject "
        "matter, styling, lighting and composition, and if it shows a specific object, "
        "product, place or setting, that is what should appear. It is NOT a photo of "
        "the brand's persona or mascot: do not treat any person, animal or character in "
        "it as the brand's own, and do not carry their identity into the scene. If the "
        "scene calls for the brand's mascot, do not take its appearance from this "
        "reference. "
    )


def reference_clause(
    reference: ReferenceImage | None, mascot_name: str, mascot_kind: str = ""
) -> str:
    """The clause that matches whatever photo is actually attached.

    Two cases, one rule -- "say only what this picture supports":

    * a library image tagged `shows_mascot` -> `identity_clause`.
    * any other library image -> `grounding_clause`; it is a product, a place
      or a setting, and there is no mascot in it to reuse.

    `None` is the third, now-vestigial case. It used to mean "the caller fell
    back to the WP hero as the reference", which is exactly what "only
    uploaded photos may anchor a generated image" abolished: with no library
    match the reels and social generators no longer generate at all, so they
    never build a clause for a `None` reference. The WP-hero pipeline still
    passes one unconditionally, and `lib.crew.wp_image._style_suffix` drops it
    when there is no reference to describe. Kept TOTAL -- `grounding_clause`,
    never a raise -- so no caller can turn a missing reference into a crash,
    and so the answer stays the honest one if a `None` ever reaches a prompt.
    """
    if reference is not None and reference.shows_mascot:
        return identity_clause(mascot_name, mascot_kind)
    return grounding_clause()
