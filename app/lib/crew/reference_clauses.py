"""What to tell the image model about the reference photo it was handed.

Split out of `lib.crew.reference_library` (which was at the 300-line ceiling)
because this is no longer one constant string: it is a decision.

The library holds ANY reference image -- a product, a location, a person, a
setting, a style plate -- not only portraits of the brand's persona and
mascot. Two different instructions follow from that, and sending the wrong one
actively degrades the generation:

  * `identity_clause` -- "reuse the EXACT SAME subject in the attached photo".
    Right for a photo of the brand's own mascot or persona, and the only thing
    that keeps either of them recognisable across generated scenes.
  * `grounding_clause` -- "match this scene's subject and styling; it is not
    the persona or the mascot". Right for everything else.

Handing the identity clause to a photo of a shelf of products tells the model
to reproduce a subject that is not in the picture: it either hallucinates one
or corrupts the frame trying. `reference_clause` picks between the two from the
manifest's flags, in ONE place, so the three generators that condition on the
library (reels beats, the WP hero, the social hook image) can never drift
apart.

`paired_reference_clause` is the same decision for a generator that attaches
TWO photos -- a scene reference plus the photo that anchors the mascot (see
`lib.crew.reference_mascot`). It is assembled from the two clauses above
rather than written afresh, for the same no-drift reason.

**Two subjects, four cases.** A photo may show the mascot, the persona (the
person behind the brand, `site.brand_persona`), both, or neither, and the
identity instruction has to name exactly the ones that are actually in frame.
Naming the mascot for a photo that only shows the person is the same failure as
naming either for a photo of a porch: an instruction to reproduce something the
model cannot see. So the clause is assembled from the flags rather than picked
from a shelf of three fixed strings.

Nothing here may assume WHAT those subjects are. This is a multi-brand engine,
and these strings go straight into the prompt: an earlier version said "person
and dog" throughout, which told every brand without a dog that it had one. The
species now comes from the brand's own `site.mascot_kind` (see
`lib.crew.brand_identity`) or is simply not mentioned, and the persona is never
described beyond the name the brand gave -- no gender, no role, no appearance.
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.crew.reference_library import ReferenceImage

#: Spelled-out counts for the opening sentence -- "THREE reference photos"
#: reads as an instruction, "3 reference photos" reads as data.
_NUMBER_WORDS = {2: "TWO", 3: "THREE", 4: "FOUR"}


def _mascot_aside(mascot_name: str, mascot_kind: str) -> str:
    """The fragment that names the mascot, or `""` when nothing is known.

    Four cases, all grammatical, none assuming a species -- "Nalla, the
    brand's dog", "Rusty, the brand's delivery van", "the brand's cartoon
    fox", or nothing at all when the brand configured neither field.
    """
    name, kind = mascot_name.strip(), mascot_kind.strip()
    if name and kind:
        return f"the mascot is {name}, the brand's {kind}"
    if name:
        return f"the mascot is {name}"
    if kind:
        return f"the mascot is the brand's {kind}"
    return ""


def _persona_aside(persona_name: str) -> str:
    """The fragment that names the persona, or `""` when the brand named none.

    Deliberately only the name: the attached photo describes the person, and
    anything this string added would be the engine inventing one.
    """
    name = persona_name.strip()
    return f"the persona is {name}" if name else ""


def _subjects(*, shows_mascot: bool, shows_persona: bool) -> tuple[str, str, str]:
    """(what the photo shows, "subject(s)", "a different one"/"different ones").

    Both flags false cannot reach here -- `reference_clause` sends that case
    to `grounding_clause` -- but `identity_clause` is public, so it degrades to
    the mascot reading rather than emitting a sentence with no subject in it.
    """
    if shows_mascot and shows_persona:
        return "the brand's own persona and mascot", "subjects", "different ones"
    if shows_persona:
        return "the brand's own persona", "subject", "a different one"
    return "the brand's own mascot", "subject", "a different one"


def identity_clause(
    mascot_name: str = "",
    mascot_kind: str = "",
    persona_name: str = "",
    *,
    shows_mascot: bool = True,
    shows_persona: bool = False,
) -> str:
    """Subject-consistency instruction for a reference that DOES show one of
    the brand's own subjects.

    Moved verbatim out of `lib.crew.wp_image._style_suffix`, then out of
    `lib.crew.reference_library`, so every generator conditioning on such a
    photo phrases the constraint identically -- then rewritten to stop
    hardcoding a species into it, and again to stop claiming both subjects are
    present when only one is.

    The two flags say which subjects are in the frame and default to the
    long-standing mascot-only reading, so `identity_clause(name, kind)` still
    means exactly what it always did. `mascot_kind` and `persona_name` are the
    brand's own `site.mascot_kind` / `site.brand_persona` and are optional:
    with them the model is told what to keep consistent, without them the
    attached photo is the only description, which is enough.
    """
    what, subject, others = _subjects(shows_mascot=shows_mascot, shows_persona=shows_persona)
    asides = [
        aside
        for aside in (
            _persona_aside(persona_name) if shows_persona else "",
            _mascot_aside(mascot_name, mascot_kind) if shows_mascot else "",
        )
        if aside
    ]
    named = f" -- {', and '.join(asides)}" if asides else ""
    return (
        f"A reference photo of {what} is attached -- reproduce the EXACT SAME "
        f"{subject} shown in that photo (same appearance, same distinguishing "
        f"features{named}), placed into this new scene. Do not substitute "
        f"{others}. "
    )


def grounding_clause() -> str:
    """Scene-consistency instruction for a reference that does NOT show the
    brand's persona or mascot.

    Everything the attached image can honestly be used for -- subject matter,
    styling, lighting, composition -- with an explicit denial of the identity
    reading, because that is the failure mode: without it the model treats a
    stranger's pet in a stock photo as the brand's own and carries it into
    every later frame.

    Takes no subjects, by construction: this clause must not describe the
    persona or the mascot at all, only deny that the attached picture shows
    either.
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
    reference: ReferenceImage | None,
    mascot_name: str,
    mascot_kind: str = "",
    persona_name: str = "",
) -> str:
    """The clause that matches whatever photo is actually attached.

    Four cases, one rule -- "say only what this picture supports":

    * tagged `shows_mascot` only -> reproduce the same mascot.
    * tagged `shows_persona` only -> reproduce the same person. Without this
      case a photo of the brand's own persona was treated as scenery, and the
      person in the generated image changed from post to post.
    * both -> one clause naming both, because that is what a photo of the two
      of them together can support.
    * neither -> `grounding_clause`; it is a product, a place or a setting, and
      there is nobody in it to reuse.

    `None` is the fifth, now-vestigial case. It used to mean "the caller fell
    back to the WP hero as the reference", which is exactly what "only
    uploaded photos may anchor a generated image" abolished: with no library
    match the reels and social generators no longer generate at all, so they
    never build a clause for a `None` reference. The WP-hero pipeline still
    passes one unconditionally, and `lib.crew.wp_image._style_suffix` drops it
    when there is no reference to describe. Kept TOTAL -- `grounding_clause`,
    never a raise -- so no caller can turn a missing reference into a crash,
    and so the answer stays the honest one if a `None` ever reaches a prompt.
    """
    if reference is None or not (reference.shows_mascot or reference.shows_persona):
        return grounding_clause()
    return identity_clause(
        mascot_name,
        mascot_kind,
        persona_name,
        shows_mascot=reference.shows_mascot,
        shows_persona=reference.shows_persona,
    )


def anchored_reference_clause(
    scene: ReferenceImage,
    anchors: Sequence[ReferenceImage],
    mascot_name: str = "",
    mascot_kind: str = "",
    persona_name: str = "",
) -> str:
    """One positional clause for a scene photo plus ANY number of anchors.

    `paired_reference_clause` covered exactly one anchor because exactly one
    subject was ever anchored: the mascot. A brief that puts a person in the
    frame needs a second (`lib.crew.reference_persona`), and the brand's own
    animal and its own person are different subjects that can arrive on
    different photos.

    Same contract as the two-photo version, extended: photos are numbered from
    1 in the order the caller attaches them, PHOTO 1 is always the scene, and
    each anchor is described by what IT shows. The caller MUST send the parts
    in this same order.
    """
    if not anchors:
        return reference_clause(scene, mascot_name, mascot_kind, persona_name)
    numbered = [f"PHOTO 1 -- {reference_clause(scene, mascot_name, mascot_kind, persona_name)}"]
    for position, anchor in enumerate(anchors, start=2):
        numbered.append(
            f"PHOTO {position} -- "
            + identity_clause(
                mascot_name,
                mascot_kind,
                persona_name,
                shows_mascot=anchor.shows_mascot,
                shows_persona=anchor.shows_persona,
            )
        )
    count = _NUMBER_WORDS.get(len(anchors) + 1, str(len(anchors) + 1).upper())
    # The single-anchor case names PHOTO 2 outright. "The later photo" is
    # strictly vaguer, and vaguer is worse in a prompt -- it only earns its
    # keep when there is more than one photo it could mean.
    single = len(anchors) == 1
    resolve = (
        "follow PHOTO 2 for that subject"
        if single
        else "follow the later photo for the subject it shows"
    )
    scope = "both" if single else "all of them"
    # PHOTO 1 is not always a pure setting photo. Once a persona anchor can
    # ride along with a scene that already shows the mascot, calling a studio
    # portrait of the mascot a reference for "setting, styling and props"
    # describes it wrongly -- and the model has to reconcile that against a
    # PHOTO 1 clause that just told it to reproduce the subject exactly.
    if scene.shows_mascot or scene.shows_persona:
        jobs = (
            "PHOTO 1 fixes the setting and the subject it shows; "
            + ("PHOTO 2 fixes" if single else "each later photo fixes")
            + " a subject PHOTO 1 does not"
        )
    else:
        jobs = (
            "PHOTO 1 fixes the setting, styling and props, "
            + ("PHOTO 2 fixes" if single else "the photos after it fix")
            + " what the brand's own "
            + ("subject looks" if single else "subjects look")
            + " like"
        )
    return (
        f"{count} reference photos are attached, in this order. "
        + "".join(numbered)
        + f"They have different jobs: {jobs}. Where they disagree, {resolve} "
        f"and PHOTO 1 for everything else, and follow the scene described "
        f"above over {scope}. "
    )


def paired_reference_clause(
    scene: ReferenceImage,
    anchor: ReferenceImage,
    mascot_name: str,
    mascot_kind: str = "",
    persona_name: str = "",
) -> str:
    """The one-anchor case of `anchored_reference_clause`, kept as a name.

    Every caller that attaches exactly one anchor reads better saying so, and
    this is the shape `lib.crew.reference_mascot` documents. It delegates
    rather than repeating the prose, so the two- and three-photo paths cannot
    drift into describing the same photo differently.
    """
    return anchored_reference_clause(scene, (anchor,), mascot_name, mascot_kind, persona_name)
