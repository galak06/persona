"""What to tell the image model about the reference photo it was handed.

Split out of `lib.crew.reference_library` (which was at the 300-line ceiling)
because this is no longer one constant string: it is a decision.

The library holds ANY reference image -- a finished dish, an ingredient, a
kitchen counter, a product, a location, a style plate -- not only portraits
of the brand's persona and mascot. Two different instructions follow from
that, and sending the wrong one actively degrades the generation:

  * `identity_clause` -- "reuse the EXACT person and dog in the attached
    photo". Right for a mascot portrait, and the only thing that keeps the
    brand's own dog recognisable across generated scenes.
  * `grounding_clause` -- "match this scene's subject and styling; it is not
    the mascot". Right for everything else.

Handing the identity clause to a photo of a bowl of food tells the model to
reproduce a person and a dog that are not in the picture: it either
hallucinates them or corrupts the frame trying. `reference_clause` picks
between the two from the manifest's `shows_mascot` flag, in ONE place, so
the three generators that condition on the library (reels beats, the WP hero,
the social hook image) can never drift apart.
"""

from __future__ import annotations

from lib.crew.reference_library import ReferenceImage


def identity_clause(mascot_name: str) -> str:
    """Subject-consistency instruction for a reference that DOES show the
    brand's persona and mascot.

    Moved verbatim out of `lib.crew.wp_image._style_suffix`, then out of
    `lib.crew.reference_library`, so every generator conditioning on a mascot
    photo phrases the constraint identically.
    """
    return (
        "A reference photo of the brand's real persona and mascot is attached -- use the "
        "EXACT SAME person and dog shown in that photo (same face, same dog coloring/"
        f"markings{f' -- the dog is {mascot_name}' if mascot_name else ''}), placed into "
        "this new scene. Do not invent a different person or dog. "
    )


def grounding_clause() -> str:
    """Scene-consistency instruction for a reference that does NOT show the
    brand's persona or mascot.

    Everything the attached image can honestly be used for -- subject matter,
    styling, lighting, composition -- with an explicit denial of the identity
    reading, because that is the failure mode: without it the model treats a
    stray dog in a stock kitchen photo as the brand's own and carries it into
    every later frame.
    """
    return (
        "An image is attached as a visual reference for this scene -- match its subject "
        "matter, styling, lighting and composition, and if it shows a specific dish, "
        "ingredient, product or place, that is what should appear. It is NOT a photo of "
        "the brand's persona or mascot: do not treat any animal or person in it as the "
        "brand's own, and do not carry their identity into the scene. If the scene calls "
        "for the brand's own dog, do not take that dog's appearance from this reference. "
    )


def reference_clause(reference: ReferenceImage | None, mascot_name: str) -> str:
    """The clause that matches whatever photo is actually attached.

    Two cases, one rule -- "say only what this picture supports":

    * a library image tagged `shows_mascot` -> `identity_clause`.
    * any other library image -> `grounding_clause`; it is a dish, a place or
      a product, and there is no mascot in it to reuse.

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
        return identity_clause(mascot_name)
    return grounding_clause()
