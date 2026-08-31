"""The photo that anchors the brand's own PERSONA -- the person behind it.

Sibling of `lib.crew.reference_mascot`, and it exists for the same reason at a
different subject. That module fixed the invented ANIMAL; nothing fixed the
invented PERSON.

Live on 2026-08-31: a social post whose brief opened "A person holds a dog's
mouth open to show its teeth" generated a cropped hand and a torso with the
face out of frame. The mascot was anchored and came back right; the human was
whoever the model felt like, because no photo told it otherwise. A brand whose
whole voice is one identifiable owner ships a stranger's hands.

The trigger is the BRIEF, not the library. Attaching a persona photo to every
generation would put a person into images that should have none -- a bowl of
kibble, a close-up of teeth -- so the anchor is offered only when the brief
actually calls for a human, and only when no photo already attached shows one.

Nothing here assumes who the persona is. `shows_persona` is the vision
tagger's verdict on the brand's own person (see `lib.crew.brand_identity`), and
a brand that keeps no such photo gets `None` -- today's behaviour exactly.
"""

from __future__ import annotations

import re
from pathlib import Path

from lib.crew.reference_library import (
    Candidate,
    ReferenceImage,
    best_tier,
    existing_images_by_category,
    pick,
)
from lib.observability import get_logger

logger = get_logger(__name__)

#: Words that mean a HUMAN is in the frame. Deliberately nouns and body parts
#: only -- verbs like "holding" or "feeding" describe an action a brief can
#: attribute to an off-camera subject, so they are not evidence of a person
#: being DEPICTED. Matched on word boundaries, so "handful" and "manage" do
#: not count as a hand or a man.
_PERSON_WORDS = (
    "person",
    "persons",
    "people",
    "human",
    "someone",
    "somebody",
    "owner",
    "owners",
    "man",
    "men",
    "woman",
    "women",
    "guy",
    "adult",
    "hand",
    "hands",
    "arm",
    "arms",
    "finger",
    "fingers",
    "lap",
    "knee",
    "knees",
    "shoulder",
    "shoulders",
)
_PERSON_RE = re.compile(r"\b(?:" + "|".join(_PERSON_WORDS) + r")\b", re.IGNORECASE)


def brief_calls_for_a_person(brief: str, persona_name: str = "") -> bool:
    """True if `brief` puts a human in the frame.

    `persona_name` counts too, and is checked case-insensitively as a whole
    phrase: a brief that names the brand's own person is asking for them by
    definition, and their name is the one "person word" this engine cannot
    know in advance.
    """
    if not brief:
        return False
    name = persona_name.strip()
    if name and name.lower() in brief.lower():
        return True
    return bool(_PERSON_RE.search(brief))


def _persona_candidates(brand_dir: Path) -> list[Candidate]:
    """Every photo in the library tagged `shows_persona`, from any category.

    Category is ignored for the same reason `reference_mascot` ignores it: the
    persona looks the same in a studio portrait and on a forest trail, and
    restricting the anchor to one tag would reintroduce the miss.
    """
    return [
        candidate
        for candidates in existing_images_by_category(brand_dir).values()
        for candidate in candidates
        if candidate[1].shows_persona
    ]


def any_persona_photo(brand_dir: Path, *, seed: str = "") -> ReferenceImage | None:
    """The best photo of the brand's persona the library holds, or `None`.

    Ranking matches `resolve_reference` and `any_mascot_photo` -- uploads
    before WP-media harvests, seeded pick inside the best tier only -- so the
    same `seed` picks the same face rather than rotating the brand's person
    between posts.
    """
    candidates = _persona_candidates(brand_dir)
    if not candidates:
        return None
    return pick(best_tier(candidates), seed)


def persona_anchor(
    brand_dir: Path,
    scene: ReferenceImage | None,
    attached: ReferenceImage | None = None,
    *,
    brief: str = "",
    persona_name: str = "",
    seed: str = "",
) -> ReferenceImage | None:
    """The photo that must ground the persona's appearance, or `None`.

    `None` in every case that is already correct, attach nothing extra:

    * the brief puts no human in the frame -- attaching a person would ADD one
      the post never asked for, which is worse than the problem being fixed.
    * `scene` is `None` -- there is no generation to add a reference to.
    * `scene` already shows the persona, or the mascot anchor `attached`
      alongside it does. One photo is doing the job; a second only gives the
      model two versions of the same face.
    * the library holds no `shows_persona` photo -- nothing may substitute.
    """
    if scene is None or not brief_calls_for_a_person(brief, persona_name):
        return None
    if scene.shows_persona or (attached is not None and attached.shows_persona):
        return None
    anchor = any_persona_photo(brand_dir, seed=seed)
    if anchor is None:
        logger.info("reference_library_no_persona_photo", scene_category=scene.category)
    return anchor
