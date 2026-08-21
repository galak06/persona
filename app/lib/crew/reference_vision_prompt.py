"""What the vision tagger is actually asked -- the prompt, and its schema.

Split out of `lib.crew.reference_vision` (which was at its 300-line ceiling)
because the instruction stopped being a footnote to the HTTP call and became
the thing that decides whether the library is usable at all.

**Why the wording was inverted.** The first version told the model to "prefer
reusing a tag from the list over inventing a near-duplicate". With `general`
the only tag a fresh library has, that instruction reads as "put everything in
`general`" -- and it did: an operator's first ten photos (studio character
sheets, forest trails, a porch, products on a deck) all landed in one bucket.
A category holding every kind of scene cannot answer "give me the photo for a
forest beat": the resolver's pick inside it is arbitrary, so a product beat got
a portrait and a forest beat got a studio sheet. The mechanics were never
wrong; the tags were too coarse to select on.

So the bias now runs the other way. The model is asked for a SPECIFIC tag
derived from what is in front of it, reuse is conditioned on the existing tag
genuinely describing the same kind of scene (the anti-duplicate rule survives,
but it now guards `forest-trail` against `forest-path` rather than herding
everything into a catch-all), and `general` is named as a last resort.

**Two subjects, judged independently.** A brand has a mascot and a persona --
the person behind it (`site.brand_persona`) -- and reference photos routinely
show one, the other, both, or neither. Both flags are per image, because "the
mascot appears in this photo" is a property of the photo, not of its tag, and
because the identity clause a generator attaches downstream
(`lib.crew.reference_clauses`) has to name exactly the subjects that are
really in the frame.

Nothing here may guess what either subject IS. The mascot is described in the
brand's own words (`site.mascot_kind`) or in general terms; the persona is "the
person behind this brand", never gendered, never characterised.
"""

from __future__ import annotations

from typing import Any

#: Structured-output contract for the tagging call. `is_new_category` is
#: deliberately absent: novelty is computed by the caller from its own tag
#: list, never asked of a model that has every incentive to claim its
#: invention was already there.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "description": {"type": "STRING"},
        "category": {"type": "STRING"},
        "shows_mascot": {"type": "BOOLEAN"},
        "shows_persona": {"type": "BOOLEAN"},
    },
    "required": ["description", "category", "shows_mascot", "shows_persona"],
}


def mascot_terms(mascot_name: str, mascot_kind: str) -> tuple[str, str]:
    """(how to name the mascot, what a stand-in for it would be called).

    Species-free by construction: a brand that configured `site.mascot_kind`
    gets its own word used, and a brand that did not is asked about "the
    brand's own mascot" in the abstract. Nothing here may guess.
    """
    if mascot_name and mascot_kind:
        subject = f'the brand\'s own mascot (the specific {mascot_kind} named "{mascot_name}")'
    elif mascot_name:
        subject = f'the brand\'s own mascot (the specific one named "{mascot_name}")'
    elif mascot_kind:
        subject = f"the brand's own mascot (its own specific {mascot_kind})"
    else:
        subject = "the brand's own mascot (the specific one this brand uses)"
    return subject, (mascot_kind or "look-alike")


def persona_term(persona_name: str) -> str:
    """How to name the brand's persona -- the PERSON behind the brand.

    Named when `site.brand_persona` is configured and described only as "the
    person behind this brand" when it is not. No gender, no role, no
    appearance: the attached photo is the description, and anything this
    string adds would be the engine inventing a person for the brand.
    """
    if persona_name:
        return f'the brand\'s own persona (the specific person named "{persona_name}")'
    return "the brand's own persona (the specific person behind this brand)"


def build_prompt(labels: list[str], mascot_name: str, mascot_kind: str, persona_name: str) -> str:
    """Instruction half of the request -- the image is the other half."""
    mascot, stand_in = mascot_terms(mascot_name, mascot_kind)
    persona = persona_term(persona_name)
    options = ", ".join(f'"{label}"' for label in labels) if labels else "(no tags yet)"
    return (
        "You are tagging a photo for a brand's reference-image library. These photos "
        "ground generated imagery, so they show anything the brand shoots: its mascot "
        "and the person behind the brand, but also products, locations, settings, and "
        "style shots. Each generated image asks this library for the ONE tag that "
        "describes the scene it needs, so a tag is only useful if it says what the "
        "photo actually shows. "
        "Answer four questions about the attached image. "
        "(1) description: one short sentence saying what is actually in the image. "
        f"(2) shows_mascot: true ONLY if {mascot} actually appears in this image. It is "
        f"false for any other subject, false for a generic or stock {stand_in} that is "
        "not that specific one, and false for an image that shows nothing of the kind "
        "at all. "
        f"(3) shows_persona: true ONLY if {persona} actually appears in this image. It "
        "is false for anybody else, false for a stand-in or stock model who is not that "
        "specific person, and false for an image with nobody in it. Judge this "
        "INDEPENDENTLY of shows_mascot: an image may show both subjects, either one "
        "alone, or neither. "
        "(4) category: ONE SPECIFIC tag naming what this photo shows -- derive it from "
        "the image, naming the setting, subject or object actually in front of you. "
        'Examples of the right shape: "forest-trail", "studio-mascot", '
        '"persona-portrait", "products", "home-exterior". At most two words, lowercase, '
        "hyphenated. "
        f"These tags already exist: {options}. Reuse one EXACTLY as spelled ONLY if it "
        "genuinely describes this same kind of scene -- do not coin a near-duplicate of "
        'a tag that is already there ("forest-path" when "forest-trail" exists). If no '
        "existing tag describes this photo, propose your own specific one instead: a "
        "library of specific tags is the goal, and a new tag is the right answer "
        "whenever this is a different kind of scene. "
        'Use "general" ONLY as a last resort, for a photo that genuinely fits no '
        "describable category at all. It is the library's catch-all, so a photo filed "
        "there gets picked for scenes it does not match -- almost nothing belongs in it. "
        'Reply as JSON: {"description": "...", "category": "...", "shows_mascot": true, '
        '"shows_persona": false}.'
    )
